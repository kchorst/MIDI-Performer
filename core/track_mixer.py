from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil

import mido


@dataclass(frozen=True)
class TrackInfo:
    index: int
    name: str
    note_count: int
    channels: list[int]
    programs: list[int]

    @property
    def is_playable(self) -> bool:
        """True when the track contains actual note-on events.

        Standard MIDI files often use track 1 as a conductor/control track for
        tempo, meter, names, markers, and sometimes setup messages. That track is
        not a musical part, so the UI should not show it as an editable piano
        strip just because GM program 0 is the default fallback.
        """
        return int(self.note_count or 0) > 0

    @property
    def is_control_only(self) -> bool:
        return not self.is_playable

    def display_name(self) -> str:
        base = self.name or f"Track {self.index + 1}"
        channel_text = "ch " + ",".join(str(c + 1) for c in self.channels) if self.channels else "no channel"
        if self.is_control_only:
            return f"{base} · control track · {channel_text}"
        program_text = "program " + ",".join(str(p + 1) for p in self.programs) if self.programs else "default program"
        return f"{base} · {self.note_count} notes · {channel_text} · {program_text}"


def analyze_tracks(path: str | Path) -> list[TrackInfo]:
    midi = mido.MidiFile(path)
    results: list[TrackInfo] = []
    for index, track in enumerate(midi.tracks):
        name = ""
        notes = 0
        channels: set[int] = set()
        programs: set[int] = set()
        for msg in track:
            if msg.type == "track_name":
                name = str(msg.name)
            elif msg.type == "note_on" and getattr(msg, "velocity", 0) > 0:
                notes += 1
                channels.add(int(msg.channel))
            elif hasattr(msg, "channel") and msg.type in {"note_on", "note_off"}:
                channels.add(int(msg.channel))
            elif msg.type == "program_change":
                programs.add(int(msg.program))
                channels.add(int(msg.channel))
        results.append(TrackInfo(index, name, notes, sorted(channels), sorted(programs)))
    return results


def _active_int_map(values: dict[str, int | None] | None) -> dict[int, int]:
    out: dict[int, int] = {}
    for k, v in (values or {}).items():
        if v is None:
            continue
        out[int(k)] = int(v)
    return out


def apply_track_program_overrides(source_midi: str | Path, output_midi: str | Path, track_programs: dict[str, int | None]) -> Path:
    return apply_track_mixer_overrides(source_midi, output_midi, track_programs, {}, {}, {}, {})


def apply_track_mixer_overrides(
    source_midi: str | Path,
    output_midi: str | Path,
    track_programs: dict[str, int | None] | None,
    track_volumes: dict[str, int] | None,
    track_pans: dict[str, int] | None,
    track_solos: dict[str, bool] | None = None,
    track_mutes: dict[str, bool] | None = None,
    track_octaves: dict[str, int] | None = None,
) -> Path:
    """Apply per-track instrument, volume, pan/balance, octave, solo, and mute controls.

    track_programs are zero-based GM program numbers by track index. Program overrides also reset bank select to GM bank 0.
    track_volumes are MIDI CC7 values 0-127 by track index.
    track_pans are MIDI CC10 values 0-127 by track index, where 64 is center.
    track_octaves are integer octave shifts by track index. 0 means unchanged; +1 shifts notes up 12 semitones.
    track_solos / track_mutes are booleans by track index. If any solo is active, non-solo tracks are muted.
    Percussion channel 10 (zero-based channel 9) keeps its drum program; volume/pan/octave may still apply if the user explicitly shifts the track.
    """
    target = Path(output_midi)
    target.parent.mkdir(parents=True, exist_ok=True)
    programs = _active_int_map(track_programs)
    volumes = _active_int_map(track_volumes)
    pans = _active_int_map(track_pans)
    octaves = {k: max(-4, min(4, int(v))) for k, v in _active_int_map(track_octaves).items() if int(v) != 0}
    solos = {int(k): bool(v) for k, v in (track_solos or {}).items() if bool(v)}
    mutes = {int(k): bool(v) for k, v in (track_mutes or {}).items() if bool(v)}
    any_solo = bool(solos)
    if not programs and not volumes and not pans and not octaves and not solos and not mutes:
        source = Path(source_midi)
        if source.resolve() != target.resolve():
            shutil.copy2(source, target)
        return target

    midi = mido.MidiFile(source_midi)
    out = mido.MidiFile(type=midi.type, ticks_per_beat=midi.ticks_per_beat, charset=getattr(midi, "charset", "latin1"))

    for track_index, track in enumerate(midi.tracks):
        program = programs.get(track_index)
        volume = volumes.get(track_index)
        pan = pans.get(track_index)
        octave = octaves.get(track_index, 0)
        note_shift = int(octave) * 12
        muted = bool(mutes.get(track_index)) or (any_solo and not bool(solos.get(track_index)))
        needs_mix = program is not None or volume is not None or pan is not None or note_shift != 0 or muted
        if not needs_mix:
            out.tracks.append(mido.MidiTrack([msg.copy() for msg in track]))
            continue

        channels = set()
        for msg in track:
            if hasattr(msg, "channel") and msg.type in {"note_on", "note_off", "program_change", "control_change"}:
                channels.add(int(msg.channel))
        if not channels and program is not None:
            channels.add(0)

        new_track = mido.MidiTrack()
        inserted = False
        pending_time = 0
        for msg in track:
            # Muted tracks should not send notes or channel controls that might affect other tracks.
            # Carry their delta time forward so meta events keep their absolute position.
            if muted and hasattr(msg, "channel"):
                pending_time += msg.time
                continue
            # Remove prior program / volume / pan controls when this track has an override,
            # carrying their time forward so musical timing stays intact.
            if msg.type == "program_change" and program is not None and getattr(msg, "channel", 0) != 9:
                pending_time += msg.time
                continue
            if msg.type == "control_change" and getattr(msg, "control", None) in {0, 32} and program is not None:
                pending_time += msg.time
                continue
            if msg.type == "control_change" and getattr(msg, "control", None) == 7 and volume is not None:
                pending_time += msg.time
                continue
            if msg.type == "control_change" and getattr(msg, "control", None) == 10 and pan is not None:
                pending_time += msg.time
                continue

            if not inserted and not muted:
                first_time = pending_time
                for channel in sorted(channels):
                    if program is not None and channel != 9:
                        new_track.append(mido.Message("control_change", channel=channel, control=0, value=0, time=first_time))
                        first_time = 0
                        new_track.append(mido.Message("control_change", channel=channel, control=32, value=0, time=first_time))
                        new_track.append(mido.Message("program_change", channel=channel, program=max(0, min(127, program)), time=0))
                    if volume is not None:
                        new_track.append(mido.Message("control_change", channel=channel, control=7, value=max(0, min(127, volume)), time=first_time))
                        first_time = 0
                    if pan is not None:
                        new_track.append(mido.Message("control_change", channel=channel, control=10, value=max(0, min(127, pan)), time=first_time))
                        first_time = 0
                pending_time = 0
                inserted = True

            if note_shift and msg.type in {"note_on", "note_off"} and hasattr(msg, "note"):
                shifted = max(0, min(127, int(msg.note) + note_shift))
                new_track.append(msg.copy(note=shifted, time=msg.time + pending_time))
            else:
                new_track.append(msg.copy(time=msg.time + pending_time))
            pending_time = 0
        if not inserted and not muted:
            first_time = pending_time
            for channel in sorted(channels):
                if program is not None and channel != 9:
                    new_track.append(mido.Message("control_change", channel=channel, control=0, value=0, time=first_time))
                    first_time = 0
                    new_track.append(mido.Message("control_change", channel=channel, control=32, value=0, time=first_time))
                    new_track.append(mido.Message("program_change", channel=channel, program=max(0, min(127, program)), time=0))
                if volume is not None:
                    new_track.append(mido.Message("control_change", channel=channel, control=7, value=max(0, min(127, volume)), time=first_time))
                    first_time = 0
                if pan is not None:
                    new_track.append(mido.Message("control_change", channel=channel, control=10, value=max(0, min(127, pan)), time=first_time))
                    first_time = 0
        elif pending_time and len(new_track):
            new_track[-1].time += pending_time
        out.tracks.append(new_track)

    out.save(target)
    return target
