from __future__ import annotations

import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication

from app.main_window import compose_pdf_measure_strip
from core.pdf_measure_map import PdfMeasureBox, RenderedMeasureCell


def _measure_cell(index: int) -> RenderedMeasureCell:
    width = 60
    height = 140
    pixels = bytearray([255] * (width * height))

    def dark(x: int, y: int) -> None:
        if 0 <= x < width and 0 <= y < height:
            pixels[y * width + x] = 20

    # Four staves and a few note-like marks. The source is deliberately tall
    # and narrow, matching the failure mode seen in the Windows screenshots.
    for staff_top in (12, 42, 72, 102):
        for line in range(5):
            y = staff_top + line * 3
            for x in range(3, width - 3):
                dark(x, y)
        for note_x in (16, 30, 44):
            for yy in range(staff_top + 3, staff_top + 8):
                for xx in range(note_x - 2, note_x + 3):
                    dark(xx, yy)
    for y in range(8, height - 8):
        dark(2, y)
        dark(width - 3, y)

    return RenderedMeasureCell(
        samples=bytes(pixels),
        width=width,
        height=height,
        stride=width,
        measure=PdfMeasureBox(
            page_index=0,
            system_index=0,
            measure_index_in_system=index,
            global_measure_index=index,
            x0=index * width,
            y0=0,
            x1=(index + 1) * width,
            y1=height,
        ),
    )


def _ink_span(image, slot: int, *, slot_width: int = 240) -> int:
    x0 = slot * slot_width
    x1 = x0 + slot_width
    dark_columns: list[int] = []
    for x in range(x0, x1):
        found = False
        for y in range(image.height()):
            if image.pixelColor(x, y).red() < 150:
                found = True
                break
        if found:
            dark_columns.append(x)
    if not dark_columns:
        return 0
    return dark_columns[-1] - dark_columns[0] + 1


def test_four_and_eight_bar_pdf_views_are_readable_detail_views() -> None:
    app = QApplication.instance() or QApplication([])
    cells = [_measure_cell(index) for index in range(16)]

    four = compose_pdf_measure_strip(cells[:4], 4)
    eight = compose_pdf_measure_strip(cells[:8], 8)
    sixteen = compose_pdf_measure_strip(cells, 16)

    assert four.size().width() == 4 * 240
    assert eight.size().width() == 8 * 240
    assert sixteen.size().width() == 16 * 240
    assert four.height() == eight.height() == sixteen.height() == 160

    four_span = _ink_span(four, 0)
    eight_span = _ink_span(eight, 0)
    overview_span = _ink_span(sixteen, 0)

    # Detail windows enlarge the notation, while 16 bars remains a compact
    # overview. Every crop still stays within its exact MIDI-bar slot.
    assert four_span >= 90
    assert eight_span >= 90
    assert four_span >= int(overview_span * 1.35)
    assert eight_span >= int(overview_span * 1.30)
    for image, count in ((four, 4), (eight, 8), (sixteen, 16)):
        for slot in range(count):
            assert _ink_span(image, slot) > 0

    app.processEvents()


if __name__ == "__main__":
    test_four_and_eight_bar_pdf_views_are_readable_detail_views()
    print("alignment_pdf_detail_contract PASS")
