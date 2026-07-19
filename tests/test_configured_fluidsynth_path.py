from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.dependency_setup import find_fluidsynth
from core.fluidsynth_player import FluidSynthPlayer


def test_configured_fluidsynth_path_is_used_without_moving_file(tmp_path):
    executable = tmp_path / "fluidsynth.exe"
    executable.write_bytes(b"")
    player = FluidSynthPlayer(tmp_path, executable)
    assert Path(player.find_executable()) == executable
    assert Path(find_fluidsynth(tmp_path, executable)) == executable
