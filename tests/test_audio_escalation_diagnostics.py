from __future__ import annotations

from pathlib import Path
import tempfile
import time

import mido

from core.playback_engine import PlaybackEngine


class FakePlayer:
    app_root = Path(tempfile.gettempdir())


class DiagnosticBackend:
    def __init__(self) -> None:
        self.starts: list[dict] = []
        self.commands: list[tuple] = []
        self.preferred_driver = "default"

    def set_preferred_driver(self, driver):
        self.preferred_driver = str(driver or "default")

    def start(self, soundfont_path, gain=0.5, **kwargs):
        self.starts.append({"soundfont": str(soundfont_path), "gain": gain, **kwargs})

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


def make_midi(path: Path) -> None:
    midi = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    midi.tracks.append(track)
    track.append(mido.Message("program_change", channel=0, program=0, time=0))
    track.append(mido.Message("note_on", channel=0, note=60, velocity=90, time=0))
    track.append(mido.Message("note_off", channel=0, note=60, velocity=0, time=240))
    midi.save(path)


def test_preload_prepares_data_without_opening_audio_process() -> None:
    root = Path(tempfile.mkdtemp())
    midi = root / "test.mid"
    sf = root / "test.sf2"
    make_midi(midi)
    sf.write_bytes(b"placeholder")

    engine = PlaybackEngine(FakePlayer(), lambda source, label, key: source)
    backend = DiagnosticBackend()
    engine.backend = backend

    assert engine.preload_midi(midi, "Original", "original", sf, 0.5, 100)
    assert backend.starts == []


def test_play_opens_fresh_audio_route_with_force_restart() -> None:
    root = Path(tempfile.mkdtemp())
    midi = root / "test.mid"
    sf = root / "test.sf2"
    make_midi(midi)
    sf.write_bytes(b"placeholder")

    engine = PlaybackEngine(FakePlayer(), lambda source, label, key: source)
    backend = DiagnosticBackend()
    engine.backend = backend
    engine.set_audio_driver("wasapi")

    engine.play(midi, "Original", "original", sf, 0.5, 100)
    time.sleep(0.03)
    engine.stop()

    assert backend.preferred_driver == "wasapi"
    assert backend.starts
    assert backend.starts[0].get("force_restart") is True
    assert any(command[0] == "on" for command in backend.commands)
