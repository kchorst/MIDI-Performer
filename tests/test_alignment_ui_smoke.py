from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mido
from PySide6.QtWidgets import QApplication, QDialog

import core.pdf_measure_map as pdf_measure_map
from app.main_window import AlignmentPreviewWidget, MainWindow
from tests.test_alignment_pdf_fallback import make_score_pdf


def make_midi(path: Path) -> None:
    midi = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    midi.tracks.append(track)
    track.append(mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0))
    track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(100), time=0))
    for bar in range(20):
        track.append(mido.Message("note_on", channel=0, note=60 + bar % 8, velocity=64, time=0))
        track.append(mido.Message("note_off", channel=0, note=60 + bar % 8, velocity=0, time=1920))
    track.append(mido.MetaMessage("end_of_track", time=0))
    midi.save(path)


def test_alignment_is_available_before_score_analysis_and_qtpdf_renders() -> None:
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
        # Deliberately no score-analysis/OMR result: visual alignment must still open.
        window._refresh_all()
        window.show()
        app.processEvents()
        assert window.fix_alignment_btn.isEnabled()
        assert not window.fix_alignment_btn.isHidden()
        assert not window.alignment_row_widget.isHidden()
        assert window.fix_alignment_btn.text() == "Align…"
        assert "Alignment" not in [label.text() for label in window.alignment_row_widget.findChildren(type(window.score_source_label))]

        original_fitz = pdf_measure_map.fitz
        original_exec = QDialog.exec
        captured: dict[str, object] = {}

        def fake_exec(dialog: QDialog):
            dialog.show()
            app.processEvents()
            previews = dialog.findChildren(AlignmentPreviewWidget)
            assert len(previews) == 1
            preview = previews[0]
            captured["image"] = preview._score_image
            captured["status"] = preview._score_image_status
            captured["mapped"] = preview._score_mapped_bars
            captured["image_height"] = preview._score_image.height() if preview._score_image is not None else 0
            dialog.reject()
            return QDialog.DialogCode.Rejected

        pdf_measure_map.fitz = None
        QDialog.exec = fake_exec
        try:
            window.fix_score_alignment()
        finally:
            QDialog.exec = original_exec
            pdf_measure_map.fitz = original_fitz
            window.close()

        assert captured["image"] is not None
        assert int(captured["mapped"]) >= 4
        assert int(captured["image_height"]) == 160
        assert "QtPdf fallback" in str(captured["status"])
        assert "detail view" in str(captured["status"])
        assert "PyMuPDF is required" not in str(captured["status"])
    app.processEvents()


if __name__ == "__main__":
    test_alignment_is_available_before_score_analysis_and_qtpdf_renders()
    print("alignment_ui_smoke PASS")
