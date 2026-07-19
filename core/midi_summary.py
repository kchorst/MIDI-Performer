from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import mido


@dataclass(frozen=True)
class MidiSummary:
    tracks: int
    notes: int
    length_seconds: float
    ticks_per_beat: int
    tempo_bpm: float | None
    time_signature: str | None
    programs: list[int]

    def compact_text(self) -> str:
        length = _format_seconds(self.length_seconds)
        tempo = f"{self.tempo_bpm:.0f} BPM" if self.tempo_bpm else "tempo map"
        sig = self.time_signature or "time sig unknown"
        instruments = f"{len(self.programs)} program(s)" if self.programs else "original programs"
        return f"{self.tracks} tracks · {self.notes} notes · {length} · {tempo} · {sig} · {instruments}"


def _format_seconds(value: float) -> str:
    seconds = max(0, int(round(value)))
    minutes, sec = divmod(seconds, 60)
    if minutes:
        return f"{minutes}:{sec:02d}"
    return f"{sec}s"


def summarize_midi(path: str | Path) -> MidiSummary:
    midi = mido.MidiFile(path)
    note_count = 0
    tempos: list[int] = []
    time_signature: str | None = None
    programs: set[int] = set()
    for track in midi.tracks:
        for msg in track:
            if msg.type == "note_on" and getattr(msg, "velocity", 0) > 0:
                note_count += 1
            elif msg.type == "set_tempo":
                tempos.append(int(msg.tempo))
            elif msg.type == "time_signature" and not time_signature:
                time_signature = f"{msg.numerator}/{msg.denominator}"
            elif msg.type == "program_change":
                programs.add(int(msg.program))

    bpm: float | None = None
    if tempos:
        # Display the first explicit tempo; later versions can show tempo maps in more detail.
        bpm = float(mido.tempo2bpm(tempos[0]))

    return MidiSummary(
        tracks=len(midi.tracks),
        notes=note_count,
        length_seconds=float(getattr(midi, "length", 0.0) or 0.0),
        ticks_per_beat=int(midi.ticks_per_beat or 0),
        tempo_bpm=bpm,
        time_signature=time_signature,
        programs=sorted(programs),
    )
