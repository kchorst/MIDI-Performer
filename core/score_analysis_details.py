from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class ScoreAnalysisDetails:
    """User-facing score-analysis clarity for the PDF/OMR workflow.

    This is deliberately about *what the app understood and used*, not a full
    notation editor.  It gives the user a compact truth table before creating a
    score-guided take and flags when alignment needs human help.
    """

    dynamics_detected: int = 0
    dynamics_used: int = 0
    articulations_detected: int = 0
    articulations_used: int = 0
    tempo_detected: int = 0
    tempo_used_lightly: int = 0
    fermatas_detected: int = 0
    fermatas_applied: int = 0
    alignment_confidence: str = "Unknown"
    repair_needed: bool = False
    summary: str = ""
    detail: str = ""

    def compact_text(self) -> str:
        if self.summary:
            return self.summary
        return build_score_analysis_details(
            dynamics=[],
            articulations=[],
            tempo_words=[],
            fermatas=[],
            alignment_summary="",
        ).summary


def alignment_confidence_from_summary(summary: str | None, detail: str | None = None) -> tuple[str, bool]:
    text = f"{summary or ''} {detail or ''}".lower()
    if not text.strip():
        return "Unknown", False
    if any(token in text for token in ("weak match", "needs alignment", "needs omr", "no note-on", "unavailable", "not produce")):
        return "Low", True
    if any(token in text for token in ("possible match", "possibly compatible", "partial", "proportional", "omr note count unavailable", "pdf guide ready")):
        return "Medium", True
    if any(token in text for token in ("likely same piece", "likely compatible", "high confidence")):
        return "High", False
    if any(token in text for token in ("guide ready", "omr ready", "score guide ready")):
        # Ready but not bar-locked is still a cautious medium.
        return "Medium", True
    return "Medium", True


def build_score_analysis_details(
    *,
    dynamics: Iterable[str] | None = None,
    articulations: Iterable[str] | None = None,
    tempo_words: Iterable[str] | None = None,
    fermatas: Iterable[str] | int | None = None,
    alignment_summary: str | None = None,
    alignment_detail: str | None = None,
    guidance_enabled: bool = True,
) -> ScoreAnalysisDetails:
    dyn = [str(x).strip() for x in (dynamics or []) if str(x).strip()]
    arts = [str(x).strip() for x in (articulations or []) if str(x).strip()]
    tempos = [str(x).strip() for x in (tempo_words or []) if str(x).strip()]
    if isinstance(fermatas, int):
        fermata_count = max(0, int(fermatas))
    else:
        fermata_count = len([str(x).strip() for x in (fermatas or []) if str(x).strip()])
    confidence, repair_needed = alignment_confidence_from_summary(alignment_summary, alignment_detail)
    dyn_used = len(dyn) if guidance_enabled else 0
    arts_used = len(arts) if guidance_enabled else 0
    tempo_used = len(tempos) if guidance_enabled else 0
    parts = [
        f"Dyn {len(dyn)}/{dyn_used} used" if dyn else "Dyn 0",
        f"Artic {len(arts)}/{arts_used} used" if arts else "Artic 0",
        f"Tempo {len(tempos)} light" if tempos else "Tempo 0",
    ]
    if fermata_count:
        parts.append(f"Fermata {fermata_count} detected/not applied")
    parts.append(f"Align {confidence}")
    detail_parts = [
        f"Dynamics detected: {len(dyn)}; used: {dyn_used}.",
        f"Articulations detected: {len(arts)}; used: {arts_used}.",
        f"Tempo marks detected: {len(tempos)}; used lightly: {tempo_used}.",
        f"Fermatas detected: {fermata_count}; applied: 0 until exact note/chord alignment is available.",
        f"Alignment confidence: {confidence}.",
    ]
    if repair_needed:
        detail_parts.append("Fix Alignment can record a simple human shift/stretch repair without opening a full score editor.")
    return ScoreAnalysisDetails(
        dynamics_detected=len(dyn),
        dynamics_used=dyn_used,
        articulations_detected=len(arts),
        articulations_used=arts_used,
        tempo_detected=len(tempos),
        tempo_used_lightly=tempo_used,
        fermatas_detected=fermata_count,
        fermatas_applied=0,
        alignment_confidence=confidence,
        repair_needed=repair_needed,
        summary=" · ".join(parts),
        detail=" ".join(detail_parts),
    )


def _half_beat_text(steps: int, *, earlier_later: bool) -> str:
    if steps == 0:
        return "aligned" if earlier_later else "ending aligned"
    amount = abs(steps) / 2.0
    if amount.is_integer():
        amount_text = str(int(amount))
        unit = "beat" if amount == 1 else "beats"
    else:
        amount_text = f"{amount:.1f}"
        unit = "beats"
    if earlier_later:
        direction = "later" if steps > 0 else "earlier"
        return f"{amount_text} {unit} {direction}"
    direction = "longer" if steps > 0 else "shorter"
    return f"section {amount_text} {unit} {direction}"


def alignment_repair_summary(
    shift_percent: float = 0.0,
    stretch_percent: float = 100.0,
    *,
    shift_beats: float | None = None,
    length_beats: float | None = None,
) -> str:
    """Summarize a manual score/MIDI repair in musical terms.

    New projects store the exact beat offsets shown by the dialog.  The legacy
    percent arguments remain supported for older project files.
    """
    if shift_beats is not None or length_beats is not None:
        shift_steps = round(float(shift_beats or 0.0) * 2.0)
        length_steps = round(float(length_beats or 0.0) * 2.0)
    else:
        shift = float(shift_percent or 0.0)
        stretch = float(stretch_percent or 100.0)
        shift_steps = round(shift / 5)
        length_steps = round((stretch - 100) / 2)
    if shift_steps == 0 and length_steps == 0:
        return "No manual alignment repair"
    shift_text = "start aligned" if shift_steps == 0 else f"start {_half_beat_text(shift_steps, earlier_later=True)}"
    length_text = _half_beat_text(length_steps, earlier_later=False)
    return f"Manual alignment repair · {shift_text} · {length_text}"
