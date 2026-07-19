from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_SCALE_FACTOR", "1.25")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication, QDialog, QPushButton

from app.main_window import MainWindow
from tests.test_alignment_ui_smoke import make_midi
from tests.test_alignment_pdf_fallback import make_score_pdf


def test_alignment_buttons_fit_at_125_percent_scaling() -> None:
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        midi_path = root / "song.mid"
        pdf_path = root / "score.pdf"
        make_midi(midi_path)
        make_score_pdf(pdf_path)

        window = MainWindow()
        window.project.source_midi_path = str(midi_path)
        window.project.score_source_path = str(pdf_path)
        window.project.score_source_format = "pdf"
        window.project.score_focus_mode = "all"
        window.project.score_focus_page_count = 2
        window._refresh_all()
        window.show()
        app.processEvents()

        original_exec = QDialog.exec
        captured: dict[str, object] = {}

        def fake_exec(dialog: QDialog):
            dialog.show()
            app.processEvents()
            visible_buttons = {
                button.text(): button
                for button in dialog.findChildren(QPushButton)
                if button.isVisible()
            }
            required = ["Play Current Section", "Stop", "Cancel", "Accept Alignment"]
            captured["required_present"] = all(name in visible_buttons for name in required)
            captured["inside"] = all(
                dialog.rect().contains(button.mapTo(dialog, button.rect().bottomRight()))
                for name, button in visible_buttons.items()
                if name in required
            )
            dialog.reject()
            return QDialog.DialogCode.Rejected

        QDialog.exec = fake_exec
        try:
            window.fix_score_alignment()
        finally:
            QDialog.exec = original_exec
            window.close()

        assert captured.get("required_present") is True
        assert captured.get("inside") is True
    app.processEvents()


if __name__ == "__main__":
    test_alignment_buttons_fit_at_125_percent_scaling()
    print("alignment_scaling_contract PASS")
