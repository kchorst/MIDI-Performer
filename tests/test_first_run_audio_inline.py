from __future__ import annotations

import os
from pathlib import Path
import tempfile
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from app.main_window import FirstRunSetupDialog, MainWindow
from core.dependency_setup import ToolCheck


class _Backend:
    def available_driver_candidates(self):
        return ["default", "dsound"]

    def close(self):
        return None

    def diagnostic_snapshot(self):
        return {"log_path": "test.log"}


class _Engine:
    def __init__(self) -> None:
        self.backend = _Backend()
        self.calls: list[tuple[str, float, str | None]] = []

    def test_audio_tone(self, soundfont, volume_gain=0.7, driver=None):
        self.calls.append((str(soundfont), float(volume_gain), driver))
        return {"running": True}

    def audio_diagnostics(self):
        return self.backend.diagnostic_snapshot()

    def set_audio_driver(self, driver):
        self.driver = driver

    def close(self):
        return None


def test_first_run_test_plays_before_inline_feedback() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    engine = _Engine()
    window.playback_engine = engine
    window.stop_playback = lambda silent=True: None
    window._volume_gain = lambda: 0.8

    soundfont = Path(tempfile.gettempdir()) / "midi-performer-inline-test.sf2"
    soundfont.write_bytes(b"test")
    window.project.selected_soundfont_path = str(soundfont)

    dialog = FirstRunSetupDialog(window)
    dialog._audio_test_dependencies = lambda: {
        "fluidsynth": ToolCheck("FluidSynth", True, "fluidsynth.exe", True, "2.x", "ok"),
        "soundfont": ToolCheck("SoundFont", True, str(soundfont), True, "", "ok"),
    }

    dialog.start_audio_test()
    loop = QEventLoop()
    QTimer.singleShot(180, loop.quit)
    loop.exec()

    assert engine.calls
    assert engine.calls[0][2] == "default"
    assert "Did you hear" in dialog.audio_test_status.text()
    assert not dialog.heard_yes_button.isHidden()
    assert not dialog.heard_no_button.isHidden()
    assert not dialog.play_again_button.isHidden()

    dialog.close()
    window.close()
    app.processEvents()
