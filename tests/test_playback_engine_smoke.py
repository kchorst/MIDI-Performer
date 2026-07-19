from __future__ import annotations

from pathlib import Path
import tempfile
import time

import mido
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.playback_engine import PlaybackEngine, PlaybackState


class FakePlayer:
    app_root = Path(tempfile.gettempdir())


class FakeBackend:
    def __init__(self) -> None:
        self.commands = []

    def start(self, soundfont_path, gain=0.5):
        self.commands.append(("start", str(soundfont_path), gain))

    def all_notes_off(self):
        self.commands.append(("alloff",))

    def reset_controllers(self):
        self.commands.append(("reset",))

    def program_change(self, channel, program):
        self.commands.append(("prog", channel, program))

    def control_change(self, channel, control, value):
        self.commands.append(("cc", channel, control, value))

    def note_on(self, channel, note, velocity):
        self.commands.append(("on", channel, note, velocity))

    def note_off(self, channel, note):
        self.commands.append(("off", channel, note))

    def pitch_bend(self, channel, value):
        self.commands.append(("bend", channel, value))

    def set_gain(self, gain):
        self.commands.append(("gain", gain))

    def close(self):
        self.commands.append(("close",))


def make_test_midi(path: Path) -> None:
    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(120), time=0))
    track.append(mido.Message("program_change", channel=0, program=0, time=0))
    for note in [60, 62, 64, 65]:
        track.append(mido.Message("note_on", channel=0, note=note, velocity=64, time=0))
        track.append(mido.Message("note_off", channel=0, note=note, velocity=64, time=240))
    track.append(mido.MetaMessage("end_of_track", time=0))
    mid.save(path)


def test_live_engine_transport_contract():
    tmp = Path(tempfile.mkdtemp())
    midi = tmp / "engine_smoke.mid"
    make_test_midi(midi)
    engine = PlaybackEngine(FakePlayer(), lambda source, label, pct: source)
    fake = FakeBackend()
    engine.backend = fake

    snapshot = engine.play(midi, "As Is", "midi", tmp / "fake.sf2", 0.5, 100, start_percent=0)
    assert snapshot.state == PlaybackState.PLAYING
    time.sleep(0.04)
    assert any(command[0] == "on" for command in fake.commands)

    paused = engine.pause()
    assert paused.state == PlaybackState.PAUSED

    sought = engine.seek(50)
    assert 45 <= sought.percent <= 55

    tempo = engine.change_tempo(140)
    assert 45 <= tempo.percent <= 55

    engine.change_gain(0.7)
    assert any(command == ("gain", 0.7) for command in fake.commands)

    stopped = engine.stop()
    assert stopped.state == PlaybackState.STOPPED
    assert stopped.percent == 0

if __name__ == "__main__":
    test_live_engine_transport_contract()
    print("playback_engine_smoke PASS")
