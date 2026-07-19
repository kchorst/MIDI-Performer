from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import mido


@dataclass(frozen=True)
class MidiPreviewNote:
    start: float
    duration: float
    velocity: int
    pitch: int


@dataclass(frozen=True)
class MidiPreviewProfile:
    bars: int
    label: str
    notes: list[MidiPreviewNote] = field(default_factory=list)
    total_bars: float | None = None
    window_fraction: float = 1.0
    window_start_fraction: float = 0.0
    window_end_fraction: float = 1.0
    start_bar: int = 0
    end_bar: int = 0
    beats_per_bar: float = 4.0
    ticks_per_bar: int = 1920
    total_ticks: int = 0
    window_start_tick: int = 0
    window_end_tick: int = 0


@dataclass(frozen=True)
class ScoreMeasurePreview:
    number: str
    note_count: int
    dynamics: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ScorePreviewProfile:
    label: str
    measures: list[ScoreMeasurePreview] = field(default_factory=list)
    dynamics: list[str] = field(default_factory=list)
    position_mode: str = "mapped"


def _time_signature_and_ticks_per_bar(midi: mido.MidiFile) -> tuple[int, int, int]:
    numerator = 4
    denominator = 4
    for track in midi.tracks:
        for msg in track:
            if getattr(msg, "type", None) == "time_signature":
                numerator = int(getattr(msg, "numerator", 4) or 4)
                denominator = int(getattr(msg, "denominator", 4) or 4)
                return numerator, denominator, max(
                    1,
                    int(round(int(midi.ticks_per_beat or 480) * numerator * (4.0 / max(1, denominator)))),
                )
    return numerator, denominator, max(1, int(round(int(midi.ticks_per_beat or 480) * 4.0)))


def midi_preview_profile(
    path: str | Path | None,
    *,
    bars: int = 8,
    start_bar: int = 0,
    max_notes: int = 360,
) -> MidiPreviewProfile:
    requested_bars = max(1, int(bars or 8))
    requested_start = max(0, int(start_bar or 0))
    if not path:
        return MidiPreviewProfile(bars=requested_bars, start_bar=requested_start, end_bar=requested_start + requested_bars, label="No MIDI loaded")
    source = Path(path)
    if not source.exists():
        return MidiPreviewProfile(bars=requested_bars, start_bar=requested_start, end_bar=requested_start + requested_bars, label="MIDI not found")
    try:
        midi = mido.MidiFile(source)
    except Exception as exc:  # noqa: BLE001
        return MidiPreviewProfile(bars=requested_bars, start_bar=requested_start, end_bar=requested_start + requested_bars, label=f"MIDI preview unavailable: {exc}")

    tpb = int(getattr(midi, "ticks_per_beat", 480) or 480)
    numerator, denominator, ticks_per_bar = _time_signature_and_ticks_per_bar(midi)
    merged = mido.merge_tracks(midi.tracks)
    absolute_messages: list[tuple[int, Any]] = []
    now = 0
    for msg in merged:
        now += max(0, int(getattr(msg, "time", 0) or 0))
        absolute_messages.append((now, msg))
    total_ticks = max(1, now)
    total_bars = max(1.0, total_ticks / float(max(1, ticks_per_bar)))
    max_start = max(0, int(total_bars) - 1)
    start_bar_value = min(requested_start, max_start)
    start_tick = start_bar_value * ticks_per_bar
    end_tick = min(total_ticks, start_tick + requested_bars * ticks_per_bar)
    if end_tick <= start_tick:
        end_tick = min(total_ticks, start_tick + ticks_per_bar)
    window_ticks = max(1, end_tick - start_tick)

    active: dict[tuple[int, int], list[tuple[int, int]]] = {}
    note_intervals: list[tuple[int, int, int, int]] = []
    for tick, msg in absolute_messages:
        typ = getattr(msg, "type", "")
        if typ == "note_on" and int(getattr(msg, "velocity", 0) or 0) > 0:
            key = (int(getattr(msg, "channel", 0) or 0), int(getattr(msg, "note", 0) or 0))
            active.setdefault(key, []).append((tick, int(getattr(msg, "velocity", 0) or 0)))
        elif typ in {"note_off", "note_on"}:
            key = (int(getattr(msg, "channel", 0) or 0), int(getattr(msg, "note", 0) or 0))
            stack = active.get(key) or []
            if stack:
                note_start, velocity = stack.pop(0)
                note_intervals.append((note_start, max(note_start + 1, tick), key[1], velocity))
    for key, stack in active.items():
        for note_start, velocity in stack:
            note_intervals.append((note_start, total_ticks, key[1], velocity))

    notes: list[MidiPreviewNote] = []
    for note_start, note_end, pitch, velocity in note_intervals:
        if note_end <= start_tick or note_start >= end_tick:
            continue
        clipped_start = max(start_tick, note_start)
        clipped_end = min(end_tick, note_end)
        notes.append(
            MidiPreviewNote(
                start=max(0.0, min(1.0, (clipped_start - start_tick) / float(window_ticks))),
                duration=max(0.003, min(1.0, (clipped_end - clipped_start) / float(window_ticks))),
                velocity=max(1, min(127, int(velocity))),
                pitch=max(0, min(127, int(pitch))),
            )
        )

    notes.sort(key=lambda note: (note.start, note.pitch))
    if len(notes) > max_notes:
        step = len(notes) / float(max_notes)
        notes = [notes[min(len(notes) - 1, int(i * step))] for i in range(max_notes)]

    actual_end_bar = min(int(total_bars + 0.999999), start_bar_value + requested_bars)
    label = f"MIDI bars {start_bar_value + 1}–{max(start_bar_value + 1, actual_end_bar)} · {len(notes)} notes"
    return MidiPreviewProfile(
        bars=requested_bars,
        label=label,
        notes=notes,
        total_bars=total_bars,
        window_fraction=max(0.0001, min(1.0, window_ticks / float(total_ticks))),
        window_start_fraction=max(0.0, min(1.0, start_tick / float(total_ticks))),
        window_end_fraction=max(0.0, min(1.0, end_tick / float(total_ticks))),
        start_bar=start_bar_value,
        end_bar=max(start_bar_value + 1, actual_end_bar),
        beats_per_bar=max(1.0, numerator * (4.0 / max(1, denominator))),
        ticks_per_bar=ticks_per_bar,
        total_ticks=total_ticks,
        window_start_tick=start_tick,
        window_end_tick=end_tick,
    )


def midi_total_beats(path: str | Path | None) -> float:
    """Return the merged MIDI length in quarter-note beats."""
    if not path:
        return 0.0
    source = Path(path)
    if not source.exists():
        return 0.0
    try:
        midi = mido.MidiFile(source)
        tpb = int(getattr(midi, "ticks_per_beat", 480) or 480)
        merged = mido.merge_tracks(midi.tracks)
        total_ticks = sum(max(0, int(getattr(msg, "time", 0) or 0)) for msg in merged)
        return max(0.0, total_ticks / float(max(1, tpb)))
    except Exception:
        return 0.0


def midi_total_bars(path: str | Path | None) -> float:
    if not path:
        return 0.0
    source = Path(path)
    if not source.exists():
        return 0.0
    try:
        midi = mido.MidiFile(source)
        _num, _den, ticks_per_bar = _time_signature_and_ticks_per_bar(midi)
        merged = mido.merge_tracks(midi.tracks)
        total_ticks = sum(max(0, int(getattr(msg, "time", 0) or 0)) for msg in merged)
        return max(0.0, total_ticks / float(max(1, ticks_per_bar)))
    except Exception:
        return 0.0


def score_preview_from_reference(reference: Any | None, *, bars: int = 8) -> ScorePreviewProfile:
    if reference is None:
        return ScorePreviewProfile(label="No structured score data")
    measures = []
    all_dynamics: list[str] = []
    for measure in list(getattr(reference, "measures", []) or [])[: max(1, bars)]:
        dyns = [str(d) for d in (getattr(measure, "dynamics", []) or [])]
        all_dynamics.extend(dyns)
        measures.append(
            ScoreMeasurePreview(
                number=str(getattr(measure, "number", len(measures) + 1)),
                note_count=int(getattr(measure, "note_count", 0) or 0),
                dynamics=dyns,
            )
        )
    label = f"OMR score preview · {len(measures)} measures" if measures else "No structured score data"
    return ScorePreviewProfile(label=label, measures=measures, dynamics=all_dynamics, position_mode="mapped")
