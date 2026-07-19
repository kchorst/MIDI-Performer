from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from core.variation_engine import BASE_PROFILES, PerformerControls


@dataclass(frozen=True)
class PerformanceParameterReport:
    """Read-only explanation of how a generated performance was decided.

    These are the same interpretation dimensions that future score import and
    reference-performance matching should fill from stronger evidence. Today the
    values are MIDI-rule-based and user-control-based feedback.
    """

    source_mode: str
    dynamics: int
    timing_feel: int
    tempo_shaping: int
    articulation: int
    melody_emphasis: int
    accent_strength: int
    phrase_shaping: int
    repeat_variation: int
    humanization: int
    timing_label: str
    expression_percent: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def compact_text(self) -> str:
        return (
            f"Dynamics {self._word(self.dynamics)} · Timing {self.timing_label} "
            f"· Articulation {self._word(self.articulation)} · Phrase {self._word(self.phrase_shaping)}"
        )

    def detailed_text(self) -> str:
        return (
            f"Source: MIDI rules · Dynamics {self._word(self.dynamics)} · "
            f"Accent {self._word(self.accent_strength)} · Melody {self._word(self.melody_emphasis)} · "
            f"Phrase {self._word(self.phrase_shaping)} · Articulation {self._word(self.articulation)} · "
            f"Timing {self.timing_label} · Repeat Variation {self._word(self.repeat_variation)}"
        )

    @staticmethod
    def _word(value: int) -> str:
        if value <= 8:
            return "None"
        if value <= 25:
            return "Light"
        if value <= 50:
            return "Medium"
        if value <= 75:
            return "Strong"
        return "Very Strong"


def performance_parameter_report(level: str, expressiveness: int, timing_feel: str) -> PerformanceParameterReport:
    profile = BASE_PROFILES[level.lower()]
    controls = PerformerControls(expressiveness=expressiveness, timing_feel=timing_feel)

    def pct(value: float) -> int:
        return max(0, min(100, int(round(value * controls.expression_scale * 100))))

    timing_multiplier = controls.timing_scale
    swing_multiplier = controls.swing_scale
    timing_score = pct(profile.timing_strength * timing_multiplier)
    humanization = pct(profile.dynamic_strength * 0.18)
    return PerformanceParameterReport(
        source_mode="midi_rules",
        dynamics=pct(profile.dynamic_strength),
        timing_feel=timing_score,
        tempo_shaping=0,  # rule engine currently uses microtiming, not tempo-map/rubato shaping
        articulation=pct(profile.articulation_strength),
        melody_emphasis=pct(profile.melody_strength),
        accent_strength=pct(profile.dynamic_strength),
        phrase_shaping=pct(profile.phrase_strength),
        repeat_variation=pct(profile.repeat_variation_strength),
        humanization=humanization,
        timing_label=timing_feel if swing_multiplier else "Straight",
        expression_percent=int(expressiveness),
    )
