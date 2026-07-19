from __future__ import annotations

import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import QApplication

from app.main_window import AlignmentPreviewWidget
from core.alignment_preview_data import MidiPreviewNote, MidiPreviewProfile


def _longest_run(values: list[bool]) -> int:
    longest = 0
    current = 0
    for value in values:
        if value:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def test_alignment_visual_contract_is_readable() -> None:
    app = QApplication.instance() or QApplication([])
    widget = AlignmentPreviewWidget()
    widget.resize(1200, 350)
    widget.set_sources(True, True)
    widget.set_midi_context(
        MidiPreviewProfile(
            bars=4,
            label="MIDI bars 1–4 · 2 notes",
            notes=[
                MidiPreviewNote(start=0.10, duration=0.70, velocity=104, pitch=60),
                MidiPreviewNote(start=0.55, duration=0.35, velocity=82, pitch=72),
            ],
            total_bars=4.0,
            window_start_fraction=0.0,
            window_end_fraction=1.0,
            start_bar=0,
            end_bar=4,
            beats_per_bar=4.0,
        )
    )

    score = QImage(960, 160, QImage.Format_RGB32)
    score.fill(QColor("#ffffff"))
    score_painter = QPainter(score)
    score_painter.setPen(QPen(QColor("#111111"), 1))
    for bar in range(4):
        x0 = bar * 240
        for staff in range(4):
            y0 = 18 + staff * 32
            for line in range(5):
                score_painter.drawLine(x0 + 5, y0 + line * 4, x0 + 235, y0 + line * 4)
    score_painter.end()
    widget.set_score_image(score, "PDF page 1 · measures 1–4", mapped_bars=4, measure_mapped=True)

    waveform: list[int] = []
    amplitudes = [8, 20, 55, 95, 32, 12, 70, 28, 88, 18, 45, 100, 22, 65, 10, 84]
    for amplitude in amplitudes:
        waveform.extend([-amplitude, amplitude])
    widget.set_audio_context(
        [12, 25, 62, 98, 35, 18, 74, 30, 91, 22, 51, 100, 29, 69, 16, 87],
        [0, 0, 85, 0, 0, 0, 92, 0, 88, 0, 0, 95, 0, 80, 0, 90],
        [],
        label="Reference audio",
        waveform=waveform,
    )

    widget.show()
    app.processEvents()
    rendered = QImage(widget.size(), QImage.Format_ARGB32)
    rendered.fill(Qt.transparent)
    widget.render(rendered)

    # Long MIDI durations must remain narrow onset markers, not horizontal slabs.
    midi_runs: list[int] = []
    for y in range(160, 222):
        row: list[bool] = []
        for x in range(104, 1185):
            color = rendered.pixelColor(x, y)
            row.append(color.blue() > 175 and color.red() < 90 and color.green() < 145)
        midi_runs.append(_longest_run(row))
    assert max(midi_runs) <= 10

    # The waveform must occupy a useful vertical range so peaks and valleys are visible.
    waveform_y: list[int] = []
    for y in range(228, 292):
        for x in range(104, 1185):
            color = rendered.pixelColor(x, y)
            if color.blue() > 150 and color.green() > 90 and color.red() < 80:
                waveform_y.append(y)
                break
    assert waveform_y
    assert max(waveform_y) - min(waveform_y) >= 30

    widget.close()
    app.processEvents()


if __name__ == "__main__":
    test_alignment_visual_contract_is_readable()
    print("alignment_visual_contract PASS")
