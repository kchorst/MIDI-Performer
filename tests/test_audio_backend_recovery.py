from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from core.live_fluidsynth_backend import LiveFluidSynthBackend
from core.synth_backend_factory import create_synth_backend


def test_factory_uses_single_persistent_shell_backend() -> None:
    backend = create_synth_backend(None)
    assert isinstance(backend, LiveFluidSynthBackend)


def test_windows_command_matches_known_good_directsound_path() -> None:
    backend = LiveFluidSynthBackend(None)
    with patch("core.live_fluidsynth_backend.os.name", "nt"):
        command = backend._start_command(Path("fluidsynth.exe"), r"C:\\sounds\\piano.sf2", 0.5)
    assert command == [
        "fluidsynth.exe",
        "-n",
        "-g",
        "0.500",
        "-a",
        "dsound",
        r"C:\\sounds\\piano.sf2",
    ]
    assert "wasapi" not in command
    assert "-z" not in command
    assert "-c" not in command


def test_non_windows_command_uses_platform_default_driver() -> None:
    backend = LiveFluidSynthBackend(None)
    with patch("core.live_fluidsynth_backend.os.name", "posix"):
        command = backend._start_command("fluidsynth", "/tmp/piano.sf2", 0.5)
    assert command == ["fluidsynth", "-n", "-g", "0.500", "/tmp/piano.sf2"]


def test_prime_and_keepalive_are_intentionally_noops() -> None:
    backend = LiveFluidSynthBackend(None)
    assert backend.prime_audio() is None
    assert backend.start_keepalive() is None
    assert backend.stop_keepalive() is None
    assert backend.audio_ready_delay_seconds() == 0.0
