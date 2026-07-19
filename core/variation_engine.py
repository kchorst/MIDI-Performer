from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import random
from typing import Iterable

import mido

from core.score_guidance import ScoreGuidancePlan
from core.reference_audio import ReferenceAudioGuidancePlan


@dataclass(frozen=True)
class PerformerProfile:
    """A performer intent profile, not a one-off MIDI effect preset.

    The future score/reference engine can fill or override these values from score markings
    and performance analysis. For MVP, MIDI-only analysis supplies the context.
    """

    name: str
    dynamic_strength: float
    timing_strength: float
    articulation_strength: float
    phrase_strength: float
    melody_strength: float
    repeat_variation_strength: float


@dataclass(frozen=True)
class PerformerControls:
    expressiveness: int = 100
    timing_feel: str = "Natural"

    @property
    def expression_scale(self) -> float:
        return max(0.0, min(2.0, self.expressiveness / 100.0))

    @property
    def timing_scale(self) -> float:
        key = self.timing_feel.lower()
        if key == "straight":
            return 0.35
        if key == "lilt":
            return 1.35
        return 1.0

    @property
    def swing_scale(self) -> float:
        key = self.timing_feel.lower()
        if key == "straight":
            return 0.0
        if key == "lilt":
            return 1.4
        return 1.0


@dataclass(frozen=True)
class TrackAnalysis:
    median_note: float
    note_count: int
    low_note: int
    high_note: int
    likely_role: str


BASE_PROFILES: dict[str, PerformerProfile] = {
    "minimal": PerformerProfile(
        "minimal",
        dynamic_strength=0.17,
        timing_strength=0.10,
        articulation_strength=0.10,
        phrase_strength=0.12,
        melody_strength=0.11,
        repeat_variation_strength=0.03,
    ),
    "moderate": PerformerProfile(
        "moderate",
        dynamic_strength=0.34,
        timing_strength=0.23,
        articulation_strength=0.21,
        phrase_strength=0.28,
        melody_strength=0.22,
        repeat_variation_strength=0.11,
    ),
    "enhanced": PerformerProfile(
        "enhanced",
        dynamic_strength=0.56,
        timing_strength=0.38,
        articulation_strength=0.34,
        phrase_strength=0.46,
        melody_strength=0.34,
        repeat_variation_strength=0.20,
    ),
}


def _clamp_velocity(value: float) -> int:
    return max(1, min(127, int(round(value))))


def _is_note_on(msg: mido.Message) -> bool:
    return msg.type == "note_on" and getattr(msg, "velocity", 0) > 0


def _is_note_off(msg: mido.Message) -> bool:
    return msg.type == "note_off" or (msg.type == "note_on" and getattr(msg, "velocity", 0) == 0)


def _absolute_track(track: Iterable[mido.Message]) -> list[tuple[int, int, mido.Message]]:
    absolute = 0
    events: list[tuple[int, int, mido.Message]] = []
    for order, msg in enumerate(track):
        absolute += msg.time
        events.append((absolute, order, msg.copy(time=0)))
    return events


def _rebuild_track(events: list[tuple[int, int, mido.Message]]) -> mido.MidiTrack:
    events = [(max(0, int(tick)), order, msg) for tick, order, msg in events]
    events.sort(key=lambda item: (item[0], item[1]))
    rebuilt = mido.MidiTrack()
    previous = 0
    for absolute, _order, msg in events:
        delta = max(0, absolute - previous)
        rebuilt.append(msg.copy(time=delta))
        previous = absolute
    return rebuilt


def _analyze_track(events: list[tuple[int, int, mido.Message]]) -> TrackAnalysis:
    notes = [msg.note for _tick, _order, msg in events if _is_note_on(msg)]
    if not notes:
        return TrackAnalysis(60.0, 0, 60, 60, "neutral")
    notes.sort()
    low = notes[0]
    high = notes[-1]
    median = float(notes[len(notes) // 2])
    span = high - low
    if median >= 62 and span <= 32:
        role = "melody"
    elif median <= 52:
        role = "accompaniment"
    else:
        role = "mixed"
    return TrackAnalysis(median, len(notes), low, high, role)



def _count_note_on_events(midi: mido.MidiFile) -> int:
    return sum(1 for track in midi.tracks for msg in track if _is_note_on(msg))

def _beat_accent_multiplier(tick: int, ticks_per_beat: int, profile: PerformerProfile, controls: PerformerControls) -> float:
    beat = tick // max(1, ticks_per_beat)
    position = beat % 4
    amount = profile.dynamic_strength * controls.expression_scale
    if position == 0:
        return 1.0 + amount
    if position == 2:
        return 1.0 + (amount * 0.55)
    if position == 3:
        return 1.0 - (amount * 0.30)
    return 1.0 - (amount * 0.08)


def _phrase_multiplier(tick: int, ticks_per_beat: int, profile: PerformerProfile, controls: PerformerControls) -> float:
    # 8-beat phrase contour: stable start, grow, relax. Future score support should replace this guess.
    beat_in_phrase = (tick // max(1, ticks_per_beat)) % 8
    amount = profile.phrase_strength * controls.expression_scale
    if beat_in_phrase in (0, 1):
        return 1.0 - (amount * 0.28)
    if beat_in_phrase in (3, 4, 5):
        return 1.0 + (amount * 0.52)
    if beat_in_phrase in (6, 7):
        return 1.0 - (amount * 0.62)
    return 1.0


def _note_role_multiplier(note: int, analysis: TrackAnalysis, profile: PerformerProfile, controls: PerformerControls) -> float:
    amount = profile.melody_strength * controls.expression_scale
    if analysis.likely_role == "melody":
        if note >= analysis.median_note:
            return 1.0 + amount
        return 1.0 + (amount * 0.35)
    if analysis.likely_role == "accompaniment":
        if note <= analysis.median_note:
            return 1.0 - (amount * 0.62)
        return 1.0 - (amount * 0.25)
    if note >= analysis.median_note + 5:
        return 1.0 + amount
    if note <= analysis.median_note - 7:
        return 1.0 - (amount * 0.62)
    return 1.0


def _timing_offset(
    tick: int,
    ticks_per_beat: int,
    profile: PerformerProfile,
    controls: PerformerControls,
    rng: random.Random,
    score_guidance: ScoreGuidancePlan | None = None,
) -> int:
    beat_part = tick % ticks_per_beat
    eighth = ticks_per_beat / 2
    sixteenth = ticks_per_beat / 4
    score_timing = score_guidance.timing_multiplier() if score_guidance is not None else 1.0
    max_random = int(round(ticks_per_beat * 0.075 * profile.timing_strength * controls.expression_scale * controls.timing_scale * score_timing))
    swing = int(round(ticks_per_beat * 0.095 * profile.timing_strength * controls.expression_scale * controls.swing_scale * score_timing))

    if controls.expression_scale == 0:
        return 0

    # Keep obvious beat starts stable so the performer sounds intentional, not broken.
    if beat_part < ticks_per_beat * 0.05:
        return rng.randint(-max(1, max_random // 4), max(1, max_random // 4)) if max_random else 0

    offset = rng.randint(-max_random, max_random) if max_random else 0
    if abs(beat_part - eighth) <= sixteenth:
        offset += swing
    return offset


def _length_factor(note: int, analysis: TrackAnalysis, profile: PerformerProfile, controls: PerformerControls, rng: random.Random) -> float:
    amount = profile.articulation_strength * controls.expression_scale
    if amount == 0:
        return 1.0
    factor = 1.0 + rng.uniform(-amount * 0.55, amount * 0.55)
    if note >= analysis.median_note + 5:
        factor += amount * 0.35
    elif note <= analysis.median_note - 7:
        factor -= amount * 0.22
    return max(0.50, min(1.65, factor))


def _repeat_variation_multiplier(tick: int, ticks_per_beat: int, profile: PerformerProfile, controls: PerformerControls) -> float:
    # MIDI-only repeat guess: every 16 beats gets a slightly different energy. Score support will replace this.
    amount = profile.repeat_variation_strength * controls.expression_scale
    phrase_group = (tick // max(1, ticks_per_beat * 16)) % 4
    return [1.0, 1.0 + amount * 0.35, 1.0 - amount * 0.25, 1.0 + amount * 0.15][phrase_group]


def create_variation(
    source_midi: str | Path,
    output_midi: str | Path,
    level: str,
    seed: int | None = None,
    expressiveness: int = 100,
    timing_feel: str = "Natural",
    score_guidance: ScoreGuidancePlan | None = None,
    reference_audio_guidance: ReferenceAudioGuidancePlan | None = None,
) -> Path:
    """Create a profile-based MIDI performance variation.

    This is intentionally a performer-engine seam: MIDI-only analysis drives it now; later,
    score parsing and reference-performance analysis can replace the phrase/role/timing guesses
    without changing the UI flow.
    """
    level_key = level.lower()
    if level_key not in BASE_PROFILES:
        raise ValueError(f"Unknown variation level: {level}")

    profile = BASE_PROFILES[level_key]
    controls = PerformerControls(expressiveness=expressiveness, timing_feel=timing_feel)
    rng = random.Random(seed)
    midi = mido.MidiFile(source_midi)
    ticks_per_beat = midi.ticks_per_beat or 480
    total_note_on_count = _count_note_on_events(midi)
    global_note_index = 0
    out = mido.MidiFile(type=midi.type, ticks_per_beat=ticks_per_beat, charset=getattr(midi, "charset", "latin1"))

    for track in midi.tracks:
        events = _absolute_track(track)
        analysis = _analyze_track(events)
        adjusted: list[tuple[int, int, mido.Message]] = []
        active_notes: dict[tuple[int, int], list[tuple[int, int]]] = {}

        for absolute, order, msg in events:
            new_tick = absolute
            new_msg = msg.copy(time=0)
            channel = getattr(msg, "channel", 0)
            note = getattr(msg, "note", -1)

            if _is_note_on(msg):
                guidance_note_index = global_note_index
                multiplier = _beat_accent_multiplier(absolute, ticks_per_beat, profile, controls)
                multiplier *= _phrase_multiplier(absolute, ticks_per_beat, profile, controls)
                multiplier *= _note_role_multiplier(note, analysis, profile, controls)
                multiplier *= _repeat_variation_multiplier(absolute, ticks_per_beat, profile, controls)
                if score_guidance is not None:
                    multiplier *= score_guidance.dynamic_multiplier_for_midi_note(guidance_note_index, total_note_on_count)
                    multiplier *= score_guidance.articulation_velocity_multiplier_for_midi_note(guidance_note_index, total_note_on_count)
                    multiplier *= score_guidance.contour_multiplier_for_midi_note(guidance_note_index, total_note_on_count)
                human = 1.0 + rng.uniform(-profile.dynamic_strength * controls.expression_scale * 0.18, profile.dynamic_strength * controls.expression_scale * 0.18)
                candidate_velocity = msg.velocity * multiplier * human
                if score_guidance is not None:
                    candidate_velocity = score_guidance.score_led_velocity_for_midi_note(
                        candidate_velocity, guidance_note_index, total_note_on_count
                    )
                if reference_audio_guidance is not None:
                    candidate_velocity = reference_audio_guidance.audio_led_velocity_for_midi_note(
                        candidate_velocity, guidance_note_index, total_note_on_count
                    )
                new_msg.velocity = _clamp_velocity(candidate_velocity)
                global_note_index += 1
                base_offset = _timing_offset(absolute, ticks_per_beat, profile, controls, rng, score_guidance)
                if reference_audio_guidance is not None:
                    base_offset += reference_audio_guidance.audio_led_timing_offset_for_midi_note(
                        guidance_note_index, total_note_on_count, ticks_per_beat
                    )
                new_tick = max(0, absolute + base_offset)
                active_notes.setdefault((channel, note), []).append((len(adjusted), guidance_note_index))

            elif _is_note_off(msg):
                starts = active_notes.get((channel, note))
                if starts:
                    start_index, guidance_note_index = starts.pop(0)
                    start_tick, _start_order, _start_msg = adjusted[start_index]
                    duration = max(1, absolute - start_tick)
                    factor = _length_factor(note, analysis, profile, controls, rng)
                    if score_guidance is not None:
                        factor *= score_guidance.articulation_length_factor_for_midi_note(guidance_note_index, total_note_on_count)
                    if reference_audio_guidance is not None:
                        factor *= reference_audio_guidance.audio_led_length_factor_for_midi_note(
                            guidance_note_index, total_note_on_count
                        )
                    new_tick = max(start_tick + 1, int(round(start_tick + duration * factor)))

            adjusted.append((new_tick, order, new_msg))

        out.tracks.append(_rebuild_track(adjusted))

    target = Path(output_midi)
    target.parent.mkdir(parents=True, exist_ok=True)
    out.save(target)
    return target
