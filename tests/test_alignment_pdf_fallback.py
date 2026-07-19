from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QLineF
from PySide6.QtGui import QColor, QPainter, QPdfWriter, QPen
from PySide6.QtWidgets import QApplication

import core.pdf_measure_map as pdf_measure_map


def make_score_pdf(path: Path) -> None:
    writer = QPdfWriter(str(path))
    writer.setResolution(72)
    painter = QPainter(writer)
    painter.setPen(QPen(QColor("#000000"), 2))
    for page_index in range(2):
        if page_index:
            writer.newPage()
        for top in (120, 390):
            for staff_top in (top, top + 34, top + 68):
                for line in range(5):
                    y = staff_top + line * 5
                    painter.drawLine(QLineF(45, y, 550, y))
            for x in (50, 150, 250, 350, 450, 545):
                painter.drawLine(QLineF(x, top - 5, x, top + 92))
    painter.end()


def test_qtpdf_measure_mapping_without_pymupdf() -> None:
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as tmp:
        score = Path(tmp) / "score.pdf"
        make_score_pdf(score)
        original_fitz = pdf_measure_map.fitz
        pdf_measure_map.fitz = None
        try:
            measure_map = pdf_measure_map.build_pdf_measure_map(score)
            assert measure_map.renderer == "qtpdf"
            assert measure_map.page_count == 2
            assert measure_map.measure_count >= 12
            for bars in (4, 8, 16):
                cells, status = pdf_measure_map.render_pdf_measure_cells(
                    score,
                    measure_map,
                    start_measure=0,
                    bar_count=bars,
                )
                assert len(cells) == min(bars, measure_map.measure_count)
                assert "QtPdf fallback" in status
        finally:
            pdf_measure_map.fitz = original_fitz
    app.processEvents()


if __name__ == "__main__":
    test_qtpdf_measure_mapping_without_pymupdf()
    print("alignment_pdf_fallback PASS")
