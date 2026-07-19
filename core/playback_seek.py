from __future__ import annotations

from pathlib import Path
import shutil

import mido


def trim_midi_from_percent(source_midi: str | Path, output_midi: str | Path, start_percent: int) -> Path:
    """Create an approximate playback copy that starts at a percentage of the MIDI duration.

    This is a practical shuttle implementation for review/playback. It preserves track names,
    recent tempo/program/controller context where possible, and shifts later events to time 0+.
    """
    source = Path(source_midi)
    target = Path(output_midi)
    target.parent.mkdir(parents=True, exist_ok=True)
    pct = max(0, min(99, int(start_percent or 0)))
    if pct <= 0:
        if source.resolve() != target.resolve():
            shutil.copy2(source, target)
        return target

    midi = mido.MidiFile(source)
    total_ticks = 0
    for track in midi.tracks:
        total_ticks = max(total_ticks, sum(int(msg.time) for msg in track))
    if total_ticks <= 0:
        shutil.copy2(source, target)
        return target
    start_tick = int(total_ticks * pct / 100)

    out = mido.MidiFile(type=midi.type, ticks_per_beat=midi.ticks_per_beat, charset=getattr(midi, "charset", "latin1"))
    for track in midi.tracks:
        new_track = mido.MidiTrack()
        abs_tick = 0
        last_out_tick = 0
        context_msgs: dict[tuple, object] = {}
        meta_context: list[object] = []
        for msg in track:
            abs_tick += int(msg.time)
            if abs_tick < start_tick:
                if msg.type in {"track_name", "instrument_name"}:
                    meta_context.append(msg.copy(time=0))
                elif msg.type == "set_tempo":
                    context_msgs[("tempo",)] = msg.copy(time=0)
                elif msg.type == "time_signature":
                    context_msgs[("time_signature",)] = msg.copy(time=0)
                elif msg.type == "key_signature":
                    context_msgs[("key_signature",)] = msg.copy(time=0)
                elif msg.type == "program_change":
                    context_msgs[("program", int(msg.channel))] = msg.copy(time=0)
                elif msg.type == "control_change" and getattr(msg, "control", None) in {7, 10, 64}:
                    context_msgs[("cc", int(msg.channel), int(msg.control))] = msg.copy(time=0)
                continue
            if not new_track:
                # Keep names first, then latest context at time 0.
                seen_meta = set()
                for meta in meta_context:
                    key = (meta.type, getattr(meta, "name", ""))
                    if key not in seen_meta:
                        new_track.append(meta)
                        seen_meta.add(key)
                for key in sorted(context_msgs, key=str):
                    new_track.append(context_msgs[key])
            out_tick = abs_tick - start_tick
            delta = max(0, out_tick - last_out_tick)
            new_track.append(msg.copy(time=delta))
            last_out_tick = out_tick
        if not new_track:
            new_track.append(mido.MetaMessage("end_of_track", time=0))
        out.tracks.append(new_track)
    out.save(target)
    return target
