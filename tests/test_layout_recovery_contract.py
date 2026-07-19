from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication, QPushButton

from app.main_window import AlignmentPreviewWidget, ElidedLabel, MainWindow
from core.alignment_preview_data import MidiPreviewProfile
from tests.test_alignment_ui_smoke import make_midi
from tests.test_alignment_pdf_fallback import make_score_pdf


def test_takes_table_stays_inside_viewport() -> None:
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as tmp:
        midi_path = Path(tmp) / "song.mid"
        make_midi(midi_path)

        window = MainWindow()
        window.project.source_midi_path = str(midi_path)
        window._playback_ready = True
        window._playback_preparing = False
        long_note = (
            "Light · Expr None · Natural · T 100% · V 58% · Mix 2 · Inst: unchanged · "
            "OMR score guide (2 pages) Light · dynamics, articulations · "
            "Audio guide Light · dynamics/attack/pacing"
        )
        window.project.takes = [
            {"id": f"take_{index}", "name": str(index), "path": str(midi_path), "note": long_note}
            for index in range(1, 4)
        ]
        window._rebuild_takes_table()
        window.resize(1000, 700)
        window.show()
        app.processEvents()

        assert window.takes_scroll.horizontalScrollBar().maximum() == 0
        assert window.takes_container.width() <= window.takes_scroll.viewport().width()

        status_labels = window.takes_container.findChildren(ElidedLabel)
        assert status_labels
        assert any("…" in label.text() for label in status_labels if label.fullText() == long_note)

        viewport_width = window.takes_scroll.viewport().width()
        right_actions = [
            button
            for button in window.takes_container.findChildren(QPushButton)
            if button.text() in {"Play", "Stop", "Best", "More…"}
        ]
        assert right_actions
        assert all(
            button.mapTo(window.takes_scroll.viewport(), button.rect().bottomRight()).x() < viewport_width
            for button in right_actions
        )
        window.close()
    app.processEvents()


def test_align_shares_status_row_without_extra_band() -> None:
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
        window._refresh_all()
        window.show()
        app.processEvents()

        assert window.fix_alignment_btn.isVisible()
        assert window.fix_alignment_btn.parentWidget() is window.alignment_row_widget
        assert window.score_source_label.parentWidget() is window.alignment_row_widget
        assert not window.alignment_source_summary_label.isVisible()
        assert window.alignment_row_widget.height() <= 32
        window.close()
    app.processEvents()


def test_alignment_dialog_preview_and_cursor_cue_contract() -> None:
    app = QApplication.instance() or QApplication([])
    widget = AlignmentPreviewWidget()
    widget.set_sources(True, True)
    assert widget.minimumHeight() == 350
    assert widget.maximumHeight() == 350

    profile = MidiPreviewProfile(
        bars=4,
        label="MIDI bars 1–4",
        beats_per_bar=4.0,
        ticks_per_bar=1920,
        window_start_tick=0,
        window_end_tick=7680,
    )
    exact = 1920 / 7680
    cue = AlignmentPreviewWidget.visual_cursor_fraction(1920, 0, 7680, profile, playing=True)
    stopped = AlignmentPreviewWidget.visual_cursor_fraction(1920, 0, 7680, profile, playing=False)
    assert cue > exact
    assert cue - exact < 0.02
    assert stopped == exact
    widget.close()
    app.processEvents()


if __name__ == "__main__":
    test_takes_table_stays_inside_viewport()
    test_align_shares_status_row_without_extra_band()
    test_alignment_dialog_preview_and_cursor_cue_contract()
    print("layout_recovery_contract PASS")
