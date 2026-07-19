from __future__ import annotations

from pathlib import Path
import shutil

import mido


def apply_tempo_percent(source_midi: str | Path, output_midi: str | Path, tempo_percent: int) -> Path:
    """Write a MIDI copy with tempo scaled for playback/export.

    tempo_percent is musical speed: 100 preserves original, 120 plays 20% faster,
    80 plays 20% slower. It transforms set_tempo meta messages. If no tempo exists,
    it inserts an adjusted 120 BPM default tempo at the beginning.
    """
    source = Path(source_midi)
    target = Path(output_midi)
    target.parent.mkdir(parents=True, exist_ok=True)
    tempo_percent = max(25, min(250, int(tempo_percent or 100)))
    if tempo_percent == 100:
        if source.resolve() != target.resolve():
            shutil.copy2(source, target)
        return target

    midi = mido.MidiFile(source)
    out = mido.MidiFile(type=midi.type, ticks_per_beat=midi.ticks_per_beat, charset=getattr(midi, "charset", "latin1"))
    factor = 100.0 / tempo_percent
    saw_tempo = False

    for track_index, track in enumerate(midi.tracks):
        new_track = mido.MidiTrack()
        inserted_default = False
        for msg_index, msg in enumerate(track):
            if track_index == 0 and not inserted_default and msg_index == 0:
                # Only insert a default if the whole file had no tempo; decided after scan below.
                inserted_default = True
            if msg.type == "set_tempo":
                saw_tempo = True
                new_tempo = max(1, int(round(int(msg.tempo) * factor)))
                new_track.append(msg.copy(tempo=new_tempo))
            else:
                new_track.append(msg.copy())
        out.tracks.append(new_track)

    if not saw_tempo and out.tracks:
        default_tempo = int(round(mido.bpm2tempo(120) * factor))
        out.tracks[0].insert(0, mido.MetaMessage("set_tempo", tempo=default_tempo, time=0))

    out.save(target)
    return target
