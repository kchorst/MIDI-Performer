from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from bisect import bisect_left

from core.lilypond_intent import LilyPondMeasureIntent, LilyPondScoreIntent, parse_lilypond_file
from core.pdf_score_intelligence import analyze_pdf_score


DYNAMIC_BASE_MULTIPLIERS: dict[str, float] = {
    # Keep the fallback multiplier path extremely subtle. Score dynamics are
    # now mostly handled by a separate score-led velocity target below so a
    # bad original MIDI cannot exaggerate p/f contrast.
    "pppp": 0.90,
    "ppp": 0.92,
    "pp": 0.94,
    "p": 0.97,
    "mp": 0.99,
    "mf": 1.00,
    "f": 1.02,
    "ff": 1.04,
    "fff": 1.06,
    "ffff": 1.08,
    "sfz": 1.04,
    "fp": 1.01,
}

# Score-led target velocities use normal/unmarked as the center.  This is not
# speaker volume: it gently steers note velocities toward the score's printed
# dynamic level and compresses overly dramatic source-MIDI velocity swings.
DYNAMIC_TARGET_VELOCITIES: dict[str, int] = {
    "pppp": 68,
    "ppp": 70,
    "pp": 72,
    "p": 74,
    "mp": 76,
    "mf": 79,
    "f": 82,
    "ff": 84,
    "fff": 86,
    "ffff": 88,
    "sfz": 84,
    "fp": 78,
}

DYNAMIC_TARGET_BLEND_BY_STRENGTH: dict[str, float] = {
    "Light": 0.65,
    "Medium": 0.82,
    "Strong": 0.90,
}

DYNAMIC_TARGET_RESIDUAL = 0.04

# Score marks that influence attack/length.  Values are deliberately conservative:
# these are score hints layered on top of the existing MIDI performer, not a full
# replacement for score/MIDI alignment.
ARTICULATION_VELOCITY_MULTIPLIERS: dict[str, float] = {
    "accent": 1.12,
    "accented": 1.12,
    "marcato": 1.18,
    "sfz": 1.20,
    "staccato": 1.04,
    "tenuto": 1.03,
}

ARTICULATION_LENGTH_FACTORS: dict[str, float] = {
    "staccato": 0.50,
    "marcato": 0.82,
    "accent": 0.92,
    "accented": 0.92,
    "tenuto": 1.18,
    "legato": 1.22,
    "slur": 1.18,
    "phrase": 1.10,
}

CRESCENDO_WORDS = {"crescendo", "cresc", "cresc."}
DIMINUENDO_WORDS = {"diminuendo", "dim", "dim.", "decrescendo"}
RUBATO_WORDS = {"rubato", "rit", "rit.", "rall", "rall."}
ACCEL_WORDS = {"accelerando", "accel", "accel."}
A_TEMPO_WORDS = {"a tempo"}

STRENGTH_SCALES: dict[str, float] = {
    "Light": 0.18,
    "Medium": 0.28,
    "Strong": 0.38,
}


def _norm_word(word: str) -> str:
    return str(word or "").strip().lower()


def _blend(value: float, strength: float) -> float:
    return 1.0 + ((value - 1.0) * strength)


@dataclass(frozen=True)
class ScoreGuidancePlan:
    """Conservative bridge from score intent to the MIDI variation engine.

    Earlier project files only used score dynamics. The current workflow also
    lets a usable score or OMR result shape articulation length, attack accents,
    crescendo/diminuendo contours, and rubato-style timing strength.  It remains
    proportional rather than bar-by-bar exact alignment, so it is safe to disable
    and never mutates Original.
    """

    source_path: str
    source_format: str
    dynamics_enabled: bool
    strength: str
    summary: str
    detail: str
    measure_count: int
    score_note_count: int
    effective_dynamics_by_measure: tuple[str | None, ...]
    cumulative_note_ends: tuple[int, ...]
    articulation_sequence: tuple[str, ...] = ()
    tempo_words: tuple[str, ...] = ()
    alignment_shift_percent: float = 0.0
    alignment_stretch_percent: float = 100.0

    def _aligned_fraction(self, midi_note_index: int, midi_note_count: int) -> float:
        if midi_note_count <= 1:
            raw = 0.0
        else:
            raw = max(0.0, min(1.0, float(max(0, midi_note_index)) / float(max(1, midi_note_count - 1))))
        shift = max(-0.80, min(0.80, float(self.alignment_shift_percent or 0) / 100.0))
        stretch = max(0.20, min(1.80, float(self.alignment_stretch_percent or 100) / 100.0))
        # Positive shift means the guide starts later than the MIDI; for a fixed
        # MIDI note we therefore read an earlier guide position.
        return max(0.0, min(1.0, (raw - shift) / stretch))

    @property
    def strength_scale(self) -> float:
        return STRENGTH_SCALES.get(self.strength, STRENGTH_SCALES["Light"])

    @property
    def has_dynamics(self) -> bool:
        return bool(self.dynamics_enabled and any(self.effective_dynamics_by_measure))

    @property
    def has_articulations(self) -> bool:
        return bool(self.articulation_sequence)

    @property
    def has_tempo_guidance(self) -> bool:
        return bool(self.tempo_words)

    @property
    def is_active(self) -> bool:
        return bool(self.has_dynamics or self.has_articulations or self.has_tempo_guidance)

    @property
    def guidance_kinds(self) -> tuple[str, ...]:
        kinds: list[str] = []
        if self.has_dynamics:
            kinds.append("dynamics")
        if self.has_articulations:
            kinds.append("articulations")
        words = {_norm_word(w) for w in (*self.articulation_sequence, *self.tempo_words)}
        if words & (CRESCENDO_WORDS | DIMINUENDO_WORDS):
            kinds.append("phrasing")
        if words & (RUBATO_WORDS | ACCEL_WORDS | A_TEMPO_WORDS):
            kinds.append("tempo text")
        return tuple(dict.fromkeys(kinds))

    def dynamic_for_score_note_index(self, score_note_index: int) -> str | None:
        if not self.effective_dynamics_by_measure or self.score_note_count <= 0:
            return None
        bounded = max(0, min(self.score_note_count - 1, int(score_note_index)))
        measure_idx = bisect_left(self.cumulative_note_ends, bounded)
        measure_idx = max(0, min(len(self.effective_dynamics_by_measure) - 1, measure_idx))
        return self.effective_dynamics_by_measure[measure_idx]

    def dynamic_for_midi_note_index(self, midi_note_index: int, midi_note_count: int) -> str | None:
        if midi_note_count <= 0 or self.score_note_count <= 0:
            return None
        fraction = self._aligned_fraction(midi_note_index, midi_note_count)
        score_index = int(round(fraction * max(0, self.score_note_count - 1)))
        return self.dynamic_for_score_note_index(score_index)

    def dynamic_multiplier_for_midi_note(self, midi_note_index: int, midi_note_count: int) -> float:
        if not self.has_dynamics:
            return 1.0
        dynamic = self.dynamic_for_midi_note_index(midi_note_index, midi_note_count)
        if not dynamic:
            return 1.0
        base = DYNAMIC_BASE_MULTIPLIERS.get(dynamic, 1.0)
        return _blend(base, self.strength_scale)

    def score_led_velocity_for_midi_note(self, candidate_velocity: float, midi_note_index: int, midi_note_count: int) -> float:
        """Return a score-led velocity when an explicit dynamic is present.

        Multiplying a bad source MIDI by p/f marks can preserve or even exaggerate
        the source file's bad velocity shape. For explicit score dynamics, the current model
        instead blends the candidate velocity toward a restrained printed-dynamic
        target, keeping only a tiny residual from the MIDI/variation engine.
        """
        if not self.has_dynamics:
            return candidate_velocity
        dynamic = self.dynamic_for_midi_note_index(midi_note_index, midi_note_count)
        if not dynamic:
            return candidate_velocity
        target = float(DYNAMIC_TARGET_VELOCITIES.get(dynamic, 78))
        residual = (float(candidate_velocity) - 78.0) * DYNAMIC_TARGET_RESIDUAL
        score_led = target + residual
        blend = DYNAMIC_TARGET_BLEND_BY_STRENGTH.get(self.strength, DYNAMIC_TARGET_BLEND_BY_STRENGTH["Light"])
        return (float(candidate_velocity) * (1.0 - blend)) + (score_led * blend)

    def articulation_for_midi_note_index(self, midi_note_index: int, midi_note_count: int) -> str | None:
        if not self.articulation_sequence or midi_note_count <= 0:
            return None
        fraction = self._aligned_fraction(midi_note_index, midi_note_count)
        idx = int(round(fraction * max(0, len(self.articulation_sequence) - 1)))
        idx = max(0, min(len(self.articulation_sequence) - 1, idx))
        return self.articulation_sequence[idx]

    def articulation_velocity_multiplier_for_midi_note(self, midi_note_index: int, midi_note_count: int) -> float:
        articulation = _norm_word(self.articulation_for_midi_note_index(midi_note_index, midi_note_count))
        if not articulation:
            return 1.0
        base = ARTICULATION_VELOCITY_MULTIPLIERS.get(articulation, 1.0)
        return _blend(base, self.strength_scale)

    def articulation_length_factor_for_midi_note(self, midi_note_index: int, midi_note_count: int) -> float:
        articulation = _norm_word(self.articulation_for_midi_note_index(midi_note_index, midi_note_count))
        if not articulation:
            return 1.0
        base = ARTICULATION_LENGTH_FACTORS.get(articulation, 1.0)
        return _blend(base, self.strength_scale)

    def contour_multiplier_for_midi_note(self, midi_note_index: int, midi_note_count: int) -> float:
        # If explicit dynamics were detected, let them lead.  Generic crescendo/dim
        # words are useful mainly when the score exposes text but not a usable
        # ordered dynamic sequence.
        if self.has_dynamics:
            return 1.0
        words = {_norm_word(w) for w in (*self.articulation_sequence, *self.tempo_words)}
        if midi_note_count <= 1:
            return 1.0
        position = max(0.0, min(1.0, float(midi_note_index) / float(max(1, midi_note_count - 1))))
        amount = 0.18 * self.strength_scale
        has_cresc = bool(words & CRESCENDO_WORDS)
        has_dim = bool(words & DIMINUENDO_WORDS)
        if has_cresc and not has_dim:
            return 1.0 - amount + (amount * 2.0 * position)
        if has_dim and not has_cresc:
            return 1.0 + amount - (amount * 2.0 * position)
        return 1.0

    def timing_multiplier(self) -> float:
        words = {_norm_word(w) for w in self.tempo_words}
        if words & RUBATO_WORDS:
            return _blend(1.35, self.strength_scale)
        if words & ACCEL_WORDS:
            return _blend(1.15, self.strength_scale)
        if words & A_TEMPO_WORDS:
            return _blend(0.75, self.strength_scale)
        return 1.0


def create_lilypond_score_guidance(
    path: str | Path,
    *,
    dynamics_enabled: bool = True,
    strength: str = "Light",
) -> ScoreGuidancePlan:
    intent = parse_lilypond_file(path)
    return score_guidance_from_lilypond_intent(
        intent,
        dynamics_enabled=dynamics_enabled,
        strength=strength,
    )


def score_guidance_from_lilypond_intent(
    intent: LilyPondScoreIntent,
    *,
    dynamics_enabled: bool = True,
    strength: str = "Light",
) -> ScoreGuidancePlan:
    strength = strength if strength in STRENGTH_SCALES else "Light"
    effective = _effective_dynamics(intent.measures)
    cumulative = _cumulative_note_ends(intent.measures)
    dyn_count = len({d for d in effective if d})
    title = f"{intent.title} · " if intent.title else ""
    articulation_sequence = tuple(_measure_articulation(m) for m in intent.measures if _measure_articulation(m))
    tempo_words = tuple(intent.tempo_marks or [])
    kinds = _guidance_kinds_for_summary(effective, articulation_sequence, tempo_words, dynamics_enabled)
    summary = f"Score guide {strength} · {title}{', '.join(kinds) if kinds else 'no usable markings'} · {intent.measure_count} measure{'s' if intent.measure_count != 1 else ''}"
    if not kinds:
        detail = "No LilyPond dynamics, articulations, or tempo markings were found, so score guidance will not alter the take."
    else:
        parts: list[str] = []
        if dyn_count:
            used = ", ".join(d for d in sorted({d for d in effective if d}, key=_dynamic_sort_key))
            parts.append(f"dynamics ({used})")
        if articulation_sequence:
            parts.append("articulations (" + ", ".join(sorted(set(articulation_sequence))[:8]) + ")")
        if tempo_words:
            parts.append("tempo text (" + ", ".join(list(tempo_words)[:5]) + ")")
        detail = "Uses LilyPond " + "; ".join(parts) + " as proportional MIDI performance guidance. This is not full bar-by-bar alignment yet."
    return ScoreGuidancePlan(
        source_path=intent.path,
        source_format="LilyPond",
        dynamics_enabled=bool(dynamics_enabled),
        strength=strength,
        summary=summary,
        detail=detail,
        measure_count=intent.measure_count,
        score_note_count=max(0, int(intent.note_count)),
        effective_dynamics_by_measure=tuple(effective),
        cumulative_note_ends=tuple(cumulative),
        articulation_sequence=articulation_sequence,
        tempo_words=tempo_words,
    )


def score_guidance_from_dynamic_sequence(
    *,
    source_path: str,
    source_format: str,
    dynamic_sequence: list[str] | tuple[str, ...] | None,
    articulation_sequence: list[str] | tuple[str, ...] | None = None,
    tempo_words: list[str] | tuple[str, ...] | None = None,
    dynamics_enabled: bool = True,
    strength: str = "Light",
    summary_prefix: str = "Score guide",
    detail_suffix: str = "",
    alignment_shift_percent: float = 0.0,
    alignment_stretch_percent: float = 100.0,
) -> ScoreGuidancePlan:
    strength = strength if strength in STRENGTH_SCALES else "Light"
    sequence = [str(d).lower() for d in (dynamic_sequence or []) if str(d).lower() in DYNAMIC_BASE_MULTIPLIERS]
    articulations = [_normalize_articulation(a) for a in (articulation_sequence or [])]
    articulations = [a for a in articulations if a]
    tempos = [str(w).strip() for w in (tempo_words or []) if str(w).strip()]
    virtual_count = max(len(sequence), len(articulations), 1 if tempos else 0)
    measure_count = virtual_count
    effective = tuple(sequence)
    cumulative = tuple(range(len(sequence)))
    dyn_count = len(set(sequence))
    kinds = _guidance_kinds_for_summary(effective, tuple(articulations), tuple(tempos), dynamics_enabled)
    if kinds:
        summary = f"{summary_prefix} {strength} · " + ", ".join(kinds)
        details: list[str] = []
        if sequence:
            used = ", ".join(d for d in sorted(set(sequence), key=_dynamic_sort_key))
            details.append(f"dynamics ({used})")
        if articulations:
            details.append("articulations (" + ", ".join(sorted(set(articulations))[:8]) + ")")
        if tempos:
            details.append("tempo text (" + ", ".join(tempos[:5]) + ")")
        detail = "Uses detected score " + "; ".join(details) + " as proportional MIDI performance guidance."
        if detail_suffix:
            detail += " " + detail_suffix
    elif not dynamics_enabled:
        summary = f"{summary_prefix} present but disabled"
        detail = "Score guidance is present but disabled."
    else:
        summary = f"{summary_prefix} unavailable"
        detail = "No usable dynamics, articulations, or tempo text were detected."
    return ScoreGuidancePlan(
        source_path=str(source_path),
        source_format=source_format,
        dynamics_enabled=bool(dynamics_enabled),
        strength=strength,
        summary=summary,
        detail=detail,
        measure_count=measure_count,
        score_note_count=virtual_count,
        effective_dynamics_by_measure=effective,
        cumulative_note_ends=cumulative,
        articulation_sequence=tuple(articulations),
        tempo_words=tuple(tempos),
        alignment_shift_percent=float(alignment_shift_percent or 0.0),
        alignment_stretch_percent=float(alignment_stretch_percent or 100.0),
    )


def create_pdf_score_guidance(
    path: str | Path,
    *,
    dynamics_enabled: bool = True,
    strength: str = "Light",
    page_range: tuple[int, int] | None = None,
    alignment_shift_percent: float = 0.0,
    alignment_stretch_percent: float = 100.0,
) -> ScoreGuidancePlan:
    analysis = analyze_pdf_score(path, page_range=page_range)
    suffix = "This is embedded-text guidance unless OMR output is available; it is not full visual note alignment yet."
    return score_guidance_from_dynamic_sequence(
        source_path=analysis.path,
        source_format="PDF",
        dynamic_sequence=analysis.dynamic_sequence,
        articulation_sequence=analysis.articulation_words,
        tempo_words=analysis.tempo_words,
        dynamics_enabled=dynamics_enabled,
        strength=strength,
        summary_prefix=f"PDF score guide ({analysis.analyzed_pages_label})",
        detail_suffix=suffix,
        alignment_shift_percent=float(alignment_shift_percent or 0.0),
        alignment_stretch_percent=float(alignment_stretch_percent or 100.0),
    )


def _effective_dynamics(measures: list[LilyPondMeasureIntent]) -> list[str | None]:
    current: str | None = None
    effective: list[str | None] = []
    for measure in measures:
        if measure.dynamics:
            current = measure.dynamics[-1]
        effective.append(current)
    return effective


def _cumulative_note_ends(measures: list[LilyPondMeasureIntent]) -> list[int]:
    cumulative: list[int] = []
    total = 0
    for measure in measures:
        total += max(0, int(measure.note_count))
        cumulative.append(max(0, total - 1))
    return cumulative


def _measure_articulation(measure: LilyPondMeasureIntent) -> str | None:
    for preferred in ("accent", "marcato", "staccato", "tenuto", "legato", "slur", "phrase"):
        if preferred in measure.articulations:
            return preferred
    return measure.articulations[0] if measure.articulations else None


def _normalize_articulation(word: str) -> str:
    word = _norm_word(word).rstrip(".")
    aliases = {
        "accented": "accent",
        "cresc": "crescendo",
        "dim": "diminuendo",
        "rall": "rall",
        "rit": "rit",
        "accel": "accelerando",
    }
    return aliases.get(word, word)


def _guidance_kinds_for_summary(
    dynamics: tuple[str | None, ...] | list[str | None],
    articulations: tuple[str, ...],
    tempo_words: tuple[str, ...],
    dynamics_enabled: bool,
) -> list[str]:
    kinds: list[str] = []
    if dynamics_enabled and any(dynamics):
        kinds.append("dynamics")
    if articulations:
        kinds.append("articulations")
    words = {_norm_word(w) for w in (*articulations, *tempo_words)}
    if words & (CRESCENDO_WORDS | DIMINUENDO_WORDS):
        kinds.append("phrasing")
    if words & (RUBATO_WORDS | ACCEL_WORDS | A_TEMPO_WORDS):
        kinds.append("tempo text")
    return list(dict.fromkeys(kinds))


def _dynamic_sort_key(dynamic: str) -> int:
    order = list(DYNAMIC_BASE_MULTIPLIERS.keys())
    try:
        return order.index(dynamic)
    except ValueError:
        return len(order)
