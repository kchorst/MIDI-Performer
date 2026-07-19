from pathlib import Path
import tempfile
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.dependency_setup import find_fluidsynth, find_soundfont, check_soundfont
from core.omr_bridge import locate_omr_engine


def test_manual_and_auto_dependency_paths():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        fs = root / "existing" / "fluidsynth.exe"
        fs.parent.mkdir(parents=True)
        fs.write_bytes(b"MZ")
        assert find_fluidsynth(root, fs) == str(fs)

        sf_dir = root / "my soundfonts"
        sf_dir.mkdir()
        sf = sf_dir / "Piano.sf2"
        sf.write_bytes(b"RIFF" + b"0" * 2048)
        assert find_soundfont(root, None, sf_dir) == str(sf)
        assert check_soundfont(root, sf, sf_dir).confirmed

        aud = root / "Audiveris" / "bin" / "Audiveris.exe"
        aud.parent.mkdir(parents=True)
        aud.write_bytes(b"MZ")
        status = locate_omr_engine(aud.parent.parent)
        assert status.available
        assert str(aud) in status.command
