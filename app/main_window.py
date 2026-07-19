from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QTimer, QUrl, QSize, QRect, QEventLoop, QPointF
from PySide6.QtGui import QAction, QDesktopServices, QColor, QPainter, QPen, QBrush, QImage, QPainterPath
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QInputDialog,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QMenu,
    QPushButton,
    QProgressBar,
    QProgressDialog,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QSpacerItem,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.app_settings import AppSettings
from core.fluidsynth_player import FluidSynthError, FluidSynthPlayer
from core.instrument_override import GM_PROGRAM_NAMES, gm_choice_for_program, program_for_gm_choice
from core.midi_summary import summarize_midi
from core.performance_metadata import performance_parameter_report
from core.playback_engine import PlaybackEngine, PlaybackState
from core.project_model import MidiPerformerProject
from core.reference_audio import read_reference_audio, create_reference_audio_guidance
from core.score_alignment import compare_lilypond_to_midi, compare_pdf_to_midi, compare_score_reference_to_midi
from core.score_intake import read_score_source, read_musicxml, score_reference_dynamic_sequence, score_reference_articulation_sequence, score_reference_tempo_words, score_reference_fermatas, score_reference_summary
from core.score_guidance import create_lilypond_score_guidance, create_pdf_score_guidance, score_guidance_from_dynamic_sequence
from core.score_analysis_details import build_score_analysis_details, alignment_repair_summary
from core.pdf_score_intelligence import analyze_pdf_score, export_pdf_page_range, normalize_page_range, page_range_label
from core.omr_bridge import locate_omr_engine, run_pdf_omr
from core.alignment_preview_data import midi_preview_profile, midi_total_beats, midi_total_bars, score_preview_from_reference, MidiPreviewProfile, ScorePreviewProfile
from core.pdf_alignment_preview import detect_system_barlines, mapped_measure_slices
from core.pdf_measure_map import build_pdf_measure_map, render_pdf_measure_cells, PdfMeasureMap, RenderedMeasureCell
from core.dependency_setup import check_dependencies, install_audiveris_with_winget, find_ffmpeg, check_fluidsynth, check_audiveris, check_soundfont, find_soundfont
from core.audio_bootstrap import install_audio_dependencies, install_fluidsynth, install_recommended_soundfont
from core.lilypond_intent import parse_lilypond_file
from core.track_mixer import analyze_tracks, apply_track_mixer_overrides
from core.take_rendering import apply_master_volume_percent, audio_gain_for_volume_percent, normalize_volume_percent, volume_chain_summary
from core.tempo_transform import apply_tempo_percent
from core.variation_engine import create_variation
from core.version import APP_NAME, APP_VERSION_DISPLAY, TAGLINE, GUIDING_PRINCIPLE


APP_TITLE = APP_NAME
LEVELS: list[tuple[str, str]] = [("minimal", "Light"), ("moderate", "Medium"), ("enhanced", "Enhanced")]
LEVEL_LABELS = dict(LEVELS)
EXPRESSION_LEVELS: list[tuple[str, int]] = [("None", 0), ("Light", 50), ("Medium", 100), ("Strong", 150), ("Very Strong", 200)]
TIMING_FEELS = ["Straight", "Natural", "Lilt", "Laid Back"]

LEVEL_TOOLTIPS = {
    "minimal": "Light: subtle timing and velocity changes; safest first audition.",
    "moderate": "Medium: clearer phrasing and musical shaping.",
    "enhanced": "Enhanced: strongest interpretation; most noticeable performance change.",
}
EXPRESSION_TOOLTIPS = {
    "None": "No added dynamic expression beyond the source MIDI.",
    "Light": "Small velocity shaping; keeps the MIDI close to original.",
    "Medium": "Balanced accents and phrase shaping.",
    "Strong": "More obvious accents and musical emphasis.",
    "Very Strong": "Maximum dynamic contrast; useful for dramatic auditions.",
}
TIMING_TOOLTIPS = {
    "Straight": "Keeps timing close to the original grid.",
    "Natural": "Adds small human timing movement.",
    "Lilt": "Adds a light lifted/swing-like feel where appropriate.",
    "Laid Back": "Places notes slightly behind the beat for a relaxed feel.",
}


def compose_pdf_measure_strip(
    cells: list[RenderedMeasureCell],
    bar_count: int,
    *,
    cell_width: int = 240,
    target_height: int = 160,
) -> QImage:
    """Compose equal-width PDF measure slots for the shared MIDI ruler.

    Four- and eight-bar windows are inspection views.  Their notation is given a
    modest horizontal readability boost while remaining centered inside the
    exact MIDI-bar slot.  Sixteen bars intentionally stay at natural proportions
    because that window is an overview rather than a note-by-note matching view.
    """
    cell_width = max(80, int(cell_width))
    target_height = max(80, int(target_height))
    bars = max(1, int(bar_count or len(cells) or 1))
    strip = QImage(cell_width * max(1, len(cells)), target_height, QImage.Format_RGB32)
    strip.fill(QColor("#ffffff"))
    painter = QPainter(strip)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

    if bars <= 4:
        horizontal_gain, min_fraction, max_fraction = 1.65, 0.40, 0.58
    elif bars <= 8:
        horizontal_gain, min_fraction, max_fraction = 1.55, 0.40, 0.60
    else:
        horizontal_gain, min_fraction, max_fraction = 1.00, 0.0, 1.0

    for index, cell in enumerate(cells):
        source_image = QImage(
            cell.samples,
            cell.width,
            cell.height,
            cell.stride,
            QImage.Format_Grayscale8,
        ).copy()
        fitted = source_image.scaled(
            cell_width - 8,
            target_height - 6,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        if bars <= 8 and not fitted.isNull():
            natural_fraction = fitted.width() / float(cell_width)
            target_fraction = max(min_fraction, min(max_fraction, natural_fraction * horizontal_gain))
            readable_width = max(1, min(cell_width - 8, int(round(cell_width * target_fraction))))
            if readable_width > fitted.width():
                fitted = fitted.scaled(
                    readable_width,
                    fitted.height(),
                    Qt.IgnoreAspectRatio,
                    Qt.SmoothTransformation,
                )
        target_x = index * cell_width + max(4, (cell_width - fitted.width()) // 2)
        target_y = max(3, (target_height - fitted.height()) // 2)
        painter.drawImage(target_x, target_y, fitted)
    painter.end()
    return strip


class ElidedLabel(QLabel):
    """Single-line label that yields horizontal space instead of forcing overflow."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__("", parent)
        self._full_text = str(text or "")
        self.setWordWrap(False)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self._refresh_elision()

    def setText(self, text: str) -> None:  # type: ignore[override]
        self._full_text = str(text or "")
        self._refresh_elision()

    def fullText(self) -> str:
        return self._full_text

    def _refresh_elision(self) -> None:
        width = max(1, self.contentsRect().width())
        displayed = self.fontMetrics().elidedText(self._full_text, Qt.ElideRight, width)
        super().setText(displayed)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._refresh_elision()

    def sizeHint(self) -> QSize:  # type: ignore[override]
        base = super().sizeHint()
        return QSize(min(420, max(180, base.width())), base.height())

    def minimumSizeHint(self) -> QSize:  # type: ignore[override]
        base = super().minimumSizeHint()
        return QSize(80, base.height())


class AlignmentPreviewWidget(QWidget):
    """Shared score/MIDI/audio alignment timeline.

    Every available reference source is shown against the same MIDI bar ruler.
    The PDF lane contains real detected score measures, the audio lane contains
    the decoded signed waveform, and the lower lane compares MIDI velocity with
    reference-audio loudness/attacks.  No placeholder marks are presented as
    exact score positions.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._score_available = False
        self._audio_available = False
        self._adjusting_source = "score"
        self._score_shift_steps = 0
        self._score_length_steps = 0
        self._audio_shift_steps = 0
        self._audio_length_steps = 0
        self._bar_count = 4
        self._midi_profile: MidiPreviewProfile | None = None
        self._score_image: QImage | None = None
        self._score_image_status = "No mapped PDF score section"
        self._score_mapped_bars = 0
        self._audio_loudness: list[int] = []
        self._audio_waveform: list[int] = []
        self._audio_attack: list[int] = []
        self._audio_motion: list[int] = []
        self._audio_label = "No reference-audio waveform"
        self._cursor_fraction = 0.0
        self._cursor_visible = False
        self.setWindowFlags(Qt.Widget)
        self.setAttribute(Qt.WA_NativeWindow, False)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.set_sources(False, False)

    def sizeHint(self) -> QSize:  # type: ignore[override]
        return QSize(980, self.height())

    def set_sources(self, score_available: bool, audio_available: bool) -> None:
        self._score_available = bool(score_available)
        self._audio_available = bool(audio_available)
        if self._score_available and self._audio_available:
            height = 350
        elif self._score_available:
            height = 308
        elif self._audio_available:
            height = 232
        else:
            height = 210
        self.setMinimumHeight(height)
        self.setMaximumHeight(height)
        self.updateGeometry()
        self.update()

    def set_adjusting_source(self, source: str) -> None:
        self._adjusting_source = "audio" if str(source) == "audio" else "score"
        self.update()

    def set_preview_window(self, bar_count: int) -> None:
        self._bar_count = max(4, min(16, int(bar_count or 4)))
        self.update()

    def set_cursor_fraction(self, fraction: float, *, visible: bool | None = None) -> None:
        self._cursor_fraction = max(0.0, min(1.0, float(fraction if fraction is not None else 0.0)))
        if visible is not None:
            self._cursor_visible = bool(visible)
        self.update()

    def set_playing(self, playing: bool) -> None:
        self._cursor_visible = bool(playing)
        self.update()

    @staticmethod
    def visual_cursor_fraction(
        tick: int,
        start_tick: int,
        end_tick: int,
        profile: MidiPreviewProfile | None,
        *,
        playing: bool,
    ) -> float:
        """Return a tempo-independent reading cue slightly ahead of sound."""
        span = max(1, int(end_tick) - int(start_tick))
        lead_ticks = 0
        if playing and profile is not None:
            beats_per_bar = max(1.0, float(profile.beats_per_bar or 4.0))
            ticks_per_beat = float(profile.ticks_per_bar or 1920) / beats_per_bar
            lead_ticks = max(1, int(round(ticks_per_beat * 0.20)))
        visual_tick = int(tick) + lead_ticks
        return max(0.0, min(1.0, (visual_tick - int(start_tick)) / float(span)))

    def set_score_alignment(self, shift_steps: int, length_steps: int) -> None:
        self._score_shift_steps = int(shift_steps or 0)
        self._score_length_steps = int(length_steps or 0)
        self.update()

    def set_audio_alignment(self, shift_steps: int, length_steps: int) -> None:
        self._audio_shift_steps = int(shift_steps or 0)
        self._audio_length_steps = int(length_steps or 0)
        self.update()

    def set_midi_context(self, profile: MidiPreviewProfile | None) -> None:
        self._midi_profile = profile
        self.update()

    def set_score_context(self, profile: ScorePreviewProfile | None) -> None:
        # Structured score data remains available elsewhere; this view uses the
        # actual rendered PDF notation instead of synthetic score marks.
        self.update()

    def set_score_image(
        self,
        image: QImage | None,
        status: str | None = None,
        *,
        mapped_bars: int = 0,
        measure_mapped: bool = False,
    ) -> None:
        self._score_image = image if image is not None and not image.isNull() else None
        self._score_image_status = status or "No mapped PDF score section"
        self._score_mapped_bars = max(0, int(mapped_bars or 0)) if measure_mapped else 0
        self.update()

    def set_audio_context(
        self,
        loudness: list[int] | tuple[int, ...] | None,
        attack: list[int] | tuple[int, ...] | None,
        motion: list[int] | tuple[int, ...] | None,
        label: str | None = None,
        waveform: list[int] | tuple[int, ...] | None = None,
    ) -> None:
        self._audio_loudness = [max(0, min(100, int(v))) for v in (loudness or []) if v is not None]
        self._audio_waveform = [max(-100, min(100, int(v))) for v in (waveform or []) if v is not None]
        self._audio_attack = [max(0, min(100, int(v))) for v in (attack or []) if v is not None]
        self._audio_motion = [max(0, min(100, int(v))) for v in (motion or []) if v is not None]
        self._audio_label = label or ("Reference audio waveform" if self._audio_waveform else "No reference-audio waveform")
        self.update()

    def _global_transform(self, source: str) -> tuple[float, float]:
        midi = self._midi_profile
        beats_per_bar = max(1.0, float(getattr(midi, "beats_per_bar", 4.0) or 4.0))
        total_bars = max(1.0, float(getattr(midi, "total_bars", self._bar_count) or self._bar_count)) if midi else float(self._bar_count)
        total_beats = max(1.0, total_bars * beats_per_bar)
        if source == "score":
            shift_steps, length_steps = self._score_shift_steps, self._score_length_steps
        else:
            shift_steps, length_steps = self._audio_shift_steps, self._audio_length_steps
        shift_fraction = (float(shift_steps) * 0.5) / total_beats
        stretch = 1.0 + ((float(length_steps) * 0.5) / total_beats)
        return shift_fraction, max(0.20, min(3.0, stretch))

    def _source_fraction_to_local(self, source: str, source_fraction: float) -> float:
        midi = self._midi_profile
        window_start = max(0.0, min(1.0, float(getattr(midi, "window_start_fraction", 0.0) or 0.0))) if midi else 0.0
        window_end = max(window_start + 0.0001, min(1.0, float(getattr(midi, "window_end_fraction", 1.0) or 1.0))) if midi else 1.0
        shift, stretch = self._global_transform(source)
        midi_fraction = shift + max(0.0, min(1.0, float(source_fraction))) * stretch
        return (midi_fraction - window_start) / max(0.0001, window_end - window_start)

    def _transform(self, source: str) -> tuple[float, float]:
        midi = self._midi_profile
        window_start = max(0.0, min(1.0, float(getattr(midi, "window_start_fraction", 0.0) or 0.0))) if midi else 0.0
        local_start = self._source_fraction_to_local(source, window_start)
        _shift, stretch = self._global_transform(source)
        return local_start, stretch


    def _lane(self, painter: QPainter, rect: QRect, label: str, *, active: bool = False) -> None:
        painter.setPen(QPen(QColor("#7c3aed" if active else "#d6dfeb"), 2 if active else 1))
        painter.setBrush(QBrush(QColor("#ffffff")))
        painter.drawRoundedRect(rect, 5, 5)
        painter.setPen(QPen(QColor("#27364a"), 1))
        painter.drawText(self.rect().left() + 13, rect.top() + 24, label)

    def _draw_midi_notes(self, painter: QPainter, lane: QRect) -> None:
        midi = self._midi_profile
        if not midi or not midi.notes:
            painter.setPen(QPen(QColor("#94a3b8"), 1))
            painter.drawText(lane.adjusted(8, 8, -8, -8), Qt.AlignLeft | Qt.AlignVCenter, "No MIDI notes in this section")
            return
        pitches = [int(note.pitch) for note in midi.notes]
        low, high = min(pitches), max(pitches)
        span = max(1, high - low)
        inner = lane.adjusted(5, 19, -5, -6)
        marker_width = 7 if self._bar_count <= 4 else (5 if self._bar_count <= 8 else 3)
        for note in midi.notes:
            x = inner.left() + int(inner.width() * max(0.0, min(1.0, float(note.start))))
            pitch_fraction = (int(note.pitch) - low) / float(span)
            y = inner.bottom() - int(pitch_fraction * max(1, inner.height() - 5))
            velocity = max(1, min(127, int(note.velocity)))
            alpha = 120 + int((velocity / 127.0) * 110)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(QColor(37, 99, 235, alpha)))
            painter.drawRoundedRect(
                QRect(x, y - 2, min(marker_width, max(2, inner.right() - x + 1)), 5),
                1,
                1,
            )
        painter.setPen(QPen(QColor("#52657a"), 1))
        label = (midi.label or "MIDI notes") + " · onset view"
        painter.drawText(lane.left() + 7, lane.top() + 14, label[:120])

    def _draw_audio_waveform(self, painter: QPainter, lane: QRect) -> None:
        values = self._audio_waveform
        if len(values) >= 4 and len(values) % 2 == 0:
            pairs = [(values[i], values[i + 1]) for i in range(0, len(values), 2)]
        else:
            pairs = [(-abs(value), abs(value)) for value in values]
        center = lane.center().y() + 7
        painter.setPen(QPen(QColor("#c4d0df"), 1))
        painter.drawLine(lane.left() + 3, center, lane.right() - 3, center)
        mapped: list[tuple[float, int, int]] = []
        for index, (low, high) in enumerate(pairs):
            raw = index / max(1, len(pairs) - 1)
            local = self._source_fraction_to_local("audio", raw)
            if -0.01 <= local <= 1.01:
                mapped.append((local, low, high))
        if not mapped:
            painter.setPen(QPen(QColor("#b45309"), 1))
            painter.drawText(lane.adjusted(8, 8, -8, -8), Qt.AlignLeft | Qt.AlignVCenter | Qt.TextWordWrap, "No decoded waveform is visible in this section. Adjust Audio start/end or reload after FFmpeg is configured.")
            return
        magnitudes = sorted(max(abs(low), abs(high)) for _local, low, high in mapped)
        percentile_index = min(len(magnitudes) - 1, max(0, int(round((len(magnitudes) - 1) * 0.88))))
        reference_peak = max(1, magnitudes[percentile_index])
        half_height = max(8.0, float(lane.height() // 2 - 18))

        def shaped_y(value: int) -> float:
            magnitude = min(1.0, abs(float(value)) / float(reference_peak))
            expanded = magnitude ** 0.78
            signed = -expanded if value < 0 else expanded
            return float(center) - signed * half_height

        top_points: list[QPointF] = []
        bottom_points: list[QPointF] = []
        for local, low, high in mapped:
            x = float(lane.left()) + float(lane.width()) * local
            top = min(shaped_y(low), shaped_y(high))
            bottom = max(shaped_y(low), shaped_y(high))
            if abs(bottom - top) < 1.5:
                top = float(center) - 0.75
                bottom = float(center) + 0.75
            top_points.append(QPointF(x, top))
            bottom_points.append(QPointF(x, bottom))

        if top_points:
            # Use a stepped min/max envelope.  Diagonal interpolation made the
            # reference performance look artificially smooth and hid attacks.
            # Bucket edges preserve abrupt peaks and valleys without altering
            # the decoded audio or its alignment.
            left_edges: list[float] = []
            right_edges: list[float] = []
            for index, point in enumerate(top_points):
                previous_x = top_points[index - 1].x() if index > 0 else float(lane.left())
                next_x = top_points[index + 1].x() if index + 1 < len(top_points) else float(lane.right())
                left_edges.append(float(lane.left()) if index == 0 else (previous_x + point.x()) / 2.0)
                right_edges.append(float(lane.right()) if index + 1 == len(top_points) else (point.x() + next_x) / 2.0)

            envelope = QPainterPath(QPointF(left_edges[0], top_points[0].y()))
            for index, point in enumerate(top_points):
                if index > 0:
                    envelope.lineTo(QPointF(left_edges[index], top_points[index - 1].y()))
                    envelope.lineTo(QPointF(left_edges[index], point.y()))
                envelope.lineTo(QPointF(right_edges[index], point.y()))
            for index in range(len(bottom_points) - 1, -1, -1):
                point = bottom_points[index]
                envelope.lineTo(QPointF(right_edges[index], point.y()))
                envelope.lineTo(QPointF(left_edges[index], point.y()))
                if index > 0:
                    envelope.lineTo(QPointF(left_edges[index], bottom_points[index - 1].y()))
            envelope.closeSubpath()
            painter.setPen(QPen(QColor("#0369a1"), 1.4))
            painter.setBrush(QBrush(QColor(14, 165, 233, 88)))
            painter.drawPath(envelope)

            painter.setPen(QPen(QColor("#0284c7"), 1))
            for edge, point_top, point_bottom in zip(left_edges, top_points, bottom_points):
                painter.drawLine(QPointF(edge, point_top.y()), QPointF(edge, point_bottom.y()))
        painter.setPen(QPen(QColor("#0369a1"), 1))
        painter.drawText(lane.left() + 7, lane.top() + 14, (self._audio_label + " · waveform envelope")[:125])

    def _draw_dynamics_timing(self, painter: QPainter, lane: QRect) -> None:
        inner = lane.adjusted(5, 20, -5, -7)
        painter.setPen(QPen(QColor("#e2e8f0"), 1))
        painter.drawLine(inner.left(), inner.center().y(), inner.right(), inner.center().y())
        midi = self._midi_profile
        if midi and midi.notes:
            painter.setPen(QPen(QColor(37, 99, 235, 175), 2))
            for note in midi.notes:
                x = inner.left() + int(inner.width() * max(0.0, min(1.0, float(note.start))))
                velocity = max(1, min(127, int(note.velocity)))
                height = max(2, int(inner.height() * velocity / 127.0))
                painter.drawLine(x, inner.bottom(), x, inner.bottom() - height)

        visible_loudness: list[tuple[float, int]] = []
        if self._audio_available:
            for index, value in enumerate(self._audio_loudness):
                raw = index / max(1, len(self._audio_loudness) - 1)
                local = self._source_fraction_to_local("audio", raw)
                if 0.0 <= local <= 1.0:
                    visible_loudness.append((local, int(value)))
        if visible_loudness:
            low = min(value for _local, value in visible_loudness)
            high = max(value for _local, value in visible_loudness)
            audio_points: list[QPointF] = []
            for local, value in visible_loudness:
                normalized = 0.50 if high <= low else max(0.0, min(1.0, (value - low) / float(high - low)))
                display_value = 0.12 + (normalized ** 0.70) * 0.86
                audio_points.append(QPointF(
                    float(inner.left()) + float(inner.width()) * local,
                    float(inner.bottom()) - float(inner.height()) * display_value,
                ))
            if len(audio_points) >= 2:
                area = QPainterPath(QPointF(audio_points[0].x(), float(inner.bottom())))
                area.lineTo(audio_points[0])
                for point in audio_points[1:]:
                    area.lineTo(point)
                area.lineTo(QPointF(audio_points[-1].x(), float(inner.bottom())))
                area.closeSubpath()
                painter.setPen(QPen(QColor("#0f766e"), 2))
                painter.setBrush(QBrush(QColor(13, 148, 136, 54)))
                painter.drawPath(area)
        if self._audio_available and self._audio_attack:
            painter.setPen(QPen(QColor("#d97706"), 2))
            for index, value in enumerate(self._audio_attack):
                if value < 58:
                    continue
                raw = index / max(1, len(self._audio_attack) - 1)
                local = self._source_fraction_to_local("audio", raw)
                if 0.0 <= local <= 1.0:
                    x = inner.left() + int(inner.width() * local)
                    painter.drawLine(x, inner.top(), x, inner.top() + 8)

        painter.setPen(QPen(QColor("#52657a"), 1))
        legend = "MIDI velocity bars (blue)"
        if self._audio_available:
            legend += " · audio loudness contour (green) · attacks (orange)"
        if self._score_available:
            legend += " · printed score dynamics remain visible above"
        painter.drawText(lane.left() + 7, lane.top() + 14, legend[:150])

    def paintEvent(self, event) -> None:  # type: ignore[override]
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        outer = self.rect().adjusted(2, 2, -2, -2)
        painter.setPen(QPen(QColor("#cfd8e6"), 1))
        painter.setBrush(QBrush(QColor("#f8fafc")))
        painter.drawRoundedRect(outer, 9, 9)

        left = outer.left() + 104
        right = outer.right() - 15
        width = max(1, right - left)
        y = outer.top() + 26
        lanes: list[tuple[str, QRect]] = []
        if self._score_available:
            score_height = 126 if self._audio_available else 146
            lanes.append(("score", QRect(left, y, width, score_height)))
            y += score_height + 7
        midi_height = 60 if self._audio_available else 64
        lanes.append(("midi", QRect(left, y, width, midi_height)))
        y += midi_height + 7
        if self._audio_available:
            lanes.append(("audio", QRect(left, y, width, 64)))
            y += 71
        dynamics_height = 50 if self._audio_available else 52
        lanes.append(("dynamics", QRect(left, y, width, dynamics_height)))
        bottom = lanes[-1][1].bottom()

        midi = self._midi_profile
        start_bar = int(getattr(midi, "start_bar", 0) or 0) if midi else 0
        bars = max(4, min(16, self._bar_count))
        beats_per_bar = max(1, min(12, int(round(float(getattr(midi, "beats_per_bar", 4.0) or 4.0))))) if midi else 4
        for index in range(bars + 1):
            x = left + int(width * index / bars)
            painter.setPen(QPen(QColor("#dbe5f3"), 1))
            painter.drawLine(x, outer.top() + 21, x, bottom)
            if index < bars:
                painter.setPen(QPen(QColor("#52657a"), 1))
                painter.drawText(x + 4, outer.top() + 17, str(start_bar + index + 1))
                for beat in range(1, beats_per_bar):
                    beat_x = x + int((width / float(bars)) * beat / float(beats_per_bar))
                    painter.setPen(QPen(QColor("#edf2f7"), 1, Qt.DashLine))
                    painter.drawLine(beat_x, outer.top() + 22, beat_x, bottom)

        for lane_name, lane in lanes:
            label = {
                "score": "PDF score",
                "midi": "MIDI notes",
                "audio": "Audio",
                "dynamics": "Dynamics / timing",
            }[lane_name]
            active = lane_name == self._adjusting_source
            self._lane(painter, lane, label, active=active)
            if lane_name == "score":
                if self._score_image is None or self._score_image.isNull():
                    painter.setPen(QPen(QColor("#b45309"), 1))
                    painter.drawText(lane.adjusted(9, 9, -9, -9), Qt.AlignLeft | Qt.AlignVCenter | Qt.TextWordWrap, self._score_image_status)
                else:
                    shift, scale = self._transform("score")
                    mapped_fraction = max(0.05, min(1.0, self._score_mapped_bars / float(max(1, bars))))
                    draw_width = max(1, int(lane.width() * mapped_fraction * scale))
                    draw_height = lane.height() - 22
                    scaled = self._score_image.scaled(draw_width, draw_height, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
                    x = lane.left() + int(lane.width() * shift)
                    painter.save()
                    painter.setClipRect(lane.adjusted(1, 1, -1, -1))
                    painter.drawImage(QRect(x, lane.top() + 2, scaled.width(), scaled.height()), scaled)
                    painter.restore()
                    painter.setPen(QPen(QColor("#475467"), 1))
                    painter.drawText(lane.left() + 7, lane.bottom() - 5, self._score_image_status[:145])
            elif lane_name == "midi":
                self._draw_midi_notes(painter, lane)
            elif lane_name == "audio":
                self._draw_audio_waveform(painter, lane)
            else:
                self._draw_dynamics_timing(painter, lane)

        if self._cursor_visible:
            x = left + int(width * self._cursor_fraction)
            painter.setPen(QPen(QColor("#dc2626"), 2))
            painter.drawLine(x, outer.top() + 20, x, bottom)
            painter.setBrush(QBrush(QColor("#dc2626")))
            painter.drawEllipse(QRect(x - 4, outer.top() + 8, 8, 8))
            painter.setPen(QPen(QColor("#b91c1c"), 1))
            painter.drawText(x + 5, outer.top() + 15, "Cue")

class InstrumentLibraryManagerDialog(QDialog):
    """Discover, install, select, and change SoundFont instrument libraries."""

    def __init__(self, parent: "MainWindow") -> None:
        super().__init__(parent)
        self.parent_window = parent
        self.setWindowTitle("Instrument Libraries")
        self.setMinimumSize(700, 430)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        layout.addWidget(parent._section_title("Instrument Libraries"))
        note = QLabel("The instrument library has a major effect on the sound before interpretation is applied. Choose the library that best suits the performance.")
        note.setObjectName("SubtleText")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.list_widget = QListWidget()
        self.list_widget.setMinimumHeight(250)
        layout.addWidget(self.list_widget)
        self.status = QLabel("")
        self.status.setObjectName("FeedbackText")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        install = QPushButton("Install GeneralUser GS…")
        install.setObjectName("PrimaryButton")
        install.clicked.connect(self.install_recommended)
        add = QPushButton("Add Existing Library…")
        add.clicked.connect(self.add_existing)
        activate = QPushButton("Use Selected Library")
        activate.clicked.connect(self.activate_selected)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        buttons.addWidget(install)
        buttons.addWidget(add)
        buttons.addStretch(1)
        buttons.addWidget(activate)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self.list_widget.itemDoubleClicked.connect(lambda _item: self.activate_selected())
        self.refresh()

    @staticmethod
    def _friendly_name(path: Path) -> str:
        name = path.stem.replace("_", " ").replace("-", " ")
        if "generaluser" in name.lower():
            return "GeneralUser GS (Recommended)"
        if "musescore" in name.lower():
            return "MuseScore General (Compatibility)"
        return name

    def _all_paths(self) -> list[Path]:
        candidates: list[Path] = []
        selected = self.parent_window.project.selected_soundfont_path
        if selected:
            candidates.append(Path(selected))
        for value in self.parent_window.settings.get("recent_soundfont_paths", []) or []:
            candidates.append(Path(value))
        directory = self.parent_window.settings.get("last_soundfont_directory")
        if directory and Path(directory).is_dir():
            candidates.extend(sorted(Path(directory).glob("*.sf2")))
            candidates.extend(sorted(Path(directory).glob("*.sf3")))
        for folder in (
            self.parent_window._repo_root() / "assets" / "soundfonts",
            self.parent_window._repo_root() / "soundfonts",
        ):
            if folder.is_dir():
                candidates.extend(sorted(folder.glob("*.sf2")))
                candidates.extend(sorted(folder.glob("*.sf3")))
        seen: set[str] = set()
        result = []
        for candidate in candidates:
            try:
                key = str(candidate.resolve())
            except OSError:
                key = str(candidate)
            if candidate.is_file() and candidate.suffix.lower() in {".sf2", ".sf3"} and key not in seen:
                seen.add(key)
                result.append(candidate)
        return result

    def refresh(self) -> None:
        current = str(self.parent_window.project.selected_soundfont_path or "")
        self.list_widget.clear()
        for path in self._all_paths():
            marker = "✓ " if current and str(path) == current else ""
            size_mb = path.stat().st_size / (1024 * 1024)
            item = QListWidgetItem(f"{marker}{self._friendly_name(path)}    {size_mb:.1f} MB")
            item.setData(Qt.UserRole, str(path))
            item.setToolTip(str(path))
            self.list_widget.addItem(item)
            if current and str(path) == current:
                self.list_widget.setCurrentItem(item)
        if not self.list_widget.count():
            self.status.setText("No instrument library is available. Install the recommended library or add an existing .sf2 or .sf3 file.")
        else:
            self.status.setText(f"{self.list_widget.count()} instrument library file(s) available.")

    def _progress_install(self, title: str, installer) -> None:
        progress = QProgressDialog(title, "Cancel", 0, 100, self)
        progress.setWindowTitle(title)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setValue(0)
        def update(label: str, value: int) -> None:
            progress.setLabelText(label)
            progress.setValue(value)
            QApplication.processEvents()
        result = installer(self.parent_window._repo_root(), progress=update)
        progress.close()
        if not result.ok or not result.soundfont_path:
            QMessageBox.warning(self, title, result.detail or "Installation did not complete.")
            return
        self.parent_window._activate_soundfont(result.soundfont_path, prepare=False)
        self.refresh()
        QMessageBox.information(self, title, "GeneralUser GS is installed and selected as the current instrument library.")

    def install_recommended(self) -> None:
        answer = QMessageBox.question(
            self,
            "Install GeneralUser GS",
            "Download the recommended GeneralUser GS instrument library from its official release and store it inside MIDI Performer?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if answer == QMessageBox.Yes:
            self._progress_install("Install GeneralUser GS", install_recommended_soundfont)

    def add_existing(self) -> None:
        start = self.parent_window.settings.get("last_soundfont_directory") or str(self.parent_window._repo_root())
        path, _ = QFileDialog.getOpenFileName(self, "Add instrument library", start, "Instrument libraries (*.sf2 *.sf3);;All Files (*.*)")
        if path:
            check = check_soundfont(self.parent_window._repo_root(), path, str(Path(path).parent))
            if not check.confirmed:
                QMessageBox.warning(self, "Instrument library", check.detail)
                return
            self.parent_window._activate_soundfont(path, prepare=False)
            self.refresh()

    def activate_selected(self) -> None:
        item = self.list_widget.currentItem()
        if not item:
            QMessageBox.information(self, "Instrument library", "Select an instrument library first.")
            return
        path = str(item.data(Qt.UserRole) or "")
        if path:
            self.parent_window._activate_soundfont(path, prepare=True)
            self.refresh()
            self.status.setText(f"Current instrument library: {Path(path).name}")


class FirstRunSetupDialog(QDialog):
    """Clear first-run setup with one action per missing component."""

    def __init__(self, parent: "MainWindow") -> None:
        super().__init__(parent)
        self.parent_window = parent
        self.setWindowTitle("Welcome to MIDI Performer")
        self.setMinimumWidth(760)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)
        title = QLabel("Prepare MIDI Performer")
        title.setObjectName("SectionTitle")
        layout.addWidget(title)
        intro = QLabel("Complete the two playback items below. Score analysis is optional. Nothing needs to be moved, and MIDI Performer does not change Windows PATH.")
        intro.setObjectName("SubtleText")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(10)
        self.grid.setVerticalSpacing(12)
        self.fs_status = QLabel()
        self.sf_status = QLabel()
        self.omr_status = QLabel()
        for label in (self.fs_status, self.sf_status, self.omr_status):
            label.setWordWrap(True)
            label.setObjectName("LoadedName")
        self.grid.addWidget(QLabel("Playback Engine (FluidSynth)"), 0, 0)
        self.grid.addWidget(self.fs_status, 0, 1)
        self.install_fs = QPushButton("Install FluidSynth…")
        self.install_fs.setObjectName("PrimaryButton")
        self.install_fs.clicked.connect(self.install_fluidsynth)
        self.use_fs = QPushButton("Use Existing…")
        self.use_fs.clicked.connect(self.choose_fluidsynth)
        self.grid.addWidget(self.install_fs, 0, 2)
        self.grid.addWidget(self.use_fs, 0, 3)
        self.grid.addWidget(QLabel("Instrument Library"), 1, 0)
        self.grid.addWidget(self.sf_status, 1, 1)
        self.install_sf = QPushButton("Install GeneralUser GS…")
        self.install_sf.setObjectName("PrimaryButton")
        self.install_sf.clicked.connect(self.install_soundfont)
        self.use_sf = QPushButton("Choose Existing…")
        self.use_sf.clicked.connect(self.choose_soundfont)
        self.grid.addWidget(self.install_sf, 1, 2)
        self.grid.addWidget(self.use_sf, 1, 3)
        self.grid.addWidget(QLabel("Score Analysis (Audiveris)"), 2, 0)
        self.grid.addWidget(self.omr_status, 2, 1)
        self.install_omr = QPushButton("Install Audiveris…")
        self.install_omr.clicked.connect(self.install_audiveris)
        self.use_omr = QPushButton("Use Existing…")
        self.use_omr.clicked.connect(self.choose_audiveris)
        self.grid.addWidget(self.install_omr, 2, 2)
        self.grid.addWidget(self.use_omr, 2, 3)
        self.grid.setColumnStretch(1, 1)
        layout.addLayout(self.grid)
        self.summary = QLabel("")
        self.summary.setObjectName("FeedbackText")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        # Playback verification stays inside the first-run window.  Earlier
        # builds displayed a blocking information box before the chord, so the
        # user heard sound only after dismissing an apparently blank popup.
        self.audio_test_panel = QFrame()
        self.audio_test_panel.setObjectName("AudioTestPanel")
        test_layout = QVBoxLayout(self.audio_test_panel)
        test_layout.setContentsMargins(12, 10, 12, 10)
        test_layout.setSpacing(8)
        test_title = QLabel("Playback Test")
        test_title.setObjectName("SectionTitle")
        test_layout.addWidget(test_title)
        self.audio_test_status = QLabel("Not tested. Click Test Playback to hear a short piano chord.")
        self.audio_test_status.setWordWrap(True)
        self.audio_test_status.setObjectName("SubtleText")
        test_layout.addWidget(self.audio_test_status)
        test_answers = QHBoxLayout()
        self.heard_yes_button = QPushButton("Yes, I heard it")
        self.heard_yes_button.setObjectName("PrimaryButton")
        self.heard_yes_button.clicked.connect(self.confirm_audio_heard)
        self.heard_no_button = QPushButton("No sound")
        self.heard_no_button.clicked.connect(self.report_audio_not_heard)
        self.play_again_button = QPushButton("Play Again")
        self.play_again_button.clicked.connect(self.play_audio_again)
        for button in (self.heard_yes_button, self.heard_no_button, self.play_again_button):
            button.setVisible(False)
            test_answers.addWidget(button)
        test_answers.addStretch(1)
        test_layout.addLayout(test_answers)
        layout.addWidget(self.audio_test_panel)

        self._audio_test_candidates: list[str] = []
        self._audio_test_index = 0
        self._audio_test_driver: str | None = None
        self._audio_test_failures: list[str] = []
        self._audio_test_running = False

        buttons = QHBoxLayout()
        self.test_button = QPushButton("Test Playback…")
        self.test_button.clicked.connect(self.start_audio_test)
        self.start_button = QPushButton("Start MIDI Performer")
        self.start_button.setObjectName("PrimaryButton")
        self.start_button.clicked.connect(self.accept)
        buttons.addWidget(self.test_button)
        buttons.addStretch(1)
        buttons.addWidget(self.start_button)
        layout.addLayout(buttons)
        self.refresh()

    def refresh(self) -> None:
        deps = check_dependencies(
            self.parent_window._repo_root(),
            self.parent_window.settings.get("audiveris_path"),
            self.parent_window.settings.get("fluidsynth_path"),
            self.parent_window.settings.get("last_soundfont_path"),
            self.parent_window.settings.get("last_soundfont_directory"),
        )
        fs, sf, omr = deps["fluidsynth"], deps["soundfont"], deps["audiveris"]
        self.fs_status.setText(f"Ready: {fs.path}" if fs.confirmed else (f"Found but not verified: {fs.path}" if fs.found else "Not installed or not found"))
        self.sf_status.setText(f"Ready: {Path(sf.path).name}" if sf.confirmed and sf.path else "No instrument library selected")
        self.omr_status.setText(f"Ready: {omr.path}" if omr.found else "Optional: not found")
        self.install_fs.setVisible(not fs.confirmed)
        self.install_sf.setVisible(not sf.confirmed)
        self.install_omr.setVisible(not omr.found)
        ready = fs.confirmed and sf.confirmed
        self.test_button.setEnabled(ready and not self._audio_test_running)
        self.start_button.setEnabled(ready)
        verified = bool(self.parent_window.settings.get("audio_driver_verified", False))
        if ready and verified and not self._audio_test_running:
            driver = str(self.parent_window.settings.get("audio_driver", "default") or "default")
            self.summary.setText(f"Playback is configured and verified using the {driver} audio route.")
            if self._audio_test_driver is None:
                self.audio_test_status.setText(f"Playback verified. Current audio route: {driver}.")
                self.play_again_button.setVisible(True)
        elif ready:
            self.summary.setText("Playback components are configured. Test Playback is recommended before starting MIDI Performer.")
        else:
            self.summary.setText("Install or select both FluidSynth and an instrument library to continue.")
            self.audio_test_status.setText("Playback cannot be tested until both required items are ready.")
            for button in (self.heard_yes_button, self.heard_no_button, self.play_again_button):
                button.setVisible(False)

    def _audio_test_dependencies(self):
        return check_dependencies(
            self.parent_window._repo_root(),
            self.parent_window.settings.get("audiveris_path"),
            self.parent_window.settings.get("fluidsynth_path"),
            self.parent_window.settings.get("last_soundfont_path"),
            self.parent_window.settings.get("last_soundfont_directory"),
        )

    def start_audio_test(self) -> None:
        """Play immediately, then collect feedback inside this dialog."""
        deps = self._audio_test_dependencies()
        fs_check, sf_check = deps["fluidsynth"], deps["soundfont"]
        if not fs_check.confirmed or not sf_check.confirmed or not sf_check.path:
            self.audio_test_status.setText("FluidSynth and an instrument library must both be ready before playback can be tested.")
            self.refresh()
            return
        if sf_check.path:
            self.parent_window.project.selected_soundfont_path = sf_check.path
        backend = self.parent_window.playback_engine.backend
        candidate_getter = getattr(backend, "available_driver_candidates", None)
        candidates = candidate_getter() if callable(candidate_getter) else [str(self.parent_window.settings.get("audio_driver", "default") or "default")]
        preferred = str(self.parent_window.settings.get("audio_driver", "default") or "default")
        ordered: list[str] = []
        for candidate in [preferred, *[str(value or "default") for value in candidates]]:
            if candidate not in ordered:
                ordered.append(candidate)
        self._audio_test_candidates = ordered or ["default"]
        self._audio_test_index = 0
        self._audio_test_driver = None
        self._audio_test_failures = []
        self._try_current_audio_route()

    def _try_current_audio_route(self) -> None:
        if self._audio_test_index >= len(self._audio_test_candidates):
            self._audio_test_running = False
            self.test_button.setEnabled(True)
            self.start_button.setEnabled(True)
            self.heard_yes_button.setVisible(False)
            self.heard_no_button.setVisible(False)
            self.play_again_button.setVisible(True)
            diagnostics = self.parent_window.playback_engine.audio_diagnostics()
            log_path = str(diagnostics.get("log_path") or (self.parent_window._repo_root() / "logs" / "audio_diagnostics.log"))
            detail = "; ".join(self._audio_test_failures[-4:]) or "No route completed the test."
            self.audio_test_status.setText(
                "No audible route was confirmed. Check the Windows output device and volume, then choose Play Again. "
                f"Details: {detail}. Diagnostic log: {log_path}"
            )
            self.parent_window.settings.set("audio_driver_verified", False)
            return

        driver = str(self._audio_test_candidates[self._audio_test_index] or "default")
        self._audio_test_driver = driver
        self._audio_test_running = True
        self.test_button.setEnabled(False)
        self.start_button.setEnabled(False)
        for button in (self.heard_yes_button, self.heard_no_button, self.play_again_button):
            button.setVisible(False)
        self.audio_test_status.setText(f"Playing a short piano chord using the {driver} audio route…")
        QApplication.processEvents()
        QTimer.singleShot(40, lambda selected=driver: self._perform_audio_test(selected))

    def _perform_audio_test(self, driver: str) -> None:
        soundfont = str(self.parent_window.project.selected_soundfont_path or "")
        try:
            self.parent_window.stop_playback(silent=True)
            self.parent_window.playback_engine.test_audio_tone(
                soundfont,
                volume_gain=max(0.65, self.parent_window._volume_gain()),
                driver=driver,
            )
        except Exception as exc:  # noqa: BLE001 - visible guided recovery
            self._audio_test_failures.append(f"{driver}: {exc}")
            try:
                self.parent_window.playback_engine.backend.close()
            except Exception:
                pass
            self._audio_test_index += 1
            self.audio_test_status.setText(f"The {driver} route could not start. Trying the next Windows audio route…")
            QApplication.processEvents()
            QTimer.singleShot(120, self._try_current_audio_route)
            return

        self._audio_test_running = False
        self.test_button.setEnabled(True)
        self.start_button.setEnabled(True)
        self.audio_test_status.setText(f"Did you hear the piano chord? Audio route tested: {driver}.")
        self.heard_yes_button.setVisible(True)
        self.heard_no_button.setVisible(True)
        self.play_again_button.setVisible(True)

    def confirm_audio_heard(self) -> None:
        driver = str(self._audio_test_driver or "default")
        self.parent_window.settings.set("audio_driver", driver)
        self.parent_window.settings.set("audio_driver_verified", True)
        self.parent_window.playback_engine.set_audio_driver(driver)
        try:
            self.parent_window.playback_engine.backend.close()
        except Exception:
            pass
        self.audio_test_status.setText(f"Playback verified. MIDI Performer will use the {driver} audio route.")
        self.heard_yes_button.setVisible(False)
        self.heard_no_button.setVisible(False)
        self.play_again_button.setVisible(True)
        self.refresh()

    def report_audio_not_heard(self) -> None:
        driver = str(self._audio_test_driver or "default")
        self._audio_test_failures.append(f"{driver}: chord was not heard")
        try:
            self.parent_window.playback_engine.backend.close()
        except Exception:
            pass
        self._audio_test_index += 1
        if self._audio_test_index < len(self._audio_test_candidates):
            next_driver = self._audio_test_candidates[self._audio_test_index]
            self.audio_test_status.setText(f"No sound was confirmed. Trying the next Windows audio route: {next_driver}…")
            QApplication.processEvents()
            QTimer.singleShot(120, self._try_current_audio_route)
        else:
            self._try_current_audio_route()

    def play_audio_again(self) -> None:
        driver = str(self._audio_test_driver or self.parent_window.settings.get("audio_driver", "default") or "default")
        self._audio_test_candidates = [driver]
        self._audio_test_index = 0
        self._audio_test_failures = []
        self._try_current_audio_route()

    def _run_install(self, title: str, installer, result_key: str) -> None:
        progress = QProgressDialog(title, "Cancel", 0, 100, self)
        progress.setWindowTitle(title)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        def update(label: str, value: int) -> None:
            progress.setLabelText(label)
            progress.setValue(value)
            QApplication.processEvents()
        result = installer(self.parent_window._repo_root(), progress=update)
        progress.close()
        if not result.ok:
            QMessageBox.warning(self, title, result.detail)
            return
        if result_key == "fluidsynth" and result.fluidsynth_path:
            self.parent_window.settings.set("audio_driver_verified", False)
            self.parent_window.settings.set("fluidsynth_path", result.fluidsynth_path)
            self.parent_window.player.set_fluidsynth_path(result.fluidsynth_path)
        if result_key == "soundfont" and result.soundfont_path:
            self.parent_window._activate_soundfont(result.soundfont_path, prepare=False)
        self.refresh()

    def install_fluidsynth(self) -> None:
        self._run_install("Install FluidSynth", install_fluidsynth, "fluidsynth")

    def install_soundfont(self) -> None:
        self._run_install("Install GeneralUser GS", install_recommended_soundfont, "soundfont")

    def choose_fluidsynth(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose fluidsynth.exe", str(self.parent_window._repo_root()), "FluidSynth (fluidsynth.exe);;Executables (*.exe);;All Files (*.*)")
        if path:
            check = check_fluidsynth(self.parent_window._repo_root(), path)
            if not check.confirmed:
                QMessageBox.warning(self, "FluidSynth", check.detail)
                return
            self.parent_window.settings.set("audio_driver_verified", False)
            self.parent_window.settings.set("fluidsynth_path", path)
            self.parent_window.player.set_fluidsynth_path(path)
            self.refresh()

    def choose_soundfont(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose instrument library", str(self.parent_window._repo_root()), "Instrument libraries (*.sf2 *.sf3);;All Files (*.*)")
        if path:
            check = check_soundfont(self.parent_window._repo_root(), path, str(Path(path).parent))
            if not check.confirmed:
                QMessageBox.warning(self, "Instrument library", check.detail)
                return
            self.parent_window._activate_soundfont(path, prepare=False)
            self.refresh()

    def choose_audiveris(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose Audiveris launcher", str(self.parent_window._repo_root()), "Audiveris (*.exe *.bat *.cmd *.jar);;All Files (*.*)")
        if not path:
            folder = QFileDialog.getExistingDirectory(self, "Or choose the Audiveris installation folder", str(self.parent_window._repo_root()))
            path = folder
        if path:
            self.parent_window.settings.set("audiveris_path", path)
            self.refresh()

    def install_audiveris(self) -> None:
        self.parent_window.install_audiveris_engine()
        self.refresh()


class SettingsDialog(QDialog):
    """Configuration for folders, playback engine, instrument libraries, and score analysis."""

    def __init__(self, parent: "MainWindow") -> None:
        super().__init__(parent)
        self.parent_window = parent
        self.setWindowTitle("Settings")
        self.setMinimumWidth(780)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        layout.addWidget(parent._section_title("File Locations"))
        self.rows: dict[str, QLabel] = {}
        folders = QGridLayout()
        for row, (label_text, key) in enumerate([
            ("MIDI folder", "last_midi_directory"), ("Output folder", "last_output_directory"),
            ("Project folder", "last_project_directory"), ("Score source folder", "last_score_source_directory"),
            ("Reference audio folder", "last_reference_audio_directory"),
        ]):
            folders.addWidget(QLabel(label_text), row, 0)
            value = QLabel(str(parent.settings.get(key) or "Not set")); value.setWordWrap(True); value.setObjectName("LoadedName")
            self.rows[key] = value; folders.addWidget(value, row, 1)
            button = QPushButton("Choose…"); button.clicked.connect(lambda _c=False, k=key: self.choose_folder(k)); folders.addWidget(button, row, 2)
        folders.setColumnStretch(1, 1)
        layout.addLayout(folders)
        layout.addWidget(parent._section_title("Playback and Score Components"))
        note = QLabel("Install a missing component or point MIDI Performer to an existing one. Instrument libraries are musical choices and can also be changed from the main window.")
        note.setObjectName("SubtleText"); note.setWordWrap(True); layout.addWidget(note)
        self.fs_value = QLabel(); self.sf_value = QLabel(); self.omr_value = QLabel()
        for value in (self.fs_value, self.sf_value, self.omr_value): value.setWordWrap(True); value.setObjectName("LoadedName")
        grid = QGridLayout(); grid.setHorizontalSpacing(8); grid.setVerticalSpacing(10)
        grid.addWidget(QLabel("Playback Engine (FluidSynth)"), 0, 0); grid.addWidget(self.fs_value, 0, 1)
        self.install_fs = QPushButton("Install FluidSynth…"); self.install_fs.clicked.connect(self.install_fluidsynth); grid.addWidget(self.install_fs, 0, 2)
        fs_existing = QPushButton("Use Existing…"); fs_existing.clicked.connect(self.choose_fluidsynth); grid.addWidget(fs_existing, 0, 3)
        fs_test = QPushButton("Test Playback…"); fs_test.clicked.connect(lambda: parent.run_audio_diagnostics(self)); grid.addWidget(fs_test, 0, 4)
        grid.addWidget(QLabel("Instrument Library"), 1, 0); grid.addWidget(self.sf_value, 1, 1)
        self.install_sf = QPushButton("Install GeneralUser GS…"); self.install_sf.clicked.connect(self.install_soundfont); grid.addWidget(self.install_sf, 1, 2)
        sf_manage = QPushButton("Manage Libraries…"); sf_manage.clicked.connect(self.manage_libraries); grid.addWidget(sf_manage, 1, 3)
        grid.addWidget(QLabel("Score Analysis (Audiveris)"), 2, 0); grid.addWidget(self.omr_value, 2, 1)
        self.install_omr = QPushButton("Install Audiveris…"); self.install_omr.clicked.connect(parent.install_audiveris_engine); grid.addWidget(self.install_omr, 2, 2)
        omr_existing = QPushButton("Use Existing…"); omr_existing.clicked.connect(self.choose_omr); grid.addWidget(omr_existing, 2, 3)
        grid.setColumnStretch(1, 1); layout.addLayout(grid)
        self.refresh_statuses()
        buttons = QHBoxLayout(); buttons.addStretch(1); close = QPushButton("Close"); close.clicked.connect(self.accept); buttons.addWidget(close); layout.addLayout(buttons)

    def choose_folder(self, key: str) -> None:
        current = self.parent_window.settings.get(key) or str(self.parent_window._repo_root())
        folder = QFileDialog.getExistingDirectory(self, "Choose folder", current)
        if folder:
            self.parent_window.settings.set(key, folder); self.rows[key].setText(folder)

    def refresh_statuses(self) -> None:
        deps = check_dependencies(self.parent_window._repo_root(), self.parent_window.settings.get("audiveris_path"), self.parent_window.settings.get("fluidsynth_path"), self.parent_window.settings.get("last_soundfont_path"), self.parent_window.settings.get("last_soundfont_directory"))
        fs, sf, omr = deps["fluidsynth"], deps["soundfont"], deps["audiveris"]
        self.fs_value.setText(f"Ready: {fs.path}" if fs.confirmed else (f"Found but not verified: {fs.path}" if fs.found else "Not found"))
        self.sf_value.setText(f"Current: {Path(sf.path).name}" if sf.confirmed and sf.path else "No instrument library selected")
        self.omr_value.setText(f"Ready: {omr.path}" if omr.found else "Optional: not found")
        self.install_fs.setVisible(not fs.confirmed); self.install_sf.setVisible(not sf.confirmed); self.install_omr.setVisible(not omr.found)

    def _install(self, title: str, installer, kind: str) -> None:
        progress = QProgressDialog(title, "Cancel", 0, 100, self); progress.setWindowTitle(title); progress.setWindowModality(Qt.WindowModal); progress.setMinimumDuration(0)
        def update(label: str, value: int) -> None: progress.setLabelText(label); progress.setValue(value); QApplication.processEvents()
        result = installer(self.parent_window._repo_root(), progress=update); progress.close()
        if not result.ok: QMessageBox.warning(self, title, result.detail); return
        if kind == "fs" and result.fluidsynth_path:
            self.parent_window.settings.set("audio_driver_verified", False)
            self.parent_window.settings.set("fluidsynth_path", result.fluidsynth_path); self.parent_window.player.set_fluidsynth_path(result.fluidsynth_path)
        if kind == "sf" and result.soundfont_path: self.parent_window._activate_soundfont(result.soundfont_path, prepare=False)
        self.refresh_statuses()

    def install_fluidsynth(self) -> None: self._install("Install FluidSynth", install_fluidsynth, "fs")
    def install_soundfont(self) -> None: self._install("Install GeneralUser GS", install_recommended_soundfont, "sf")

    def choose_fluidsynth(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose fluidsynth.exe", str(self.parent_window._repo_root()), "FluidSynth (fluidsynth.exe);;Executables (*.exe);;All Files (*.*)")
        if path:
            check = check_fluidsynth(self.parent_window._repo_root(), path)
            if not check.confirmed: QMessageBox.warning(self, "FluidSynth", check.detail); return
            self.parent_window.settings.set("audio_driver_verified", False)
            self.parent_window.settings.set("fluidsynth_path", path); self.parent_window.player.set_fluidsynth_path(path); self.refresh_statuses()

    def choose_omr(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose Audiveris launcher", str(self.parent_window._repo_root()), "Audiveris (*.exe *.bat *.cmd *.jar);;All Files (*.*)")
        if not path: path = QFileDialog.getExistingDirectory(self, "Choose Audiveris installation folder", str(self.parent_window._repo_root()))
        if path: self.parent_window.settings.set("audiveris_path", path); self.refresh_statuses()

    def manage_libraries(self) -> None:
        dialog = InstrumentLibraryManagerDialog(self.parent_window); dialog.setStyleSheet(self.parent_window._stylesheet()); dialog.exec(); self.refresh_statuses()


class InstrumentPickerDialog(QDialog):
    def __init__(self, parent: QWidget, current_program: int = 0) -> None:
        super().__init__(parent)
        self.setWindowTitle("Choose Instrument")
        self.setMinimumWidth(420)
        self.selected_program = int(current_program or 0)
        self._all_items = [(i, f"{i + 1:03d} {name}") for i, name in enumerate(GM_PROGRAM_NAMES)]
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        title = QLabel("Search instruments")
        title.setObjectName("SectionTitle")
        layout.addWidget(title)
        self.current_label = QLabel(f"Current: {current_program + 1:03d} {GM_PROGRAM_NAMES[max(0, min(127, int(current_program or 0)))]}")
        self.current_label.setObjectName("SelectedInstrument")
        layout.addWidget(self.current_label)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Type piano, violin, bass, flute…")
        self.search.setToolTip("Search the full General MIDI instrument list by name or number.")
        layout.addWidget(self.search)
        self.list_widget = QListWidget()
        self.list_widget.setToolTip("The highlighted row is the instrument that will be chosen.")
        self.list_widget.setMaximumHeight(260)
        layout.addWidget(self.list_widget)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        choose = QPushButton("Choose")
        choose.setObjectName("PrimaryButton")
        choose.clicked.connect(self.accept_selected)
        buttons.addWidget(cancel)
        buttons.addWidget(choose)
        layout.addLayout(buttons)
        self.search.textChanged.connect(self.refilter)
        self.list_widget.itemDoubleClicked.connect(lambda _item: self.accept_selected())
        self.refilter("")
        self._select_program(current_program)

    def refilter(self, text: str) -> None:
        q = text.strip().lower()
        self.list_widget.clear()
        for program, label in self._all_items:
            if not q or q in label.lower():
                item = QListWidgetItem(label)
                item.setData(Qt.UserRole, program)
                self.list_widget.addItem(item)
        if self.list_widget.count():
            self.list_widget.setCurrentRow(0)

    def _select_program(self, program: int) -> None:
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if int(item.data(Qt.UserRole)) == int(program):
                self.list_widget.setCurrentRow(i)
                break

    def accept_selected(self) -> None:
        item = self.list_widget.currentItem()
        if item:
            self.selected_program = int(item.data(Qt.UserRole))
        self.accept()


class ExportOptionsDialog(QDialog):
    """Single export dialog for MIDI/WAV/MP3 export options."""

    def __init__(
        self,
        parent: "MainWindow",
        default_folder: str,
        default_name: str,
        checked_count: int,
        default_kind: str = "wav",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Export checked takes")
        self.setMinimumWidth(520)
        self.export_kind = default_kind if default_kind in {"midi", "wav", "mp3"} else "wav"
        self.folder = default_folder
        self.base_name = default_name

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        title = QLabel(f"Export {checked_count} checked take(s)")
        title.setObjectName("SectionTitle")
        layout.addWidget(title)

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)

        type_label = QLabel("File type")
        type_label.setObjectName("ControlLabel")
        self.type_combo = QComboBox()
        self.type_combo.addItem("MIDI (.mid)", "midi")
        self.type_combo.addItem("WAV audio (.wav)", "wav")
        self.type_combo.addItem("MP3 audio (.mp3)", "mp3")
        idx = self.type_combo.findData(self.export_kind)
        self.type_combo.setCurrentIndex(idx if idx >= 0 else 1)
        self.type_combo.setToolTip("Choose the export format for the checked takes. MIDI Performer remembers the last type you exported.")
        grid.addWidget(type_label, 0, 0)
        grid.addWidget(self.type_combo, 0, 1, 1, 2)

        folder_label = QLabel("Folder")
        folder_label.setObjectName("ControlLabel")
        self.folder_edit = QLineEdit(default_folder)
        self.folder_edit.setPlaceholderText("Choose an export folder")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self.choose_folder)
        grid.addWidget(folder_label, 1, 0)
        grid.addWidget(self.folder_edit, 1, 1)
        grid.addWidget(browse, 1, 2)

        name_label = QLabel("Base name")
        name_label.setObjectName("ControlLabel")
        self.name_edit = QLineEdit(default_name)
        self.name_edit.setPlaceholderText("Example: maple_leaf_exports")
        self.name_edit.setToolTip("MIDI Performer will append each take number and file extension.")
        grid.addWidget(name_label, 2, 0)
        grid.addWidget(self.name_edit, 2, 1, 1, 2)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)

        self.example_label = QLabel("")
        self.example_label.setObjectName("SubtleText")
        self.example_label.setWordWrap(True)
        layout.addWidget(self.example_label)
        self.type_combo.currentIndexChanged.connect(self.update_example)
        self.name_edit.textChanged.connect(lambda _text: self.update_example())
        self.update_example()

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        export = QPushButton("Export")
        export.setObjectName("ExportButton")
        export.clicked.connect(self.accept_export)
        buttons.addWidget(cancel)
        buttons.addWidget(export)
        layout.addLayout(buttons)

    def choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose export folder", self.folder_edit.text().strip() or self.folder)
        if folder:
            self.folder_edit.setText(folder)

    def update_example(self) -> None:
        kind = str(self.type_combo.currentData() or "wav")
        extension = {"midi": "mid", "wav": "wav", "mp3": "mp3"}.get(kind, kind)
        base = self.name_edit.text().strip() or "midi_performer_export"
        self.example_label.setText(f"Example file: {base}_1.{extension} · plus midi_performer_export_manifest.txt")

    def accept_export(self) -> None:
        folder = self.folder_edit.text().strip()
        name = self.name_edit.text().strip()
        if not folder:
            QMessageBox.information(self, "Export", "Choose an export folder first.")
            return
        if not name:
            QMessageBox.information(self, "Export", "Enter a base name for the exported files.")
            return
        self.export_kind = str(self.type_combo.currentData() or "wav")
        self.folder = folder
        self.base_name = name
        self.accept()


class ExportProgressDialog(QDialog):
    """Small non-blocking export status dialog."""

    def __init__(self, parent: "MainWindow", message: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("Exporting")
        self.setMinimumWidth(420)
        self.setModal(False)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        self.message_label = QLabel(message)
        self.message_label.setObjectName("FeedbackText")
        self.message_label.setWordWrap(True)
        layout.addWidget(self.message_label)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        layout.addWidget(self.progress)
        note = QLabel("Rendering can take a moment for longer MIDI files. MIDI Performer will show the export result when finished.")
        note.setObjectName("SubtleText")
        note.setWordWrap(True)
        layout.addWidget(note)

    def set_message(self, message: str) -> None:
        self.message_label.setText(message)


class SetupStatusDialog(QDialog):
    """Readiness checklist for playback and export."""

    def __init__(self, parent: "MainWindow") -> None:
        super().__init__(parent)
        self.parent_window = parent
        self.setWindowTitle("Setup / Export Status")
        self.setMinimumWidth(620)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        layout.addWidget(parent._section_title("Setup / Export Status"))
        intro = QLabel("Use this before beta testing: it verifies MIDI, SoundFont, FluidSynth, FFmpeg, Audiveris OMR, and export readiness.")
        intro.setObjectName("SubtleText")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        report = parent._setup_status_report()
        self._audiveris_ready = any(str(item.get("name")) == "Audiveris OMR" and bool(item.get("ready")) for item in report)
        self._audio_ready = all(
            any(str(item.get("name")) == name and bool(item.get("ready")) for item in report)
            for name in ("SoundFont", "FluidSynth")
        )
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        for row, item in enumerate(report):
            state = str(item.get("state") or ("ready" if item.get("ready") else "attention"))
            label = str(item.get("label") or {"ready": "Ready", "waiting": "Waiting", "optional": "Optional"}.get(state, "Needs setup"))
            object_name = {
                "ready": "ReadyPill",
                "waiting": "WaitingPill",
                "optional": "OptionalPill",
                "attention": "WarningPill",
            }.get(state, "WarningPill")
            status = QLabel(label)
            status.setObjectName(object_name)
            status.setAlignment(Qt.AlignCenter)
            status.setFixedWidth(118)
            status.setMaximumHeight(26)
            name = QLabel(str(item.get("name") or ""))
            name.setObjectName("ControlLabelCompact")
            detail = QLabel(str(item.get("detail") or ""))
            detail.setObjectName("DialogText")
            detail.setWordWrap(True)
            grid.addWidget(status, row, 0, alignment=Qt.AlignTop)
            grid.addWidget(name, row, 1, alignment=Qt.AlignTop)
            grid.addWidget(detail, row, 2)
        grid.setColumnMinimumWidth(0, 122)
        grid.setColumnStretch(2, 1)
        layout.addLayout(grid)

        summary = QLabel(parent._setup_status_summary(report))
        summary.setObjectName("FeedbackText")
        summary.setWordWrap(True)
        layout.addWidget(summary)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        install = None
        if not self._audiveris_ready:
            install = QPushButton("Install Audiveris…")
            install.setObjectName("ToolButton")
            install.setToolTip("Install Audiveris with Windows winget, then verify dependencies.")
            install.clicked.connect(self.install_audiveris)
        setup_audio = None
        if not self._audio_ready:
            setup_audio = QPushButton("Complete Playback Setup…")
            setup_audio.setObjectName("PrimaryButton")
            setup_audio.setToolTip("Install or select FluidSynth and an instrument library.")
            setup_audio.clicked.connect(self.setup_audio)
        test_audio = QPushButton("Test Playback…")
        test_audio.setObjectName("ToolButton")
        test_audio.setToolTip("Play a direct FluidSynth chord and identify a working Windows audio driver.")
        test_audio.clicked.connect(self.test_audio)
        verify = QPushButton("Verify Again")
        verify.setObjectName("ToolButton")
        verify.clicked.connect(self.verify_again)
        settings = QPushButton("File Locations…")
        settings.setObjectName("ToolButton")
        settings.clicked.connect(self.open_settings)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        if setup_audio is not None:
            buttons.addWidget(setup_audio)
        if install is not None:
            buttons.addWidget(install)
        test_audio.setEnabled(self._audio_ready)
        buttons.addWidget(test_audio)
        buttons.addWidget(verify)
        buttons.addWidget(settings)
        buttons.addWidget(close)
        layout.addLayout(buttons)

    def setup_audio(self) -> None:
        self.accept()
        dialog = FirstRunSetupDialog(self.parent_window)
        dialog.setStyleSheet(self.parent_window._stylesheet())
        dialog.exec()

    def install_audiveris(self) -> None:
        self.accept()
        self.parent_window.install_audiveris_engine()

    def test_audio(self) -> None:
        # Keep the readiness dialog open. The test itself provides visible feedback.
        self.parent_window.run_audio_diagnostics(self)

    def verify_again(self) -> None:
        self.accept()
        self.parent_window.show_setup_status()

    def open_settings(self) -> None:
        self.accept()
        self.parent_window.show_settings()


class MixerDialog(QDialog):
    def __init__(self, parent: "MainWindow") -> None:
        super().__init__(parent)
        self.parent_window = parent
        self.setWindowTitle("Track Mixer")
        self.setMinimumWidth(560)
        self.setMaximumWidth(1220)
        self.setMaximumHeight(560)
        self._mixer_scroll_height = 220
        self.instrument_labels: dict[str, QPushButton] = {}
        self.programs: dict[str, int | None] = {}
        self.volume_sliders: dict[str, QSlider] = {}
        self.pan_sliders: dict[str, QSlider] = {}
        self.octave_controls: dict[str, QComboBox] = {}
        self.volume_labels: dict[str, QLabel] = {}
        self.pan_labels: dict[str, QLabel] = {}
        self.solo_checks: dict[str, QCheckBox] = {}
        self.mute_checks: dict[str, QCheckBox] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)
        layout.addWidget(parent._section_title("Track Mixer"))
        note = QLabel("Compact track strips. Apply saves changes for versions/export; octave changes restart the live preview when needed.")
        note.setObjectName("SubtleText")
        note.setWordWrap(False)
        note.setMaximumHeight(22)
        layout.addWidget(note)

        infos = parent._music_track_infos() if parent.project.source_midi_path else []
        self._apply_dynamic_size(len(infos))
        if parent.project.source_midi_path:
            reference = QLabel(self._mixer_reference_text(infos))
            reference.setObjectName("MixerReference")
            reference.setWordWrap(False)
            reference.setToolTip(self._mixer_reference_tooltip())
            layout.addWidget(reference)
        if not infos:
            empty = QLabel("No playable music tracks found. This MIDI may only contain control/meta data.")
            empty.setObjectName("SubtleText")
            layout.addWidget(empty)
        else:
            scroll = QScrollArea()
            scroll.setObjectName("MixerScroll")
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            scroll.setFixedHeight(self._mixer_scroll_height)
            scroll.viewport().setStyleSheet("background: #ffffff;")
            strips = QWidget()
            strips.setObjectName("MixerStripRow")
            strips.setStyleSheet("background: #ffffff;")
            row = QHBoxLayout(strips)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(5)
            for display_index, info in enumerate(infos, start=1):
                row.addWidget(self._track_strip(info, display_index))
            row.addStretch(1)
            scroll.setWidget(strips)
            layout.addWidget(scroll)

        btns = QHBoxLayout()
        reset = QPushButton("Reset")
        reset.clicked.connect(self.reset_loaded)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        apply = QPushButton("Apply")
        apply.setObjectName("PrimaryButton")
        apply.clicked.connect(self.apply_mixer)
        btns.addWidget(reset)
        btns.addStretch(1)
        btns.addWidget(cancel)
        btns.addWidget(apply)
        layout.addLayout(btns)

    def _track_strip(self, info: Any, display_index: int) -> QFrame:
        key = str(info.index)
        loaded_program = int(info.programs[0]) if info.programs else 0
        current_program = self.parent_window.project.track_program_overrides.get(key, loaded_program)
        current_program = loaded_program if current_program is None else int(current_program)
        self.programs[key] = current_program

        frame = QFrame()
        frame.setObjectName("TrackStrip")
        frame.setMinimumWidth(172)
        frame.setMaximumWidth(190)
        frame.setMaximumHeight(164)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(7, 4, 7, 4)
        layout.setSpacing(3)
        title = QLabel(f"Track {display_index}")
        title.setObjectName("MiniTitle")
        title.setToolTip(f"Visible music track {display_index}; source MIDI track {info.index + 1}.")
        layout.addWidget(title)
        meta = QLabel(self._track_meta_text(info))
        meta.setObjectName("TrackMeta")
        meta.setWordWrap(False)
        meta.setToolTip(self._track_reference_tooltip(info, display_index))
        layout.addWidget(meta)
        inst_label = QPushButton(self._program_button_text(current_program))
        self._style_instrument_label(inst_label, current_program != loaded_program)
        inst_label.setToolTip("Choose instrument")
        inst_label.clicked.connect(lambda _checked=False, k=key: self.change_instrument(k))
        inst_label.setMinimumHeight(28)
        inst_label.setMaximumHeight(30)
        self.instrument_labels[key] = inst_label
        layout.addWidget(inst_label)

        toggle_row = QHBoxLayout()
        toggle_row.setSpacing(7)
        solo = QCheckBox("S")
        solo.setToolTip("Solo this track")
        solo.setFixedWidth(38)
        solo.setChecked(bool(self.parent_window.project.track_solo_overrides.get(key, False)))
        mute = QCheckBox("M")
        mute.setToolTip("Mute this track")
        mute.setFixedWidth(38)
        mute.setChecked(bool(self.parent_window.project.track_mute_overrides.get(key, False)))
        solo.stateChanged.connect(lambda _state=0: self.preview_live_mixer())
        mute.stateChanged.connect(lambda _state=0: self.preview_live_mixer())
        self.solo_checks[key] = solo
        self.mute_checks[key] = mute
        toggle_row.addWidget(solo)
        toggle_row.addWidget(mute)

        octave_label = QLabel("Oct")
        octave_label.setObjectName("TableHeader")
        octave_label.setToolTip("Track octave shift. Apply restarts the live preview when needed.")
        octave_label.setFixedWidth(24)
        toggle_row.addWidget(octave_label)
        octave = QComboBox()
        octave.setObjectName("MiniCombo")
        octave.setFixedWidth(48)
        for value in (-2, -1, 0, 1, 2):
            octave.addItem("0" if value == 0 else (f"+{value}" if value > 0 else str(value)), value)
        current_octave = int(self.parent_window.project.track_octave_overrides.get(key, 0) or 0)
        current_octave = max(-2, min(2, current_octave))
        octave.setCurrentIndex(max(0, octave.findData(current_octave)))
        octave.setToolTip("Octave shift for this track. 0 = unchanged; +1/-1 = one octave.")
        octave.currentIndexChanged.connect(lambda _idx=0: self.preview_live_mixer())
        self.octave_controls[key] = octave
        toggle_row.addWidget(octave)
        toggle_row.addStretch(1)
        layout.addLayout(toggle_row)

        vol = QSlider(Qt.Horizontal)
        vol.setRange(0, 127)
        vol.setValue(int(self.parent_window.project.track_volume_overrides.get(key, 100)))
        vol.setFixedWidth(94)
        vol.setToolTip("Track volume override (MIDI CC7). 100 means no mixer override; this is separate from the main master volume.")
        vol_label = QLabel(str(vol.value()))
        vol_label.setObjectName("ValueLabelMini")
        vol_label.setToolTip("Track volume override. Default 100 = unchanged from mixer.")
        vol_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        vol.valueChanged.connect(lambda value, label=vol_label: label.setText(str(value)))
        vol.valueChanged.connect(lambda _value=0: self.preview_live_mixer())
        self.volume_sliders[key] = vol
        self.volume_labels[key] = vol_label
        layout.addLayout(self._mini_slider_row("Vol", vol, vol_label))

        pan = QSlider(Qt.Horizontal)
        pan.setRange(-64, 63)
        pan.setValue(int(self.parent_window.project.track_pan_overrides.get(key, 64)) - 64)
        pan.setFixedWidth(94)
        pan.setToolTip("Track pan/balance. C means centered.")
        pan_label = QLabel(self._balance_text(pan.value()))
        pan_label.setObjectName("ValueLabelMini")
        pan_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        pan.valueChanged.connect(lambda value, label=pan_label: label.setText(self._balance_text(value)))
        pan.valueChanged.connect(lambda _value=0: self.preview_live_mixer())
        self.pan_sliders[key] = pan
        self.pan_labels[key] = pan_label
        layout.addLayout(self._mini_slider_row("Bal", pan, pan_label))
        return frame

    def _mini_slider_row(self, title: str, slider: QSlider, value_label: QLabel) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(4)
        label = QLabel(title)
        label.setObjectName("TableHeader")
        label.setFixedWidth(24)
        row.addWidget(label)
        row.addWidget(slider)
        row.addWidget(value_label)
        return row

    def _mini_fader(self, title: str, slider: QSlider, value_label: QLabel) -> QVBoxLayout:
        box = QVBoxLayout()
        label = QLabel(title)
        label.setObjectName("TableHeader")
        label.setAlignment(Qt.AlignCenter)
        box.addWidget(label)
        box.addWidget(slider, alignment=Qt.AlignHCenter)
        box.addWidget(value_label)
        return box

    def _program_name(self, program: int) -> str:
        program = max(0, min(127, int(program)))
        return f"{program + 1:03d} {GM_PROGRAM_NAMES[program]}"

    def _program_button_text(self, program: int) -> str:
        name = self._program_name(program)
        if len(name) > 26:
            name = name[:25].rstrip() + "…"
        return f"{name}  ▾"

    def _style_instrument_label(self, label: QWidget, changed: bool) -> None:
        label.setObjectName("InstrumentButtonActive" if changed else "InstrumentButton")
        label.style().unpolish(label)
        label.style().polish(label)
        label.update()

    def _apply_dynamic_size(self, track_count: int) -> None:
        count = max(1, int(track_count or 0))
        # Size the dialog to the actual track count so small MIDIs do not open
        # inside a giant empty scroll well, while 10+ track files still scroll.
        width = max(620, min(1160, 64 + count * 194))
        if count <= 2:
            self._mixer_scroll_height = 154
            height = 298
        elif count <= 5:
            self._mixer_scroll_height = 176
            height = 322
        else:
            self._mixer_scroll_height = 212
            height = 364
        self.resize(width, height)

    def _mixer_reference_text(self, infos: list[Any]) -> str:
        all_infos = self.parent_window._track_infos() if self.parent_window.project.source_midi_path else []
        hidden = max(0, len(all_infos) - len(infos))
        file_name = Path(self.parent_window.project.source_midi_path or "MIDI").name
        hidden_text = f" · {hidden} control track(s) hidden" if hidden else ""
        return f"File: {file_name} · {len(infos)} playable track(s){hidden_text}"

    def _mixer_reference_tooltip(self) -> str:
        lines = []
        for info in self.parent_window._track_infos():
            name = (info.name or f"Source track {info.index + 1}").strip()
            role = "music" if getattr(info, "is_playable", False) else "control/meta"
            ch = "Ch " + ",".join(str(c + 1) for c in info.channels) if info.channels else "No channel"
            lines.append(f"Source track {info.index + 1}: {name} · {role} · {ch} · {info.note_count} notes")
        return "\n".join(lines) if lines else "No MIDI track reference available."

    def _track_meta_text(self, info: Any) -> str:
        ch = "Ch " + ",".join(str(c + 1) for c in info.channels) if info.channels else "No ch"
        return f"Src {info.index + 1} · {ch} · {info.note_count} notes"

    def _track_reference_tooltip(self, info: Any, display_index: int) -> str:
        name = (info.name or f"Source track {info.index + 1}").strip()
        programs = ", ".join(self._program_name(int(p)) for p in info.programs) if info.programs else "Loaded MIDI default"
        return f"Visible track {display_index} maps to source track {info.index + 1}.\nMIDI description: {name}\nLoaded program(s): {programs}"

    def _balance_text(self, value: int) -> str:
        value = int(value)
        if value == 0:
            return "C"
        return f"L{abs(value)}" if value < 0 else f"R{value}"

    def change_instrument(self, key: str) -> None:
        current = int(self.programs.get(key) or 0)
        dialog = InstrumentPickerDialog(self, current)
        dialog.setStyleSheet(self.styleSheet())
        if dialog.exec():
            self.programs[key] = dialog.selected_program
            self.instrument_labels[key].setText(self._program_button_text(dialog.selected_program))
            loaded = self._loaded_program_for_key(key)
            self._style_instrument_label(self.instrument_labels[key], dialog.selected_program != loaded)
            self.preview_live_mixer()

    def _loaded_program_for_key(self, key: str) -> int:
        infos = self.parent_window._music_track_infos() if self.parent_window.project.source_midi_path else []
        for info in infos:
            if str(info.index) == str(key):
                return int(info.programs[0]) if info.programs else 0
        return 0

    def reset_loaded(self) -> None:
        infos = self.parent_window._music_track_infos() if self.parent_window.project.source_midi_path else []
        for info in infos:
            key = str(info.index)
            loaded = int(info.programs[0]) if info.programs else 0
            self.programs[key] = loaded
            if key in self.instrument_labels:
                self.instrument_labels[key].setText(self._program_button_text(loaded))
                self._style_instrument_label(self.instrument_labels[key], False)
            if key in self.volume_sliders:
                self.volume_sliders[key].setValue(100)
            if key in self.pan_sliders:
                self.pan_sliders[key].setValue(0)
            if key in self.octave_controls:
                idx = self.octave_controls[key].findData(0)
                self.octave_controls[key].setCurrentIndex(max(0, idx))
            if key in self.solo_checks:
                self.solo_checks[key].setChecked(False)
            if key in self.mute_checks:
                self.mute_checks[key].setChecked(False)

    def _current_active_maps(self) -> tuple[dict[str, int], dict[str, int], dict[str, int], dict[str, bool], dict[str, bool], dict[str, int]]:
        infos = self.parent_window._music_track_infos() if self.parent_window.project.source_midi_path else []
        loaded_by_key = {str(info.index): (int(info.programs[0]) if info.programs else 0) for info in infos}
        programs = {
            k: int(v) for k, v in self.programs.items()
            if v is not None and int(v) != int(loaded_by_key.get(k, 0))
        }
        volumes = {k: int(v.value()) for k, v in self.volume_sliders.items() if int(v.value()) != 100}
        pans = {k: int(v.value()) + 64 for k, v in self.pan_sliders.items() if int(v.value()) != 0}
        solos = {k: True for k, v in self.solo_checks.items() if v.isChecked()}
        mutes = {k: True for k, v in self.mute_checks.items() if v.isChecked()}
        octaves = {k: int(v.currentData() or 0) for k, v in self.octave_controls.items() if int(v.currentData() or 0) != 0}
        return programs, volumes, pans, solos, mutes, octaves

    def preview_live_mixer(self) -> None:
        programs, volumes, pans, solos, mutes, octaves = self._current_active_maps()
        self.parent_window._apply_live_mixer_maps(programs, volumes, pans, solos, mutes)
        if octaves and (self.parent_window.playback_engine.is_playing() or self.parent_window.playback_engine.is_paused()):
            self.parent_window.status_label.setText("Octave shift will restart the preview when you click Apply")

    def reject(self) -> None:  # type: ignore[override]
        self.parent_window._apply_project_mixer_live()
        super().reject()

    def apply_mixer(self) -> None:
        programs, volumes, pans, solos, mutes, octaves = self._current_active_maps()
        was_playing = self.parent_window.playback_engine.is_playing()
        active_key = str(self.parent_window.playback_engine.version_key or "")
        prior_context = self.parent_window._playback_mixer_contexts.get(active_key, {})
        prior_octaves = dict((prior_context or {}).get("octaves", {}) or {})
        octave_changed_for_live = bool(octaves or prior_octaves or active_key == "__mixer_preview__")

        self.parent_window.project.track_program_overrides = programs
        self.parent_window.project.track_volume_overrides = volumes
        self.parent_window.project.track_pan_overrides = pans
        self.parent_window.project.track_solo_overrides = solos
        self.parent_window.project.track_mute_overrides = mutes
        self.parent_window.project.track_octave_overrides = octaves
        self.parent_window._mixer_revision += 1

        restarted = False
        if was_playing and octave_changed_for_live:
            restarted = self.parent_window._restart_live_playback_with_project_mixer()
        if not restarted:
            self.parent_window._apply_project_mixer_live()

        self.parent_window._mark_variation_changed("Mixer saved — pending TAKE updated for play/export.")
        self.parent_window.status_label.setText("Mixer applied — live preview updated" if restarted else "Mixer applied")
        self.parent_window._refresh_all()
        self.accept()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"{APP_TITLE} {APP_VERSION_DISPLAY}")
        self.resize(1060, 720)
        self.setMinimumSize(860, 560)
        self.project = MidiPerformerProject()
        self.settings = AppSettings(self._repo_root())
        self.player = FluidSynthPlayer(self._repo_root(), self.settings.get("fluidsynth_path"))
        self.playback_engine = PlaybackEngine(
            self.player,
            prepare_midi=lambda source, label, version_key: self._prepared_midi_for_live_playback(source, label, str(version_key)),
            duration_seconds=lambda source: summarize_midi(source).length_seconds,
        )
        self.playback_engine.set_audio_driver(self.settings.get("audio_driver", "default"))
        self.selected_review_kind = "original"  # original | candidate | take
        self.selected_take_id: str | None = None
        self.export_checked_keys: set[str] = set()
        self.create_level_key = "minimal"
        self.expression_label = "None"
        self._summary_cache_path: str | None = None
        self._summary_cache: Any | None = None
        self._track_info_cache_path: str | None = None
        self._track_info_cache: list[Any] | None = None
        self._variation_dirty = False
        self._last_audition_signature: str | None = None
        # One seed belongs to the current pending TAKE. Alignment-dialog playback
        # and the later numbered take therefore render the same interpretation.
        self._pending_variation_seed: int | None = None
        self._mixer_revision = 0
        self._playback_mixer_contexts: dict[str, dict[str, Any]] = {}
        self._active_playback_tempo_percent = int(self.project.playback_tempo_percent or 100)
        self._playback_ready = False
        self.show_favorites_only = False
        self._playback_preparing = False
        self._playback_prepare_error: str | None = None
        self._prepare_generation = 0
        self._prepare_thread: threading.Thread | None = None
        self._prepare_result: dict[str, Any] | None = None
        self._spinner_index = 0
        self._spinner_frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        self.playback_timer = QTimer(self)
        self.playback_timer.setInterval(250)
        self.playback_timer.timeout.connect(self._poll_playback_state)
        self.prepare_poll_timer = QTimer(self)
        self.prepare_poll_timer.setInterval(80)
        self.prepare_poll_timer.timeout.connect(self._poll_playback_prepare)
        self.spinner_timer = QTimer(self)
        self.spinner_timer.setInterval(120)
        self.spinner_timer.timeout.connect(self._animate_preparing_playback)
        self._export_busy = False
        self._export_result: dict[str, Any] | None = None
        self._export_thread: threading.Thread | None = None
        self._export_progress_dialog: ExportProgressDialog | None = None
        self._export_spinner_index = 0
        self.export_poll_timer = QTimer(self)
        self.export_poll_timer.setInterval(150)
        self.export_poll_timer.timeout.connect(self._poll_export_job)
        self._score_analysis_busy = False
        self._score_analysis_thread: threading.Thread | None = None
        self._score_analysis_result: dict[str, Any] | None = None
        self._score_analysis_spinner_index = 0
        self.score_analysis_poll_timer = QTimer(self)
        self.score_analysis_poll_timer.setInterval(150)
        self.score_analysis_poll_timer.timeout.connect(self._poll_score_analysis_job)
        self._audio_analysis_busy = False
        self._audio_analysis_thread: threading.Thread | None = None
        self._audio_analysis_result: dict[str, Any] | None = None
        self._audio_analysis_spinner_index = 0
        self.audio_analysis_poll_timer = QTimer(self)
        self.audio_analysis_poll_timer.setInterval(150)
        self.audio_analysis_poll_timer.timeout.connect(self._poll_audio_analysis_job)
        self._dependency_install_busy = False
        self._dependency_install_result: dict[str, Any] | None = None
        self._dependency_install_thread: threading.Thread | None = None
        self._dependency_install_dialog: ExportProgressDialog | None = None
        self._dependency_spinner_index = 0
        self.dependency_poll_timer = QTimer(self)
        self.dependency_poll_timer.setInterval(150)
        self.dependency_poll_timer.timeout.connect(self._poll_dependency_install_job)
        self._autoload_soundfont()
        saved_volume = self.settings.get("master_volume")
        if saved_volume is not None:
            self.project.master_volume = normalize_volume_percent(saved_volume)
        saved_tempo = self.settings.get("playback_tempo_percent")
        if isinstance(saved_tempo, int):
            self.project.playback_tempo_percent = saved_tempo
        self.project.output_directory = self.settings.get("last_output_directory") or self.project.output_directory
        self.project.project_directory = self.settings.get("last_project_directory") or self.project.project_directory
        self._init_widgets()
        self._connect_signals()
        self._build_menu()
        self.setCentralWidget(self._build_ui())
        self._refresh_all()
        # Headless/offscreen runs are automated verification, not a real first
        # launch. Avoid leaving modal setup dialogs behind during the test suite.
        if str(os.environ.get("QT_QPA_PLATFORM", "")).lower() != "offscreen":
            QTimer.singleShot(500, self._offer_first_run_audio_setup)

    def _repo_root(self) -> Path:
        return Path(__file__).resolve().parents[1]

    def _work_versions_dir(self) -> Path:
        base = Path(self.project.output_directory) if self.project.output_directory else self._repo_root() / "projects" / "working"
        target = base / "versions"
        target.mkdir(parents=True, exist_ok=True)
        return target

    def _autoload_soundfont(self) -> None:
        saved = self.settings.get("last_soundfont_path")
        if saved and Path(saved).exists():
            self.project.selected_soundfont_path = str(saved)
            return
        sf_dir = self._repo_root() / "assets" / "soundfonts"
        candidates = sorted([*sf_dir.glob("*.sf3"), *sf_dir.glob("*.sf2")])
        if candidates:
            self.project.selected_soundfont_path = str(candidates[0])
            self.settings.set("last_soundfont_path", str(candidates[0]))
            self.settings.set("last_soundfont_directory", str(candidates[0].parent))

    def _activate_soundfont(self, path: str, *, prepare: bool = True) -> None:
        candidate = Path(path)
        if not candidate.is_file() or candidate.suffix.lower() not in {".sf2", ".sf3"}:
            return
        previous = str(self.project.selected_soundfont_path or "")
        if previous and Path(previous) != candidate:
            self.settings.set("audio_driver_verified", False)
        self.project.selected_soundfont_path = str(candidate)
        self.settings.set("last_soundfont_path", str(candidate))
        self.settings.set("last_soundfont_directory", str(candidate.parent))
        self._remember_recent_path("recent_soundfont_paths", str(candidate))
        if hasattr(self, "soundfont_file_combo"):
            self._invalidate_playback_ready("Instrument library changed.")
            self._refresh_all()
        if prepare and self.project.source_midi_path:
            self._begin_playback_prepare(status_prefix=f"Instrument library selected: {candidate.name} · ")

    def _master_volume_percent(self) -> int:
        return normalize_volume_percent(getattr(self.project, "master_volume", 100))

    def _take_volume_percent(self, item: dict[str, Any] | None, default: int | None = None) -> int:
        fallback = self._master_volume_percent() if default is None else int(default)
        if not item:
            return fallback
        return normalize_volume_percent(item.get("volume_percent", fallback), default=fallback)

    def _sync_volume_widget_from_project(self) -> None:
        if not hasattr(self, "volume_slider"):
            return
        value = self._master_volume_percent()
        self.volume_slider.blockSignals(True)
        self.volume_slider.setValue(value)
        self.volume_slider.blockSignals(False)
        self.volume_label.setText(f"{value}%")

    def _init_widgets(self) -> None:
        self.midi_file_combo = QComboBox()
        self.midi_file_combo.setMinimumWidth(140)
        self.midi_file_combo.setMaximumWidth(230)
        self.soundfont_file_combo = QComboBox()
        self.soundfont_file_combo.setMinimumWidth(140)
        self.soundfont_file_combo.setMaximumWidth(230)
        self.midi_info_label = QLabel("Open a MIDI file to begin.")
        self.midi_info_label.setObjectName("SubtleText")
        self.midi_info_label.setWordWrap(False)
        self.midi_info_label.setMinimumHeight(20)
        self.midi_info_label.setMaximumHeight(24)
        self.midi_info_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.prepare_status_label = QLabel("")
        self.prepare_status_label.setObjectName("PreparingStatus")
        self.prepare_status_label.setVisible(False)
        self.prepare_status_label.setMinimumHeight(20)
        self.prepare_progress = QProgressBar()
        self.prepare_progress.setRange(0, 0)
        self.prepare_progress.setTextVisible(False)
        self.prepare_progress.setFixedHeight(8)
        self.prepare_progress.setMaximumWidth(180)
        self.prepare_progress.setVisible(False)
        self.open_midi_btn = QPushButton("Open MIDI")
        self.soundfont_btn = QPushButton("Instrument Libraries")
        self.setup_status_btn = QPushButton("Setup")
        self.setup_status_btn.setObjectName("ToolButton")
        self.setup_status_btn.setToolTip("Check the instrument library, playback engine, score analysis, and export readiness.")
        self.open_midi_btn.setObjectName("PrimaryButton")
        self.status_label = QLabel("Load a MIDI to begin.")
        self.status_label.setObjectName("StatusLine")
        self.playback_status_label = QLabel("Load MIDI to play")
        self.playback_status_label.setObjectName("CompactStatus")
        self.transport_target_label = QLabel("Transport plays: Original")
        self.transport_target_label.setObjectName("SubtleText")

        self.rewind_btn = QPushButton("Rew")
        self.preview_btn = QPushButton("Play")
        self.stop_btn = QPushButton("Stop")
        self.ff_btn = QPushButton("FF")
        self.stop_btn.setObjectName("StopButton")
        self.preview_btn.setObjectName("PlayButton")
        self.position_slider = QSlider(Qt.Horizontal)
        self.position_slider.setRange(0, 99)
        self.position_slider.setValue(0)
        self.position_label = QLabel("0%")
        self.position_label.setObjectName("ValueLabel")
        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setRange(0, 200)
        self.volume_slider.setValue(self._master_volume_percent())
        self.volume_slider.setFixedWidth(104)
        self.volume_slider.setToolTip("Master playback volume. 100% is neutral for the loaded MIDI and instrument library; lower values reduce output and values above 100% boost.")
        self.volume_label = QLabel(f"{self._master_volume_percent()}%")
        self.volume_label.setObjectName("ValueLabel")
        self.volume_label.setToolTip("100% = neutral app playback. Mixer track volume is separate.")
        self.tempo_slider = QSlider(Qt.Horizontal)
        self.tempo_slider.setRange(50, 150)
        self.tempo_slider.setValue(int(self.project.playback_tempo_percent or 100))
        self.tempo_slider.setFixedWidth(104)
        self.tempo_label = QLabel(f"{int(self.project.playback_tempo_percent or 100)}%")
        self.tempo_label.setObjectName("ValueLabelWide")
        self.reset_volume_btn = QPushButton("Reset")
        self.reset_volume_btn.setObjectName("MiniButton")
        self.reset_volume_btn.setToolTip("Reset master playback volume to neutral 100%.")
        self.reset_volume_btn.setFixedWidth(52)
        self.reset_tempo_btn = QPushButton("Reset")
        self.reset_tempo_btn.setObjectName("MiniButton")
        self.reset_tempo_btn.setFixedWidth(52)

        self.level_group = QButtonGroup(self)
        self.level_group.setExclusive(True)
        self.level_buttons: dict[str, QPushButton] = {}
        for key, label in LEVELS:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setMinimumWidth(52)
            btn.setToolTip(LEVEL_TOOLTIPS.get(key, "Choose how much interpretation MIDI Performer should add."))
            self.level_group.addButton(btn)
            self.level_buttons[key] = btn
        for key, btn in self.level_buttons.items():
            btn.setObjectName("ChoicePill")
            btn.setFixedWidth({"minimal": 54, "moderate": 62, "enhanced": 70}.get(key, 58))
        self.level_buttons["minimal"].setChecked(True)

        self.expression_group = QButtonGroup(self)
        self.expression_group.setExclusive(True)
        self.expression_buttons: dict[str, QPushButton] = {}
        for label, _value in EXPRESSION_LEVELS:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setMinimumWidth(46)
            btn.setToolTip(EXPRESSION_TOOLTIPS.get(label, "Choose how much dynamic expression to add."))
            self.expression_group.addButton(btn)
            self.expression_buttons[label] = btn
        for label, btn in self.expression_buttons.items():
            btn.setObjectName("ChoicePill")
            btn.setFixedWidth({"None": 48, "Light": 48, "Medium": 62, "Strong": 58, "Very Strong": 52}.get(label, 56))
        self.expression_buttons["Very Strong"].setText("Max")
        self.expression_buttons["Very Strong"].setToolTip("Maximum dynamic contrast (Very Strong).")
        self.expression_buttons["None"].setChecked(True)

        self.timing_group = QButtonGroup(self)
        self.timing_group.setExclusive(True)
        self.timing_buttons: dict[str, QPushButton] = {}
        for label in TIMING_FEELS:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setMinimumWidth(64)
            btn.setToolTip(TIMING_TOOLTIPS.get(label, "Choose the timing feel for the audition."))
            self.timing_group.addButton(btn)
            self.timing_buttons[label] = btn
        for label, btn in self.timing_buttons.items():
            btn.setObjectName("ChoicePill")
            btn.setFixedWidth({"Straight": 62, "Natural": 60, "Lilt": 44, "Laid Back": 68}.get(label, 58))
        self.timing_buttons["Natural"].setChecked(True)

        self.create_preview_btn = QPushButton("Audition")
        self.create_preview_btn.setObjectName("PrimaryButton")
        self.create_preview_btn.setToolTip("Create a numbered preview from the selected variation settings and play it immediately.")
        self.create_preview_btn.setVisible(False)
        self.play_preview_btn = QPushButton("Play Preview")
        self.save_take_btn = QPushButton("Save")
        self.save_take_btn.setObjectName("ExportButton")
        self.save_take_btn.setToolTip("Legacy preview save control; numbered rows are now saved automatically.")
        self.candidate_status_label = QLabel("")
        self.candidate_status_label.setObjectName("FeedbackText")
        self.candidate_status_label.setWordWrap(False)
        self.candidate_status_label.setVisible(False)
        self.preview_slot_title = QLabel("Preview workspace")
        self.preview_slot_title.setObjectName("MiniTitle")
        self.preview_slot_summary = QLabel("Choose settings, create a preview, then save it to Takes if it works.")
        self.preview_slot_summary.setObjectName("FeedbackText")
        self.preview_slot_summary.setWordWrap(True)
        self.preview_slot_details = QLabel("No preview created yet.")
        self.preview_slot_details.setObjectName("SubtleText")
        self.preview_slot_details.setWordWrap(True)

        self.mixer_btn = QPushButton("Track Mixer")
        self.mixer_btn.setObjectName("ToolButton")
        self.mixer_btn.setToolTip("Open while playback is running to adjust track volume, balance, solo, mute, and instruments live.")
        self.tool_note_label = QLabel("Instruments, volume, pan, solo/mute.")
        self.tool_note_label.setObjectName("SubtleText")
        self.tool_note_label.setWordWrap(False)
        self.mixer_summary_label = QLabel("Open a MIDI to view tracks.")
        self.mixer_summary_label.setObjectName("MixerSummary")
        self.mixer_summary_label.setWordWrap(True)
        self.mixer_summary_label.setMaximumHeight(118)

        self.load_score_source_btn = QPushButton("Load PDF")
        self.load_score_source_btn.setObjectName("CompactToolButton")
        self.load_score_source_btn.setToolTip("Attach a PDF score that roughly matches the loaded MIDI. Analysis controls appear after a score is loaded.")
        self.load_score_source_btn.setFixedWidth(102)
        self.clear_score_source_btn = QPushButton("Clear")
        self.clear_score_source_btn.setObjectName("MiniButton")
        self.clear_score_source_btn.setFixedWidth(78)
        self.clear_score_source_btn.setToolTip("Remove the score source from this project.")
        self.score_source_label = QLabel("Score: none")
        self.score_source_label.setObjectName("SourceReference")
        self.score_source_label.setWordWrap(False)
        self.score_source_label.setMinimumHeight(28)
        self.score_source_label.setMaximumHeight(30)
        self.score_source_label.setMinimumWidth(180)
        self.score_source_label.setMaximumWidth(420)
        self.score_source_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.score_analysis_inline_label = QLabel("Analysis appears after you attach a matching PDF score.")
        self.score_analysis_inline_label.setObjectName("ScoreAnalysisInfo")
        self.score_analysis_inline_label.setWordWrap(False)
        self.score_analysis_inline_label.setMinimumHeight(24)
        self.score_analysis_inline_label.setMaximumHeight(30)
        self.score_analysis_inline_label.setMaximumWidth(320)
        self.score_analysis_inline_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.score_analysis_progress = QProgressBar()
        self.score_analysis_progress.setObjectName("ScoreAnalysisProgress")
        self.score_analysis_progress.setRange(0, 0)
        self.score_analysis_progress.setFixedWidth(130)
        self.score_analysis_progress.setFixedHeight(12)
        self.score_analysis_progress.setTextVisible(False)
        self.score_analysis_progress.setVisible(False)
        self.score_analysis_details_label = QLabel("Details appear after analysis")
        self.score_analysis_details_label.setObjectName("ScoreAnalysisDetails")
        self.score_analysis_details_label.setWordWrap(False)
        self.score_analysis_details_label.setMinimumHeight(24)
        self.score_analysis_details_label.setMaximumHeight(30)
        self.score_analysis_details_label.setMaximumWidth(300)
        self.score_analysis_details_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.fix_alignment_btn = QPushButton("Align…")
        self.fix_alignment_btn.setObjectName("CompactToolButton")
        self.fix_alignment_btn.setFixedWidth(86)
        self.fix_alignment_btn.setToolTip("Compare the loaded PDF and/or audio with the MIDI bar ruler.")
        self.alignment_source_summary_label = QLabel("Load a MIDI plus PDF or audio to align.")
        self.alignment_source_summary_label.setObjectName("SubtleText")
        self.alignment_source_summary_label.setWordWrap(False)
        self.alignment_source_summary_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.score_dynamics_check = QCheckBox("Use score")
        self.score_dynamics_check.setObjectName("GuideCheck")
        self.score_dynamics_check.setFixedWidth(92)
        self.score_dynamics_check.setToolTip("Optional: use detected score markings to guide velocities in newly created takes. Off by default; Original is unchanged.")
        # Compatibility-only hidden label. Earlier builds toggled this orphan
        # QLabel visible even though it was never placed in a layout, creating
        # the stray black native window titled "python" with the word Score.
        self.score_guidance_strength_label = QLabel("", self)
        self.score_guidance_strength_label.setVisible(False)
        self.score_guidance_strength_combo = QComboBox()
        self.score_guidance_strength_combo.addItems(["Light", "Medium", "Strong"])
        self.score_guidance_strength_combo.setToolTip("How strongly detected score markings should influence newly created takes.")
        self.score_guidance_strength_combo.setFixedWidth(78)
        self.analyze_score_btn = QPushButton("Analyze Score")
        self.analyze_score_btn.setObjectName("CompactToolButton")
        self.analyze_score_btn.setFixedWidth(108)
        self.analyze_score_btn.setToolTip("Analyze the attached PDF. If embedded text is not enough, MIDI Performer will try the configured Audiveris OMR bridge.")
        self.score_guided_take_btn = QPushButton("Create Score Take")
        self.score_guided_take_btn.setObjectName("ExportButton")
        self.score_guided_take_btn.setToolTip("Score-guided versions use the normal Takes row: enable Use score, then play the pending numbered row.")
        self.score_guided_take_btn.setVisible(False)
        self.open_score_pdf_btn = QPushButton("Open PDF")
        self.open_score_pdf_btn.setObjectName("MiniButton")
        self.open_score_pdf_btn.setFixedWidth(78)
        self.open_score_pdf_btn.setToolTip("Open the attached PDF score in your default PDF viewer for visual comparison.")
        self.score_focus_label = QLabel("Focus")
        self.score_focus_label.setObjectName("ControlLabelCompact")
        self.score_focus_combo = QComboBox()
        self.score_focus_combo.addItems(["All pages", "Single page", "Page range"])
        self.score_focus_combo.setToolTip("Choose which PDF page(s) to analyze. Align opens at the first mapped measure of this selection.")
        self.score_focus_combo.setFixedWidth(96)
        self.score_focus_start_edit = QLineEdit()
        self.score_focus_start_edit.setPlaceholderText("page")
        self.score_focus_start_edit.setToolTip("First page to analyze and the page where Align should begin.")
        self.score_focus_start_edit.setFixedWidth(52)
        self.score_focus_to_label = QLabel("to")
        self.score_focus_to_label.setObjectName("SubtleText")
        self.score_focus_end_edit = QLineEdit()
        self.score_focus_end_edit.setPlaceholderText("page")
        self.score_focus_end_edit.setToolTip("Last page in the focused score range.")
        self.score_focus_end_edit.setFixedWidth(52)
        self.score_omr_engine_label = QLabel("OMR: not checked")
        self.score_omr_engine_label.setObjectName("ScoreAnalysisInfo")
        self.score_omr_engine_label.setWordWrap(False)
        self.score_omr_engine_label.setMaximumWidth(115)
        self.score_omr_engine_label.setToolTip("True optical music recognition for scanned/image-only PDFs uses Audiveris when configured.")
        self.score_omr_setup_btn = QPushButton("OMR…")
        self.score_omr_setup_btn.setObjectName("CompactToolButton")
        self.score_omr_setup_btn.setToolTip("Choose Audiveris executable or JAR here, close to Analyze Score.")
        self.score_omr_help_btn = QPushButton("Install OMR…")
        self.score_omr_help_btn.setObjectName("CompactToolButton")
        self.score_omr_help_btn.setToolTip("Install Audiveris with Windows winget, then verify the OMR engine. If winget is unavailable, MIDI Performer opens the official install page.")

        self.load_reference_audio_btn = QPushButton("Load Audio")
        self.load_reference_audio_btn.setObjectName("CompactToolButton")
        self.load_reference_audio_btn.setFixedWidth(102)
        self.load_reference_audio_btn.setToolTip("Attach a local reference performance. WAV/FFmpeg-supported audio gets dynamics, attack, and pacing analysis for future mimic work.")
        self.clear_reference_audio_btn = QPushButton("Clear")
        self.clear_reference_audio_btn.setObjectName("MiniButton")
        self.clear_reference_audio_btn.setFixedWidth(78)
        self.clear_reference_audio_btn.setToolTip("Remove the reference audio source from this project.")
        self.reference_audio_label = QLabel("Audio: none")
        self.reference_audio_label.setObjectName("SourceReference")
        self.reference_audio_label.setWordWrap(False)
        self.reference_audio_label.setMinimumHeight(28)
        self.reference_audio_label.setMaximumHeight(30)
        self.reference_audio_label.setMinimumWidth(180)
        self.reference_audio_label.setMaximumWidth(420)
        self.reference_audio_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.audio_analysis_progress = QProgressBar()
        self.audio_analysis_progress.setObjectName("ScoreAnalysisProgress")
        self.audio_analysis_progress.setRange(0, 0)
        self.audio_analysis_progress.setFixedWidth(120)
        self.audio_analysis_progress.setFixedHeight(12)
        self.audio_analysis_progress.setTextVisible(False)
        self.audio_analysis_progress.setVisible(False)
        self.audio_guidance_check = QCheckBox("Use audio")
        self.audio_guidance_check.setObjectName("GuideCheck")
        self.audio_guidance_check.setFixedWidth(92)
        self.audio_guidance_check.setToolTip("Optional: use analyzed reference audio dynamics, attack, and pacing contours to guide new takes. Original is unchanged.")
        # Compatibility-only hidden label. Keeping it parented and permanently
        # hidden prevents the old orphan "Audio" widget from becoming a native
        # top-level window on Windows.
        self.audio_guidance_strength_label = QLabel("", self)
        self.audio_guidance_strength_label.setVisible(False)
        self.audio_guidance_strength_combo = QComboBox()
        self.audio_guidance_strength_combo.addItems(["Light", "Medium", "Strong"])
        self.audio_guidance_strength_combo.setToolTip("How strongly the reference performance dynamics/attack/pacing contours should influence new takes.")
        self.audio_guidance_strength_combo.setFixedWidth(78)

        self.open_project_btn = QPushButton("Open Project")
        self.open_project_btn.setObjectName("ToolButton")
        self.open_project_btn.setToolTip("Open a saved MIDI Performer project with its takes and settings.")
        self.save_project_btn = QPushButton("Save Project")
        self.save_project_btn.setObjectName("ToolButton")
        self.save_project_btn.setToolTip("Save this session: source MIDI, instrument library, takes, notes, favorites, and mixer settings.")
        self.project_file_label = QLabel("Project: unsaved")
        self.project_file_label.setObjectName("SubtleText")
        self.project_file_label.setWordWrap(False)
        self.project_file_label.setMaximumHeight(24)
        self.project_file_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        self.select_all_export_btn = QPushButton("Select all")
        self.select_all_export_btn.setObjectName("ToolButton")
        self.select_all_export_btn.setToolTip("Check every playable saved row for export.")
        self.select_none_export_btn = QPushButton("Select none")
        self.select_none_export_btn.setObjectName("ToolButton")
        self.select_none_export_btn.setToolTip("Clear all export checkboxes.")
        self.takes_summary_label = QLabel("Original locked · no takes yet")
        self.takes_summary_label.setObjectName("SubtleText")
        self.takes_summary_label.setWordWrap(False)
        self.favorite_filter_btn = QPushButton("★ only")
        self.favorite_filter_btn.setObjectName("ToolButton")
        self.favorite_filter_btn.setCheckable(True)
        self.favorite_filter_btn.setToolTip("Show only favorited numbered versions, plus Original and any pending row.")
        self.export_checked_btn = QPushButton("Export…")
        self.export_checked_btn.setObjectName("ExportButton")
        self.export_checked_btn.setToolTip("Export checked rows as MIDI, WAV, or MP3 and choose the output folder/name.")
        self.export_best_btn = QPushButton("Export Best…")
        self.export_best_btn.setObjectName("ExportButton")
        self.export_best_btn.setToolTip("Export the single version marked Best. Use Best when you have chosen the keeper.")
        # Legacy invisible buttons are kept only so older smoke/UI code paths do not break.
        self.export_wav_btn = QPushButton("Export WAV")
        self.export_wav_btn.setObjectName("ExportButton")
        self.export_wav_btn.setVisible(False)
        self.export_mp3_btn = QPushButton("Export MP3")
        self.export_mp3_btn.setObjectName("ExportButton")
        self.export_mp3_btn.setVisible(False)
        self.delete_unchecked_takes_btn = QPushButton("Delete unchecked")
        self.delete_unchecked_takes_btn.setObjectName("DeleteButton")
        self.delete_unchecked_takes_btn.setToolTip("Remove numbered versions that are not checked for export. Original is preserved.")
        self.delete_all_takes_btn = QPushButton("Delete all")
        self.delete_all_takes_btn.setObjectName("DeleteButton")
        self.delete_all_takes_btn.setToolTip("Remove all numbered versions from the Takes list. Original MIDI is preserved.")
        self.takes_container = QWidget()
        self.takes_container.setObjectName("TakesContainer")
        self.takes_layout = QGridLayout(self.takes_container)
        self.takes_layout.setHorizontalSpacing(6)
        self.takes_layout.setVerticalSpacing(5)
        self.takes_layout.setContentsMargins(6, 6, 6, 6)
        self.takes_scroll = QScrollArea()
        self.takes_scroll.setObjectName("TakesScroll")
        self.takes_scroll.setWidgetResizable(True)
        self.takes_scroll.setFrameShape(QFrame.NoFrame)
        self.takes_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.takes_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.takes_scroll.setMinimumHeight(170)
        self.takes_scroll.setMaximumHeight(260)
        self.takes_scroll.setWidget(self.takes_container)
        self.take_checkboxes: dict[str, QCheckBox] = {}
        self.take_play_buttons: dict[str, QPushButton] = {}
        self.take_stop_buttons: dict[str, QPushButton] = {}
        self.take_select_buttons: dict[str, QPushButton] = {}
        for button in [
            self.open_midi_btn, self.soundfont_btn, self.setup_status_btn, self.rewind_btn, self.preview_btn, self.stop_btn,
            self.ff_btn, self.reset_volume_btn, self.reset_tempo_btn, self.create_preview_btn,
            self.play_preview_btn, self.save_take_btn, self.mixer_btn, self.open_project_btn,
            self.save_project_btn, self.select_all_export_btn, self.select_none_export_btn,
            self.favorite_filter_btn, self.export_checked_btn, self.export_best_btn, self.export_wav_btn,
            self.export_mp3_btn, self.delete_unchecked_takes_btn, self.delete_all_takes_btn,
            self.load_score_source_btn, self.clear_score_source_btn, self.load_reference_audio_btn, self.clear_reference_audio_btn,
            self.analyze_score_btn, self.score_guided_take_btn, self.open_score_pdf_btn, self.score_omr_setup_btn, self.score_omr_help_btn,
            self.score_focus_combo, self.score_focus_start_edit, self.score_focus_end_edit,
        ]:
            button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def _connect_signals(self) -> None:
        self.open_midi_btn.clicked.connect(self.load_midi)
        self.soundfont_btn.clicked.connect(self.show_instrument_libraries)
        self.setup_status_btn.clicked.connect(self.show_setup_status)
        self.midi_file_combo.activated.connect(self._midi_combo_activated)
        self.soundfont_file_combo.activated.connect(self._soundfont_combo_activated)
        self.rewind_btn.clicked.connect(self.rewind_playback)
        self.preview_btn.clicked.connect(self.preview_selected)
        self.stop_btn.clicked.connect(self.stop_playback)
        self.ff_btn.clicked.connect(self.fast_forward_playback)
        self.position_slider.valueChanged.connect(self.set_playback_position)
        self.position_slider.sliderReleased.connect(self.restart_playback_from_shuttle)
        self.volume_slider.valueChanged.connect(self.set_master_volume)
        self.tempo_slider.valueChanged.connect(self.set_tempo_percent)
        self.reset_volume_btn.clicked.connect(lambda: self.volume_slider.setValue(100))
        self.reset_tempo_btn.clicked.connect(lambda: self.tempo_slider.setValue(100))
        self.create_preview_btn.clicked.connect(self.create_preview)
        self.play_preview_btn.clicked.connect(self.play_candidate)
        self.save_take_btn.clicked.connect(self.save_candidate_to_takes)
        self.mixer_btn.clicked.connect(self.open_mixer)
        self.open_project_btn.clicked.connect(self.open_project)
        self.save_project_btn.clicked.connect(self.save_project)
        self.select_all_export_btn.clicked.connect(self.select_all_exports)
        self.select_none_export_btn.clicked.connect(self.select_none_exports)
        self.favorite_filter_btn.clicked.connect(self.toggle_favorites_filter)
        self.export_checked_btn.clicked.connect(self.show_export_dialog)
        self.export_best_btn.clicked.connect(self.show_export_best_dialog)
        self.export_wav_btn.clicked.connect(lambda: self.export_checked_takes("wav"))
        self.export_mp3_btn.clicked.connect(lambda: self.export_checked_takes("mp3"))
        self.delete_unchecked_takes_btn.clicked.connect(self.delete_unchecked_takes)
        self.delete_all_takes_btn.clicked.connect(self.delete_all_takes)
        self.load_score_source_btn.clicked.connect(self.load_score_source)
        self.clear_score_source_btn.clicked.connect(self.clear_score_source)
        self.analyze_score_btn.clicked.connect(self.analyze_score_source)
        self.score_guided_take_btn.clicked.connect(self.create_score_guided_take)
        self.fix_alignment_btn.clicked.connect(self.fix_score_alignment)
        self.open_score_pdf_btn.clicked.connect(self.open_score_pdf)
        self.score_focus_combo.currentTextChanged.connect(self.set_score_focus_mode)
        self.score_focus_start_edit.editingFinished.connect(self.set_score_focus_pages)
        self.score_focus_end_edit.editingFinished.connect(self.set_score_focus_pages)
        self.score_omr_setup_btn.clicked.connect(self.choose_score_omr_engine)
        self.score_omr_help_btn.clicked.connect(self.install_audiveris_engine)
        self.score_dynamics_check.toggled.connect(self.set_score_dynamics_enabled)
        self.score_guidance_strength_combo.currentTextChanged.connect(self.set_score_guidance_strength)
        self.load_reference_audio_btn.clicked.connect(self.load_reference_audio)
        self.clear_reference_audio_btn.clicked.connect(self.clear_reference_audio)
        self.audio_guidance_check.toggled.connect(self.set_audio_guidance_enabled)
        self.audio_guidance_strength_combo.currentTextChanged.connect(self.set_audio_guidance_strength)
        for key, btn in self.level_buttons.items():
            btn.clicked.connect(lambda _checked=False, k=key: self.set_level(k))
        for label, btn in self.expression_buttons.items():
            btn.clicked.connect(lambda _checked=False, v=label: self.set_expression(v))
        for label, btn in self.timing_buttons.items():
            btn.clicked.connect(lambda _checked=False, v=label: self.set_timing_feel(v))

    def _build_menu(self) -> None:
        bar = self.menuBar()
        bar.setNativeMenuBar(False)

        file_menu = bar.addMenu("File")
        self.open_midi_action = QAction("Open MIDI…", self)
        self.open_midi_action.setShortcut("Ctrl+O")
        self.open_midi_action.triggered.connect(self.load_midi)
        file_menu.addAction(self.open_midi_action)

        self.recent_midi_menu = file_menu.addMenu("Recent MIDI")
        self.recent_projects_menu = file_menu.addMenu("Recent Projects")
        file_menu.addSeparator()

        new_action = QAction("New Project", self)
        new_action.setShortcut("Ctrl+N")
        new_action.triggered.connect(self.new_project)
        open_action = QAction("Open Project…", self)
        open_action.setShortcut("Ctrl+Shift+O")
        open_action.triggered.connect(self.open_project)
        save_action = QAction("Save Project", self)
        save_action.setShortcut("Ctrl+S")
        save_action.triggered.connect(self.save_project)
        save_as_action = QAction("Save Project As…", self)
        save_as_action.setShortcut("Ctrl+Shift+S")
        save_as_action.triggered.connect(self.save_project_as)
        file_menu.addActions([new_action, open_action, save_action, save_as_action])
        file_menu.addSeparator()

        export_action = QAction("Export checked takes…", self)
        export_action.setShortcut("Ctrl+E")
        export_action.triggered.connect(self.show_export_dialog)
        file_menu.addAction(export_action)
        export_best_action = QAction("Export Best…", self)
        export_best_action.setShortcut("Ctrl+Shift+E")
        export_best_action.triggered.connect(self.show_export_best_dialog)
        file_menu.addAction(export_best_action)
        file_menu.addSeparator()

        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        transport_menu = bar.addMenu("Transport")
        play_action = QAction("Play / Pause selected row", self)
        play_action.setShortcut("Space")
        play_action.triggered.connect(self.preview_selected)
        stop_action = QAction("Stop", self)
        stop_action.setShortcut("Esc")
        stop_action.triggered.connect(self.stop_playback)
        rewind_action = QAction("Rewind", self)
        rewind_action.setShortcut("Home")
        rewind_action.triggered.connect(self.rewind_playback)
        transport_menu.addActions([play_action, stop_action, rewind_action])

        sources_menu = bar.addMenu("Sources")
        self.load_score_action = QAction("Load Score Source…", self)
        self.load_score_action.setShortcut("Ctrl+L")
        self.load_score_action.triggered.connect(self.load_score_source)
        self.clear_score_action = QAction("Clear Score Source", self)
        self.clear_score_action.triggered.connect(self.clear_score_source)
        self.load_audio_action = QAction("Load Reference Audio…", self)
        self.load_audio_action.setShortcut("Ctrl+R")
        self.load_audio_action.triggered.connect(self.load_reference_audio)
        self.clear_audio_action = QAction("Clear Reference Audio", self)
        self.clear_audio_action.triggered.connect(self.clear_reference_audio)
        sources_menu.addActions([self.load_score_action, self.clear_score_action])
        self.recent_score_sources_menu = sources_menu.addMenu("Recent Score Sources")
        sources_menu.addSeparator()
        sources_menu.addActions([self.load_audio_action, self.clear_audio_action])
        self.recent_reference_audio_menu = sources_menu.addMenu("Recent Reference Audio")

        settings_menu = bar.addMenu("Settings")
        self.recent_soundfonts_menu = settings_menu.addMenu("Recent Instrument Libraries")
        setup_action = QAction("Setup / Export Status…", self)
        setup_action.setShortcut("Ctrl+,")
        setup_action.triggered.connect(self.show_setup_status)
        locations = QAction("File Locations…", self)
        locations.triggered.connect(self.show_settings)
        settings_menu.addAction(setup_action)
        settings_menu.addAction(locations)

        help_menu = bar.addMenu("Help")
        shortcuts = QAction("Keyboard Shortcuts", self)
        shortcuts.triggered.connect(self.show_keyboard_shortcuts)
        about = QAction("About", self)
        about.triggered.connect(self.show_about)
        help_menu.addActions([shortcuts, about])

    def _build_ui(self) -> QWidget:
        content = QWidget()
        content.setObjectName("AppRoot")
        outer = QVBoxLayout(content)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(6)

        header = QFrame()
        header.setObjectName("Header")
        h = QVBoxLayout(header)
        h.setContentsMargins(10, 4, 10, 4)
        h.setSpacing(0)
        title = QLabel("MIDI Performer")
        title.setObjectName("Title")
        subtitle = QLabel(TAGLINE)
        subtitle.setObjectName("Subtitle")
        h.addWidget(title)
        h.addWidget(subtitle)
        outer.addWidget(header)

        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)
        grid.addWidget(self._source_card(), 0, 0, alignment=Qt.AlignTop)
        grid.addWidget(self._mixer_card(), 0, 1, alignment=Qt.AlignTop)
        grid.addWidget(self._score_sources_card(), 1, 0, 1, 2, alignment=Qt.AlignTop)
        grid.addWidget(self._audition_card(), 2, 0, 1, 2)
        grid.addWidget(self._takes_card(), 3, 0, 1, 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setRowStretch(0, 0)
        grid.setRowStretch(1, 0)
        grid.setRowStretch(2, 0)
        grid.setRowStretch(3, 2)
        outer.addLayout(grid, 1)

        outer.addWidget(self.status_label)
        content.setMinimumWidth(900)
        content.setStyleSheet(self._stylesheet())

        scroll = QScrollArea()
        scroll.setObjectName("MainScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setWidget(content)
        scroll.setStyleSheet(self._stylesheet())
        return scroll

    def _source_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("Card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(5)
        layout.setAlignment(Qt.AlignTop)

        source = QGridLayout()
        source.setHorizontalSpacing(6)
        source.setVerticalSpacing(2)
        midi_source_label = QLabel("MIDI")
        midi_source_label.setObjectName("ControlLabelTop")
        library_label = QLabel("Instrument Library")
        library_label.setObjectName("ControlLabelTop")
        setup_label = QLabel("Readiness")
        setup_label.setObjectName("ControlLabelTop")
        source.addWidget(midi_source_label, 0, 0)
        source.addWidget(library_label, 0, 1)
        source.addWidget(setup_label, 0, 2)
        source.addWidget(self.midi_file_combo, 1, 0)
        source.addWidget(self.soundfont_file_combo, 1, 1)
        source.addWidget(self.setup_status_btn, 1, 2)
        source.addWidget(self.midi_info_label, 2, 0, 1, 3)
        prep_row = QHBoxLayout()
        prep_row.setSpacing(6)
        prep_row.addWidget(self.prepare_status_label)
        prep_row.addWidget(self.prepare_progress)
        prep_row.addStretch(1)
        source.addLayout(prep_row, 3, 0, 1, 3)
        source.setRowStretch(0, 0)
        source.setRowStretch(1, 0)
        source.setRowStretch(2, 0)
        source.setRowStretch(3, 0)
        source.setColumnStretch(0, 1)
        source.setColumnStretch(1, 1)
        source.setColumnStretch(2, 0)
        layout.addLayout(source)

        project_row = QHBoxLayout()
        project_row.setSpacing(6)
        project_row.addWidget(self._control_label_left("Project"))
        project_row.addWidget(self.open_project_btn)
        project_row.addWidget(self.save_project_btn)
        project_row.addWidget(self.project_file_label)
        project_row.addStretch(1)
        layout.addLayout(project_row)
        layout.addWidget(self._separator())

        top = QHBoxLayout()
        top.setSpacing(6)
        top.addWidget(self._section_title("Playback"))
        for b in (self.rewind_btn, self.preview_btn, self.stop_btn, self.ff_btn):
            top.addWidget(b)
        top.addStretch(1)
        top.addWidget(self.playback_status_label)
        layout.addLayout(top)

        shuttle = QGridLayout()
        shuttle.setHorizontalSpacing(6)
        shuttle.addWidget(QLabel("Shuttle"), 0, 0)
        shuttle.addWidget(self.position_slider, 0, 1)
        shuttle.addWidget(self.position_label, 0, 2)
        shuttle.setColumnStretch(1, 1)
        layout.addLayout(shuttle)

        controls = QHBoxLayout()
        controls.setSpacing(8)
        controls.addWidget(self._compact_control_group("Volume", self.volume_slider, self.volume_label, self.reset_volume_btn))
        controls.addWidget(self._compact_control_group("Tempo", self.tempo_slider, self.tempo_label, self.reset_tempo_btn))
        controls.addStretch(1)
        layout.addLayout(controls)
        return card

    def _mixer_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("Card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(5)
        layout.setAlignment(Qt.AlignTop)
        row = QHBoxLayout()
        row.addWidget(self._section_title("Mixer"))
        row.addStretch(1)
        row.addWidget(self.mixer_btn)
        layout.addLayout(row)
        layout.addWidget(self.mixer_summary_label)
        layout.addWidget(self.tool_note_label)
        return card

    def _score_sources_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("Card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(8, 5, 8, 5)
        layout.setSpacing(4)
        layout.setAlignment(Qt.AlignTop)

        # Stable fixed-width action slots keep Use score / Use audio aligned even
        # when Open/Clear controls appear or disappear.
        score_actions = QWidget(card)
        score_actions.setFixedWidth(162)
        score_actions_layout = QHBoxLayout(score_actions)
        score_actions_layout.setContentsMargins(0, 0, 0, 0)
        score_actions_layout.setSpacing(6)
        score_actions_layout.addWidget(self.open_score_pdf_btn)
        score_actions_layout.addWidget(self.clear_score_source_btn)

        audio_actions = QWidget(card)
        audio_actions.setFixedWidth(162)
        audio_actions_layout = QHBoxLayout(audio_actions)
        audio_actions_layout.setContentsMargins(0, 0, 0, 0)
        audio_actions_layout.setSpacing(6)
        audio_actions_layout.addSpacing(84)
        audio_actions_layout.addWidget(self.clear_reference_audio_btn)

        focus_group = QWidget(card)
        focus_layout = QHBoxLayout(focus_group)
        focus_layout.setContentsMargins(0, 0, 0, 0)
        focus_layout.setSpacing(5)
        focus_layout.addWidget(self.score_focus_label)
        focus_layout.addWidget(self.score_focus_combo)
        focus_layout.addWidget(self.score_focus_start_edit)
        focus_layout.addWidget(self.score_focus_to_label)
        focus_layout.addWidget(self.score_focus_end_edit)

        score_row = QHBoxLayout()
        score_row.setSpacing(6)
        score_row.addWidget(self.load_score_source_btn)
        score_row.addWidget(score_actions)
        score_row.addWidget(self.score_dynamics_check)
        score_row.addWidget(self.score_guidance_strength_combo)
        score_row.addSpacing(4)
        score_row.addWidget(focus_group)
        score_row.addWidget(self.analyze_score_btn)
        score_row.addWidget(self.score_analysis_progress)
        score_row.addStretch(1)
        layout.addLayout(score_row)

        # Align remains a distinct left-edge action, but shares the existing
        # score-status row so it does not consume another vertical band.
        self.alignment_row_widget = QWidget(card)
        score_status = QHBoxLayout(self.alignment_row_widget)
        score_status.setContentsMargins(0, 0, 0, 0)
        score_status.setSpacing(7)
        score_status.addWidget(self.fix_alignment_btn)
        score_status.addWidget(self.score_source_label)
        score_status.addWidget(self.score_analysis_inline_label)
        score_status.addWidget(self.score_analysis_details_label)
        score_status.addStretch(1)
        layout.addWidget(self.alignment_row_widget)

        audio_row = QHBoxLayout()
        audio_row.setSpacing(6)
        audio_row.addWidget(self.load_reference_audio_btn)
        audio_row.addWidget(audio_actions)
        audio_row.addWidget(self.audio_guidance_check)
        audio_row.addWidget(self.audio_guidance_strength_combo)
        audio_row.addSpacing(4)
        audio_row.addWidget(self.reference_audio_label)
        audio_row.addWidget(self.audio_analysis_progress)
        audio_row.addStretch(1)
        layout.addLayout(audio_row)

        # The older separate alignment summary row is retired. The hidden label
        # remains only for project compatibility and tooltips/state updates.
        self.alignment_source_summary_label.setVisible(False)

        omr_row = QHBoxLayout()
        omr_row.setSpacing(6)
        omr_row.addWidget(self.score_omr_engine_label)
        omr_row.addWidget(self.score_omr_setup_btn)
        omr_row.addWidget(self.score_omr_help_btn)
        omr_row.addStretch(1)
        layout.addLayout(omr_row)

        card.setMinimumHeight(96)
        card.setMaximumHeight(154)
        return card
    def _tools_card(self) -> QFrame:
        return self._mixer_card()

    def _playback_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("Card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(5)
        layout.addWidget(self._section_title("Playback"))
        transport = QHBoxLayout()
        transport.setSpacing(5)
        for b in (self.rewind_btn, self.preview_btn, self.stop_btn, self.ff_btn):
            transport.addWidget(b)
        transport.addStretch(1)
        layout.addLayout(transport)
        layout.addWidget(self.transport_target_label)
        layout.addWidget(self.playback_status_label)
        shuttle = QGridLayout()
        shuttle.setHorizontalSpacing(6)
        shuttle.addWidget(QLabel("0:00"), 0, 0)
        shuttle.addWidget(self.position_slider, 0, 1)
        shuttle.addWidget(self.position_label, 0, 2)
        shuttle.setColumnStretch(1, 1)
        layout.addLayout(shuttle)
        controls = QGridLayout()
        controls.setHorizontalSpacing(6)
        controls.setVerticalSpacing(4)
        controls.addWidget(self._control_label_left("Volume"), 0, 0)
        controls.addWidget(self.volume_slider, 0, 1)
        controls.addWidget(self.volume_label, 0, 2)
        controls.addWidget(self.reset_volume_btn, 0, 3)
        controls.addWidget(self._control_label_left("Tempo"), 1, 0)
        controls.addWidget(self.tempo_slider, 1, 1)
        controls.addWidget(self.tempo_label, 1, 2)
        controls.addWidget(self.reset_tempo_btn, 1, 3)
        controls.setColumnStretch(1, 1)
        layout.addLayout(controls)
        return card

    def _audition_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("Card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(2)

        strip = QHBoxLayout()
        strip.setSpacing(8)
        strip.addWidget(self._section_title("Variation"))
        strip.addWidget(self._choice_group_frame("Level", [self.level_buttons[k] for k, _ in LEVELS], "How much MIDI Performer should reshape the source MIDI."))
        strip.addWidget(self._choice_group_frame("Expr.", [self.expression_buttons[label] for label, _ in EXPRESSION_LEVELS], "How much dynamic/velocity expression to add. Default is None."))
        strip.addWidget(self._choice_group_frame("Timing", [self.timing_buttons[label] for label in TIMING_FEELS], "How the notes should sit against the beat."))
        strip.addStretch(1)
        layout.addLayout(strip)
        card.setMaximumHeight(58)
        return card

    def _takes_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("Card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(5)
        title_row = QHBoxLayout()
        title_row.setSpacing(5)
        title_row.addWidget(self._section_title("Takes"))
        title_row.addWidget(self.takes_summary_label)
        # Export is the primary action for this workspace, so keep it close to the title
        # instead of letting it drift off the right edge on narrower windows.
        title_row.addWidget(self.export_checked_btn)
        title_row.addWidget(self.export_best_btn)
        title_row.addWidget(self.select_all_export_btn)
        title_row.addWidget(self.select_none_export_btn)
        title_row.addWidget(self.favorite_filter_btn)
        title_row.addStretch(1)
        title_row.addWidget(self.delete_unchecked_takes_btn)
        title_row.addWidget(self.delete_all_takes_btn)
        layout.addLayout(title_row)
        layout.addWidget(self.takes_scroll, 1)
        return card

    def _compact_control_group(self, label_text: str, slider: QSlider, value_label: QLabel, reset_button: QPushButton) -> QFrame:
        frame = QFrame()
        frame.setObjectName("ControlGroup")
        frame.setToolTip(f"Adjust {label_text.lower()} for playback and saved versions.")
        row = QHBoxLayout(frame)
        row.setContentsMargins(7, 3, 7, 3)
        row.setSpacing(5)
        label = self._control_label_left(label_text)
        label.setMinimumWidth(48)
        row.addWidget(label)
        row.addWidget(slider)
        row.addWidget(value_label)
        row.addSpacing(2)
        row.addWidget(reset_button)
        return frame

    def _compact_control_row(self, label_text: str, slider: QSlider, value_label: QLabel, reset_button: QPushButton) -> QHBoxLayout:
        # Compatibility helper for older playback-card code paths. The visible
        # source card now uses _compact_control_group() so Volume and Tempo get
        # the same framed treatment and balanced reset placement.
        row = QHBoxLayout()
        row.setSpacing(5)
        label = self._control_label_left(label_text)
        label.setMinimumWidth(54)
        row.addWidget(label)
        row.addWidget(slider)
        row.addWidget(value_label)
        row.addWidget(reset_button)
        return row

    def _choice_group_frame(self, label_text: str, buttons: list[QPushButton], tooltip: str) -> QFrame:
        frame = QFrame()
        frame.setObjectName("ChoiceGroup")
        frame.setToolTip(tooltip)
        row = QHBoxLayout(frame)
        row.setContentsMargins(4, 2, 4, 2)
        row.setSpacing(2)
        label = self._control_label_left(label_text)
        label.setMinimumWidth(0)
        label.setToolTip(tooltip)
        row.addWidget(label)
        for btn in buttons:
            row.addWidget(btn)
        return frame

    def _inline_choice_group(self, label_text: str, buttons: list[QPushButton]) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(4)
        label = self._control_label_left(label_text)
        label.setMinimumWidth(0)
        row.addWidget(label)
        for btn in buttons:
            row.addWidget(btn)
        return row

    def _button_row(self, buttons: list[QPushButton]) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(5)
        for btn in buttons:
            row.addWidget(btn)
        row.addStretch(1)
        return row

    def _vertical_fader(self, label_text: str, slider: QSlider, value_label: QLabel, reset_button: QPushButton | None = None) -> QVBoxLayout:
        box = QVBoxLayout()
        box.setSpacing(3)
        label = QLabel(label_text)
        label.setObjectName("ControlLabelTop")
        label.setAlignment(Qt.AlignCenter)
        value_label.setAlignment(Qt.AlignCenter)
        box.addWidget(label)
        box.addWidget(slider, alignment=Qt.AlignHCenter)
        box.addWidget(value_label)
        if reset_button is not None:
            box.addWidget(reset_button, alignment=Qt.AlignHCenter)
        return box

    def _section_title(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("SectionTitle")
        return label

    def _control_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("ControlLabel")
        label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        return label

    def _control_label_left(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("ControlLabelCompact")
        label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        return label

    def _separator(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setObjectName("Separator")
        return line

    def _refresh_all(self) -> None:
        self._refresh_file_combos()
        self._refresh_recent_menus()
        self._update_midi_summary()
        self._refresh_mixer_summary()
        self._refresh_candidate_status()
        self._refresh_project_label()
        self._refresh_source_references()
        self._refresh_takes_summary()
        self._rebuild_takes_table()
        self._update_action_states()

    def _refresh_project_label(self) -> None:
        path = getattr(self.project, "current_project_path", None)
        if path:
            self.project_file_label.setText(f"Project: {Path(path).name}")
            self.project_file_label.setToolTip(str(path))
        else:
            self.project_file_label.setText("Project: unsaved")
            self.project_file_label.setToolTip("Save this MIDI Performer session to reopen takes later.")

    def _compact_score_summary(self, text: str) -> str:
        compact = " ".join(str(text or "").split())
        replacements = [
            ("PDF analyzed · ", "analyzed · "),
            ("musical text found", "music text"),
            ("dynamic marks found", "dynamics"),
            ("dynamic marks", "dynamics"),
            ("score notes", "notes"),
            ("OMR score", "OMR"),
        ]
        for old, new in replacements:
            compact = compact.replace(old, new)
        return compact

    def _compact_audio_summary(self, summary: str, analysis: str = "") -> str:
        compact = " ".join(str(summary or "").split())
        if analysis and analysis not in compact:
            compact = f"{compact} · {analysis}" if compact else str(analysis)
        replacements = [
            ("loudness contour ready", "contour ready"),
            ("FFmpeg contour ready", "contour ready"),
            ("attached · audio guide unavailable", "attached · no contour"),
            ("duration unknown", "unknown length"),
            ("channels unknown", "ch ?"),
            ("rate unknown", "rate ?"),
        ]
        for old, new in replacements:
            compact = compact.replace(old, new)
        return compact[:68] + ("…" if len(compact) > 68 else "")

    def _refresh_source_references(self) -> None:
        if hasattr(self, "score_source_label"):
            score_path = getattr(self.project, "score_source_path", None)
            if score_path:
                fmt = getattr(self.project, "score_source_format", None) or "Score"
                summary = getattr(self.project, "score_intent_summary", None) or getattr(self.project, "score_source_summary", None) or Path(score_path).name
                status = getattr(self.project, "score_source_status", None) or "Loaded"
                detail = getattr(self.project, "score_intent_detail", None) or getattr(self.project, "score_source_detail", None) or ""
                alignment_summary = getattr(self.project, "score_alignment_summary", None) or ""
                alignment_detail = getattr(self.project, "score_alignment_detail", None) or ""
                tooltip_parts = [str(score_path), status, detail, alignment_summary, alignment_detail]
                compact_summary = self._compact_score_summary(str(summary))
                self.score_source_label.setText(f"Score: {fmt} · {compact_summary}")
                self.score_source_label.setToolTip("\n".join(part for part in tooltip_parts if part))
            else:
                self.score_source_label.setText("Score: none · load matching PDF score")
                self.score_source_label.setToolTip("Attach a PDF score that roughly matches the loaded MIDI. MIDI Performer will analyze what it can and clearly report when true OMR is still needed.")
            self.clear_score_source_btn.setEnabled(bool(score_path))
            self._sync_score_guidance_widgets()
            self._sync_score_focus_widgets()
            self._refresh_omr_setup_widgets()
            self._refresh_score_analysis_notice_from_project()
            self._refresh_score_analysis_details_widget()
        if hasattr(self, "reference_audio_label"):
            audio_path = getattr(self.project, "reference_audio_path", None)
            if audio_path:
                fmt = getattr(self.project, "reference_audio_format", None) or "Audio"
                summary = getattr(self.project, "reference_audio_summary", None) or Path(audio_path).name
                analysis = getattr(self.project, "reference_audio_analysis_summary", None) or ""
                detail = getattr(self.project, "reference_audio_detail", None) or ""
                analysis_detail = getattr(self.project, "reference_audio_analysis_detail", None) or ""
                label = f"Audio: {fmt} · {self._compact_audio_summary(summary, analysis)}"
                self.reference_audio_label.setText(label)
                self.reference_audio_label.setToolTip("\n".join(part for part in [str(audio_path), detail, analysis_detail] if part))
            else:
                self.reference_audio_label.setText("Audio: none")
                self.reference_audio_label.setToolTip("Optional: attach a WAV/MP3 reference performance. WAV and FFmpeg-decodable audio can produce dynamics/attack/pacing contours for audio-guided takes.")
            self.clear_reference_audio_btn.setEnabled(bool(audio_path))
            self._sync_audio_guidance_widgets()

    def _remember_recent_path(self, key: str, path: str, max_items: int = 10) -> None:
        if not path:
            return
        current = self.settings.get(key, []) or []
        cleaned = [p for p in current if p and Path(p).exists() and p != path]
        cleaned.insert(0, path)
        self.settings.set(key, cleaned[:max_items])

    def _refresh_recent_menus(self) -> None:
        if not hasattr(self, "recent_midi_menu"):
            return
        self._populate_recent_menu(self.recent_midi_menu, "recent_midi_paths", self._open_recent_midi, "No recent MIDI files")
        self._populate_recent_menu(self.recent_soundfonts_menu, "recent_soundfont_paths", self._open_recent_soundfont, "No recent instrument libraries")
        self._populate_recent_menu(self.recent_projects_menu, "recent_project_paths", self._open_recent_project, "No recent projects")
        self._populate_recent_menu(self.recent_score_sources_menu, "recent_score_source_paths", self._open_recent_score_source, "No recent score sources")
        self._populate_recent_menu(self.recent_reference_audio_menu, "recent_reference_audio_paths", self._open_recent_reference_audio, "No recent reference audio")

    def _populate_recent_menu(self, menu: Any, key: str, callback: Any, empty_text: str) -> None:
        menu.clear()
        paths = [p for p in (self.settings.get(key, []) or []) if p and Path(p).exists()]
        if paths != (self.settings.get(key, []) or []):
            self.settings.set(key, paths)
        if not paths:
            empty = QAction(empty_text, self)
            empty.setEnabled(False)
            menu.addAction(empty)
            return
        for path in paths[:10]:
            action = QAction(Path(path).name, self)
            action.setToolTip(str(path))
            action.triggered.connect(lambda _checked=False, p=path: callback(p))
            menu.addAction(action)
        menu.addSeparator()
        clear = QAction("Clear recent list", self)
        clear.triggered.connect(lambda _checked=False, k=key: self._clear_recent_paths(k))
        menu.addAction(clear)

    def _clear_recent_paths(self, key: str) -> None:
        self.settings.set(key, [])
        self._refresh_recent_menus()
        self.status_label.setText("Recent list cleared")

    def _open_recent_midi(self, path: str) -> None:
        if Path(path).exists():
            self._set_midi_path(path, "Recent MIDI loaded")
        else:
            self.status_label.setText("Recent MIDI file was not found")
            self._refresh_recent_menus()

    def _open_recent_soundfont(self, path: str) -> None:
        if not Path(path).exists():
            self.status_label.setText("Recent instrument library was not found")
            self._refresh_recent_menus()
            return
        self.project.selected_soundfont_path = path
        self.settings.set("last_soundfont_path", path)
        self.settings.set("last_soundfont_directory", str(Path(path).parent))
        self._remember_recent_path("recent_soundfont_paths", path)
        if self.project.source_midi_path:
            self._invalidate_playback_ready()
        self._refresh_all()
        if self.project.source_midi_path:
            self._begin_playback_prepare(status_prefix=f"Instrument library selected: {Path(path).name} · ")
        else:
            self.status_label.setText(f"Instrument library selected: {Path(path).name}")

    def _open_recent_project(self, path: str) -> None:
        if Path(path).exists():
            self._load_project_path(path)
        else:
            self.status_label.setText("Recent project was not found")
            self._refresh_recent_menus()

    def _open_recent_score_source(self, path: str) -> None:
        if Path(path).exists():
            self._set_score_source(path)
        else:
            self.status_label.setText("Recent score source was not found")
            self._refresh_recent_menus()

    def _open_recent_reference_audio(self, path: str) -> None:
        if Path(path).exists():
            self._set_reference_audio(path)
        else:
            self.status_label.setText("Recent reference audio was not found")
            self._refresh_recent_menus()

    def _refresh_takes_summary(self) -> None:
        if not hasattr(self, "takes_summary_label"):
            return
        takes = list(self.project.takes or [])
        fav_count = sum(1 for take in takes if bool(take.get("favorite")))
        best = next((str(take.get("name")) for take in takes if bool(take.get("best"))), "none")
        if not self.project.source_midi_path:
            text = "Original locked · load MIDI"
        else:
            shown = fav_count if self.show_favorites_only else len(takes)
            text = f"Original locked · {shown}/{len(takes)} shown · ★ {fav_count} · Best {best}"
        self.takes_summary_label.setText(text)
        self.takes_summary_label.setToolTip("Original is immutable. Favorites and Best are saved with the project and included in export manifests.")

    def _refresh_file_combos(self) -> None:
        self.midi_file_combo.blockSignals(True)
        self.soundfont_file_combo.blockSignals(True)
        self.midi_file_combo.clear()
        self.soundfont_file_combo.clear()
        midi_paths = []
        if self.project.source_midi_path:
            midi_paths.append(self.project.source_midi_path)
        midi_paths.extend(self.settings.get("recent_midi_paths", []) or [])
        seen: set[str] = set()
        midi_paths = [p for p in midi_paths if p and Path(p).exists() and not (p in seen or seen.add(p))]
        if self.project.source_midi_path:
            for p in midi_paths:
                self.midi_file_combo.addItem(Path(p).name, p)
            self.midi_file_combo.addItem("Browse for MIDI…", "__browse__")
            idx = self.midi_file_combo.findData(self.project.source_midi_path)
            if idx >= 0:
                self.midi_file_combo.setCurrentIndex(idx)
        else:
            self.midi_file_combo.addItem("Open MIDI…", "__browse__")
            for p in midi_paths:
                self.midi_file_combo.addItem(Path(p).name, p)
        self.midi_file_combo.setToolTip(self.project.source_midi_path or "Open a MIDI")

        sf_paths = []
        if self.project.selected_soundfont_path:
            sf_paths.append(self.project.selected_soundfont_path)
        sf_paths.extend(self.settings.get("recent_soundfont_paths", []) or [])
        seen.clear()
        sf_paths = [p for p in sf_paths if p and Path(p).exists() and not (p in seen or seen.add(p))]
        if sf_paths:
            for p in sf_paths:
                self.soundfont_file_combo.addItem(Path(p).name, p)
            self.soundfont_file_combo.addItem("Manage Instrument Libraries…", "__browse__")
        else:
            self.soundfont_file_combo.addItem("Choose Instrument Library…", "__browse__")
        if self.project.selected_soundfont_path:
            idx = self.soundfont_file_combo.findData(self.project.selected_soundfont_path)
            if idx >= 0:
                self.soundfont_file_combo.setCurrentIndex(idx)
        self.soundfont_file_combo.setToolTip(("Current instrument library: " + self.project.selected_soundfont_path) if self.project.selected_soundfont_path else "Choose or install an instrument library")
        self.midi_file_combo.blockSignals(False)
        self.soundfont_file_combo.blockSignals(False)

    def _midi_combo_activated(self, index: int) -> None:
        path = self.midi_file_combo.itemData(index)
        if path == "__browse__":
            self.load_midi()
        elif path and path != self.project.source_midi_path:
            self._set_midi_path(path, "MIDI loaded")

    def _soundfont_combo_activated(self, index: int) -> None:
        path = self.soundfont_file_combo.itemData(index)
        if path == "__browse__":
            self.show_instrument_libraries()
        elif path and path != self.project.selected_soundfont_path:
            self._activate_soundfont(path, prepare=True)

    def _invalidate_midi_cache(self) -> None:
        self._summary_cache_path = None
        self._summary_cache = None
        self._track_info_cache_path = None
        self._track_info_cache = None

    def _midi_summary(self) -> Any:
        path = self.project.source_midi_path
        if not path:
            return None
        if self._summary_cache_path != path or self._summary_cache is None:
            self._summary_cache = summarize_midi(path)
            self._summary_cache_path = path
        return self._summary_cache

    def _track_infos(self) -> list[Any]:
        path = self.project.source_midi_path
        if not path:
            return []
        if self._track_info_cache_path != path or self._track_info_cache is None:
            self._track_info_cache = analyze_tracks(path)
            self._track_info_cache_path = path
        return list(self._track_info_cache or [])

    def _music_track_infos(self) -> list[Any]:
        """Playable tracks only, renumbered visually by the caller.

        Control/conductor tracks remain in playback and export, but they are not
        user-editable mixer parts because they contain no note-on events.
        """
        return [info for info in self._track_infos() if bool(getattr(info, "is_playable", False))]

    def _update_midi_summary(self) -> None:
        if not self.project.source_midi_path:
            if self.project.selected_soundfont_path:
                self.midi_info_label.setText("Choose a MIDI to start · instrument library ready")
            else:
                self.midi_info_label.setText("Choose a MIDI and instrument library to start")
            self._update_prepare_widgets()
            return
        if self._playback_preparing:
            self.midi_info_label.setText(f"Preparing playback for {Path(self.project.source_midi_path).name}…")
            self._update_prepare_widgets()
            return
        if not self._playback_ready:
            if self._playback_prepare_error:
                self.midi_info_label.setText(f"Playback not ready · {Path(self.project.source_midi_path).name}")
            elif not self.project.selected_soundfont_path:
                self.midi_info_label.setText(f"Select an instrument library to prepare {Path(self.project.source_midi_path).name}")
            else:
                self.midi_info_label.setText(f"Preparing playback needed · {Path(self.project.source_midi_path).name}")
            self._update_prepare_widgets()
            return
        try:
            summary = self._midi_summary()
            self.midi_info_label.setText(summary.compact_text())
            if summary.tempo_bpm:
                effective = summary.tempo_bpm * int(self.project.playback_tempo_percent or 100) / 100
                self.tempo_label.setText(f"{int(self.project.playback_tempo_percent or 100)}% · {effective:.0f} BPM")
        except Exception:
            self.midi_info_label.setText("MIDI ready · analysis unavailable")
        self._update_prepare_widgets()

    def _refresh_mixer_summary(self) -> None:
        if not self.project.source_midi_path:
            self.mixer_summary_label.setText(
                "Waiting for MIDI\n"
                "Track 1 · instrument · volume · balance\n"
                "Track 2 · instrument · volume · balance\n"
                "Track 3 · instrument · volume · balance"
            )
            self.tool_note_label.setText("Load a MIDI; this fills with track patches and mix state.")
            return
        try:
            all_infos = self._track_infos()
            infos = self._music_track_infos()
            control_count = len(all_infos) - len(infos)
            lines = []
            for display_index, info in enumerate(infos[:4], start=1):
                key = str(info.index)
                loaded = int(info.programs[0]) if info.programs else 0
                program_override = self.project.track_program_overrides.get(key)
                program = loaded if program_override is None else int(program_override)
                program_name = f"{program + 1:03d} {GM_PROGRAM_NAMES[program]}"
                vol = int(self.project.track_volume_overrides.get(key, 100))
                pan_raw = int(self.project.track_pan_overrides.get(key, 64)) - 64
                pan = "C" if pan_raw == 0 else (f"L{abs(pan_raw)}" if pan_raw < 0 else f"R{pan_raw}")
                octave = int(self.project.track_octave_overrides.get(key, 0) or 0)
                octave_text = "" if octave == 0 else f" · Oct {octave:+d}"
                flags = []
                if self.project.track_solo_overrides.get(key):
                    flags.append("Solo")
                if self.project.track_mute_overrides.get(key):
                    flags.append("Mute")
                flag_text = f" · {' / '.join(flags)}" if flags else ""
                lines.append(f"Track {display_index}: {program_name} · Vol {vol} · Pan {pan}{octave_text}{flag_text}")
            if len(infos) > 4:
                lines.append(f"+ {len(infos) - 4} more in Track Mixer")
            if control_count > 0:
                lines.append(f"{control_count} control track(s) hidden from mixer")
            self.mixer_summary_label.setText("\n".join(lines) if infos else "No playable music tracks found.")
            self.mixer_summary_label.setToolTip("\n".join(
                f"Source track {info.index + 1}: {(info.name or f'Track {info.index + 1}').strip()}"
                for info in all_infos
            ))
            active = bool(
                self.project.track_program_overrides
                or self.project.track_volume_overrides
                or self.project.track_pan_overrides
                or self.project.track_octave_overrides
                or self.project.track_solo_overrides
                or self.project.track_mute_overrides
            )
            self.tool_note_label.setText("Mixer edits active." if active else "Full mixer: instruments, octave, volume, balance, solo/mute.")
        except Exception:
            self.mixer_summary_label.setText("Track summary unavailable.")
            self.tool_note_label.setText("Open Track Mixer to inspect track settings.")

    def _rebuild_takes_table(self) -> None:
        while self.takes_layout.count():
            item = self.takes_layout.takeAt(0)
            self._delete_layout_item(item)
        self.take_checkboxes.clear()
        self.take_play_buttons.clear()
        self.take_stop_buttons.clear()
        self.take_select_buttons.clear()

        for col in range(6):
            self.takes_layout.setColumnStretch(col, 0)
        self.takes_layout.setColumnMinimumWidth(0, 50)
        self.takes_layout.setColumnMinimumWidth(1, 32)
        self.takes_layout.setColumnMinimumWidth(2, 54)
        self.takes_layout.setColumnMinimumWidth(4, 100)
        self.takes_layout.setColumnMinimumWidth(5, 100)

        if not self.project.source_midi_path and not self.project.takes:
            empty = QLabel("Load a MIDI to begin. Numbered versions will appear here.")
            empty.setObjectName("EmptyState")
            empty.setWordWrap(True)
            self.takes_layout.addWidget(empty, 0, 0, 1, 6)
            self.takes_layout.setColumnStretch(3, 1)
            return
        if self.project.source_midi_path and not self._playback_ready:
            message = "Preparing original for playback…" if self._playback_preparing else "Original is not ready to play yet."
            if self._playback_prepare_error:
                message = f"Playback preparation failed: {self._playback_prepare_error}"
            empty = QLabel(message)
            empty.setObjectName("PreparingState")
            empty.setWordWrap(True)
            self.takes_layout.addWidget(empty, 0, 0, 1, 6)
            self.takes_layout.setColumnStretch(3, 1)
            return

        headers = ["Export?", "★", "#", "Saved sound", "Listen", "Manage"]
        for col, text in enumerate(headers):
            header = QLabel(text)
            header.setObjectName("TableHeader")
            header.setToolTip({
                "Export?": "Check saved versions you want to export. Pending rows cannot export until played once.",
                "★": "Favorite likely winners. Favorites are preserved in projects and export manifests.",
                "#": "Original plus numeric-only saved version numbers.",
                "Saved sound": "Variation, expression, timing, tempo, volume, instruments, and mixer state saved with this row.",
                "Listen": "Play or stop this exact row.",
                "Manage": "Mark Best, add note, duplicate, move, or delete numbered versions. Original is never deleted.",
            }.get(text, ""))
            self.takes_layout.addWidget(header, 0, col)
        rows: list[dict[str, Any]] = []
        if self.project.source_midi_path:
            rows.append(self._original_take_row())
        take_rows = list(self.project.takes or [])
        if self.show_favorites_only:
            take_rows = [take for take in take_rows if bool(take.get("favorite"))]
        rows.extend(take_rows)
        if self._variation_dirty:
            rows.append(self._pending_variation_row())
        for row, item in enumerate(rows, start=1):
            key = str(item.get("id") or ("original" if row == 1 else f"take_{row}"))
            has_path = bool(item.get("path"))
            is_pending = key == "pending"
            is_original = key == "original"

            cb = QCheckBox()
            cb.setToolTip("Check this row to include it in export." if has_path and not is_pending else "Play pending versions first; then they can be exported.")
            cb.setChecked(key in self.export_checked_keys)
            cb.setEnabled(has_path and key != "candidate" and not is_pending)
            cb.stateChanged.connect(lambda state=0, k=key: self._set_export_checked(k, state))
            self.take_checkboxes[key] = cb
            self.takes_layout.addWidget(cb, row, 0, alignment=Qt.AlignCenter)

            star = QPushButton("★" if bool(item.get("favorite")) else "☆")
            star.setObjectName("FavoriteButton" if bool(item.get("favorite")) else "ToolButton")
            star.setFixedWidth(32)
            star.setEnabled(key.startswith("take_"))
            star.setToolTip("Mark or unmark this numbered version as a favorite.")
            star.clicked.connect(lambda _checked=False, k=key: self.toggle_take_favorite(k))
            self.takes_layout.addWidget(star, row, 1)

            name = str(item.get("name", "Take"))
            select = QPushButton(name)
            select.setToolTip("Select this row for the main transport." if has_path else "This pending number becomes a saved version when you press Play.")
            select.setCheckable(True)
            select.setChecked(self._is_selected_row(key))
            select.setEnabled(has_path)
            select.clicked.connect(lambda _checked=False, k=key: self.select_take_row(k))
            self.take_select_buttons[key] = select
            self.takes_layout.addWidget(select, row, 2)

            status_text = self._take_row_display_text(item)
            status = ElidedLabel(status_text)
            status.setObjectName("VersionStatus")
            status.setToolTip(self._take_settings_tooltip(item))
            status.setMinimumWidth(0)
            status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            self.takes_layout.addWidget(status, row, 3)

            mini = QHBoxLayout()
            mini.setSpacing(4)
            play = QPushButton("Play")
            play.setObjectName("PlayButton")
            play.setFixedWidth(48)
            play.setEnabled(has_path or is_pending)
            if is_pending:
                play.setToolTip("Create this numbered version and hear the current variation/mixer settings.")
                play.clicked.connect(lambda _checked=False: self.create_preview())
            else:
                play.clicked.connect(lambda _checked=False, k=key: self.play_take_row(k))
            stop = QPushButton("Stop")
            stop.setObjectName("StopButton")
            stop.setFixedWidth(48)
            stop.setEnabled(False)
            stop.clicked.connect(self.stop_playback)
            self.take_play_buttons[key] = play
            self.take_stop_buttons[key] = stop
            mini.addWidget(play)
            mini.addWidget(stop)
            mini.addStretch(1)
            self.takes_layout.addLayout(mini, row, 4)

            manage = QHBoxLayout()
            manage.setSpacing(3)
            if key.startswith("take_"):
                best = QPushButton("Best ✓" if bool(item.get("best")) else "Best")
                best.setObjectName("BestButton" if bool(item.get("best")) else "ToolButton")
                best.setToolTip("Mark this as the single keeper/winner take. Best is separate from ★ favorites.")
                best.clicked.connect(lambda _checked=False, k=key: self.toggle_take_best(k))
                menu_btn = QPushButton("More…")
                menu_btn.setObjectName("ToolButton")
                menu_btn.setToolTip("Use, note, duplicate, move, or delete this take.")
                menu_btn.clicked.connect(lambda _checked=False, k=key, b=menu_btn: self.show_take_manage_menu(k, b))
                best.setFixedWidth(46)
                menu_btn.setFixedWidth(50)
                manage.addWidget(best)
                manage.addWidget(menu_btn)
            elif is_original:
                original_note = QLabel("Original locked")
                original_note.setObjectName("SubtleText")
                original_note.setToolTip("Original always stays as loaded; mixer/variation changes become numbered versions.")
                manage.addWidget(original_note)
            else:
                blank = QLabel("")
                manage.addWidget(blank)
            manage.addStretch(1)
            self.takes_layout.addLayout(manage, row, 5)
        self.takes_layout.setColumnStretch(3, 1)
        spacer_row = len(rows) + 1
        self.takes_layout.addItem(QSpacerItem(1, 1, QSizePolicy.Minimum, QSizePolicy.Expanding), spacer_row, 0, 1, 6)
        self.takes_layout.setRowStretch(spacer_row, 1)

    def show_take_manage_menu(self, key: str, button: QPushButton) -> None:
        """Compact row actions so the Takes table does not clip on narrower windows."""
        if not key.startswith("take_"):
            return
        menu = QMenu(self)
        use_action = menu.addAction("Use as branch point")
        note_action = menu.addAction("Edit note…")
        dup_action = menu.addAction("Duplicate take")
        menu.addSeparator()
        up_action = menu.addAction("Move up")
        down_action = menu.addAction("Move down")
        menu.addSeparator()
        delete_action = menu.addAction("Delete take")

        use_action.triggered.connect(lambda _checked=False, k=key: self.use_take_settings(k))
        note_action.triggered.connect(lambda _checked=False, k=key: self.edit_take_note(k))
        dup_action.triggered.connect(lambda _checked=False, k=key: self.duplicate_take(k))
        up_action.triggered.connect(lambda _checked=False, k=key: self.move_take(k, -1))
        down_action.triggered.connect(lambda _checked=False, k=key: self.move_take(k, 1))
        delete_action.triggered.connect(lambda _checked=False, k=key: self.delete_take_row(k))
        menu.exec(button.mapToGlobal(button.rect().bottomLeft()))

    def _take_row_display_text(self, item: dict[str, Any]) -> str:
        note = str(item.get("note", ""))
        user_note = str(item.get("user_note") or "").strip()
        prefix = "BEST · " if bool(item.get("best")) else ""
        if bool(item.get("favorite")):
            prefix += "★ "
        if user_note:
            prefix += f"{user_note} · "
        if "Inst:" in note:
            return prefix + note
        if str(item.get("id")) == "original":
            return note
        instrument_text = self._take_instrument_summary(item, include_unchanged=False)
        octave_text = self._take_octave_summary(item)
        mixer_bits = " · ".join(bit for bit in [instrument_text if instrument_text != "Inst: unchanged" else "", octave_text] if bit)
        if mixer_bits:
            return prefix + (f"{note} · {mixer_bits}" if note else mixer_bits)
        if str(item.get("id")) == "pending":
            return f"{note} · {instrument_text}" if note else instrument_text
        return prefix + note

    def _take_settings_tooltip(self, item: dict[str, Any]) -> str:
        if str(item.get("id")) == "original":
            return "Original imported MIDI. Playback uses loaded MIDI instruments; mixer/variation edits become numbered versions."
        if str(item.get("id")) == "pending":
            return "Pending numbered version. Press Play to create it with the current variation, tempo, volume, and mixer settings."
        mixer = item.get("mixer") or {}
        programs_map = dict(mixer.get("programs", {}) or {})
        programs = len(programs_map)
        volumes = len(mixer.get("volumes", {}) or {})
        pans = len(mixer.get("pans", {}) or {})
        octaves = len(mixer.get("octaves", {}) or {})
        solos = len(mixer.get("solos", {}) or {})
        mutes = len(mixer.get("mutes", {}) or {})
        if programs_map:
            inst_lines = []
            for key, program in sorted(programs_map.items(), key=lambda kv: int(kv[0]) if str(kv[0]).isdigit() else 9999):
                display = self._display_track_number_for_source_key(str(key))
                track_text = f"Track {display}" if display is not None else f"Track {key}"
                inst_lines.append(f"{track_text}: {int(program) + 1:03d} {self._program_short_name(int(program))}")
            instruments = "Changed instruments:\n" + "\n".join(inst_lines)
        else:
            instruments = self._take_instrument_summary(item, include_unchanged=True, max_items=99)
        favorite = "yes" if bool(item.get("favorite")) else "no"
        best = "yes" if bool(item.get("best")) else "no"
        user_note = str(item.get("user_note") or "")
        return (
            f"Saved version {item.get('name', '')}\n"
            f"Best: {best}\n"
            f"Favorite: {favorite}\n"
            f"Note: {user_note}\n"
            f"Variation: {item.get('note', '')}\n"
            f"Tempo: {int(item.get('tempo_percent', 100) or 100)}%\n"
            f"Volume: {self._take_volume_percent(item)}%\n"
            f"Score guidance: {self._take_score_guidance_tooltip(item)}\n"
            f"Audio guidance: {self._take_audio_guidance_tooltip(item)}\n"
            f"{instruments}\n"
            f"Mixer changes: instruments {programs}, volume {volumes}, pan {pans}, solo {solos}, mute {mutes}"
        )

    def _take_score_guidance_tooltip(self, item: dict[str, Any]) -> str:
        guidance = item.get("score_guidance") or {}
        if not guidance or not guidance.get("active"):
            return "off"
        strength = guidance.get("strength") or "Light"
        summary = guidance.get("summary") or guidance.get("source_format") or "score dynamics"
        alignment = guidance.get("alignment") or ""
        confidence = guidance.get("alignment_confidence") or ""
        repair = guidance.get("alignment_repair") or ""
        pieces = [f"on · {strength} · {summary}"]
        if alignment:
            pieces.append(str(alignment))
        if confidence:
            pieces.append(f"confidence {confidence}")
        if repair:
            pieces.append(str(repair))
        return " · ".join(pieces)


    def _take_audio_guidance_tooltip(self, item: dict[str, Any]) -> str:
        guidance = item.get("audio_guidance") or {}
        if not guidance or not guidance.get("active"):
            return "off"
        strength = guidance.get("strength") or "Light"
        summary = guidance.get("summary") or "reference audio"
        curve_segments = guidance.get("curve_segments") or 0
        return f"on · {strength} · {summary} · {curve_segments} contour segments"

    def _set_export_checked(self, key: str, state: int) -> None:
        if state:
            self.export_checked_keys.add(key)
        else:
            self.export_checked_keys.discard(key)
        self._update_action_states()

    def _delete_layout_item(self, item) -> None:
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()
            return
        layout = item.layout()
        if layout is not None:
            while layout.count():
                child = layout.takeAt(0)
                self._delete_layout_item(child)

    def _is_selected_row(self, key: str) -> bool:
        if key == "original":
            return self.selected_review_kind == "original"
        if key.startswith("take_") and self.selected_review_kind == "take":
            return str(key) == str(self.selected_take_id)
        return False

    def _next_variation_number(self) -> int:
        return int(getattr(self.project, "audition_counter", 0) or 0) + 1

    def _version_note_text(self, level: str, expression: str, timing: str, score_detail: str = "") -> str:
        tempo = int(self.project.playback_tempo_percent or 100)
        volume = self._master_volume_percent()
        mix_count = sum(
            len(v or {}) for v in [
                self.project.track_program_overrides,
                self.project.track_volume_overrides,
                self.project.track_pan_overrides,
                self.project.track_octave_overrides,
                self.project.track_solo_overrides,
                self.project.track_mute_overrides,
            ]
        )
        mix_text = f" · Mix {mix_count}" if mix_count else ""
        score_text = f" · {score_detail}" if score_detail else (f" · Score guide {getattr(self.project, 'score_guidance_strength', 'Light')}" if self._score_guidance_active() else "")
        audio_text = f" · Audio guide {getattr(self.project, 'reference_audio_guidance_strength', 'Light')}" if self._reference_audio_guidance_active() else ""
        instrument_text = self._take_instrument_summary({
            "mixer": {"programs": dict(self.project.track_program_overrides or {})}
        }, include_unchanged=False)
        if instrument_text:
            mix_text += f" · {instrument_text}"
        octave_text = self._take_octave_summary({"mixer": {"octaves": dict(self.project.track_octave_overrides or {})}})
        if octave_text:
            mix_text += f" · {octave_text}"
        return f"{level} · Expr {expression} · {timing} · T {tempo}% · V {volume}%{mix_text}{score_text}{audio_text}"

    def _program_short_name(self, program: int) -> str:
        program = max(0, min(127, int(program)))
        return GM_PROGRAM_NAMES[program]

    def _display_track_number_for_source_key(self, key: str) -> int | None:
        for display_index, info in enumerate(self._music_track_infos(), start=1):
            if str(info.index) == str(key):
                return display_index
        try:
            return int(key) + 1
        except Exception:
            return None

    def _take_instrument_summary(self, item: dict[str, Any], include_unchanged: bool = False, max_items: int = 2) -> str:
        """Compact instrument text for the Takes row.

        Shows changed instruments first because that is what the PM needs when
        comparing many numbered versions. Original/unchanged rows can request a
        short loaded-instrument summary for reference.
        """
        mixer = item.get("mixer") or {}
        programs = dict(mixer.get("programs", {}) or {})
        if programs:
            pieces: list[str] = []
            for key, program in sorted(programs.items(), key=lambda kv: int(kv[0]) if str(kv[0]).isdigit() else 9999):
                display = self._display_track_number_for_source_key(str(key))
                track_text = f"T{display}" if display is not None else f"T{key}"
                pieces.append(f"{track_text} {self._program_short_name(int(program))}")
            shown = pieces[:max_items]
            more = f" +{len(pieces) - len(shown)}" if len(pieces) > len(shown) else ""
            return "Inst: " + ", ".join(shown) + more
        if include_unchanged and self.project.source_midi_path:
            pieces = []
            for display_index, info in enumerate(self._music_track_infos(), start=1):
                program = int(info.programs[0]) if info.programs else 0
                pieces.append(f"T{display_index} {self._program_short_name(program)}")
            shown = pieces[:max_items]
            more = f" +{len(pieces) - len(shown)}" if len(pieces) > len(shown) else ""
            return ("Inst: " + ", ".join(shown) + more) if shown else "Inst: none"
        return "Inst: unchanged"

    def _take_octave_summary(self, item: dict[str, Any], max_items: int = 3) -> str:
        mixer = item.get("mixer") or {}
        octaves = dict(mixer.get("octaves", {}) or {})
        octaves = {str(k): int(v) for k, v in octaves.items() if int(v or 0) != 0}
        if not octaves:
            return ""
        pieces: list[str] = []
        for key, shift in sorted(octaves.items(), key=lambda kv: int(kv[0]) if str(kv[0]).isdigit() else 9999):
            display = self._display_track_number_for_source_key(str(key))
            track_text = f"T{display}" if display is not None else f"T{key}"
            pieces.append(f"{track_text} {shift:+d} oct")
        shown = pieces[:max_items]
        more = f" +{len(pieces) - len(shown)}" if len(pieces) > len(shown) else ""
        return "Oct: " + ", ".join(shown) + more

    def _pending_variation_row(self) -> dict[str, Any]:
        level = LEVEL_LABELS.get(self.create_level_key, "Light")
        note = self._version_note_text(level, self.expression_label, self.project.timing_feel)
        score_on = self._score_guidance_active()
        audio_on = self._reference_audio_guidance_active()
        if score_on and audio_on:
            note = f"New score+audio-guided TAKE · {note}"
        elif score_on:
            note = f"New score-guided TAKE · {note}"
        elif audio_on:
            note = f"New audio-guided TAKE · {note}"
        else:
            note = f"New TAKE · {note}"
        return {
            "id": "pending",
            "name": str(self._next_variation_number()),
            "path": None,
            "note": note,
            **self._current_take_settings_snapshot(),
        }

    def _current_take_settings_snapshot(self) -> dict[str, Any]:
        return {
            "volume_percent": self._master_volume_percent(),
            "tempo_percent": int(self.project.playback_tempo_percent or 100),
            "score_guidance": self._current_score_guidance_snapshot(),
            "audio_guidance": self._current_audio_guidance_snapshot(),
            "mixer": {
                "programs": dict(self.project.track_program_overrides or {}),
                "volumes": dict(self.project.track_volume_overrides or {}),
                "pans": dict(self.project.track_pan_overrides or {}),
                "octaves": dict(self.project.track_octave_overrides or {}),
                "solos": dict(self.project.track_solo_overrides or {}),
                "mutes": dict(self.project.track_mute_overrides or {}),
            },
        }

    def _original_take_row(self) -> dict[str, Any]:
        ready = bool(self.project.source_midi_path and self._playback_ready and not self._playback_preparing)
        if self.project.source_midi_path and not ready:
            note = "Preparing playback…" if self._playback_preparing else "Playback not ready"
            if self._playback_prepare_error:
                note = f"Preparation failed: {self._playback_prepare_error}"
            return {
                "id": "original",
                "name": "Original",
                "path": None,
                "note": note,
                "volume_percent": self._master_volume_percent(),
                "tempo_percent": int(self.project.playback_tempo_percent or 100),
                "mixer": {"programs": {}, "volumes": {}, "pans": {}, "octaves": {}, "solos": {}, "mutes": {}},
            }
        return {
            "id": "original",
            "name": "Original",
            "path": self.project.source_midi_path if ready else None,
            "note": (f"Imported MIDI · {self._take_instrument_summary({'mixer': {'programs': {}}}, include_unchanged=True)}" if ready else "Open a MIDI first"),
            "volume_percent": self._master_volume_percent(),
            "tempo_percent": int(self.project.playback_tempo_percent or 100),
            "mixer": {
                "programs": {},
                "volumes": {},
                "pans": {},
                "octaves": {},
                "solos": {},
                "mutes": {},
            },
        }

    def _take_by_key(self, key: str) -> dict[str, Any] | None:
        if key == "original":
            return self._original_take_row()
        if key.startswith("take_"):
            for item in self.project.takes or []:
                if str(item.get("id")) == str(key):
                    return item
        return None

    def select_take_row(self, key: str) -> None:
        if key == "original":
            self.selected_review_kind = "original"
            self.selected_take_id = None
        elif key.startswith("take_"):
            if not self._take_by_key(key):
                return
            self.selected_review_kind = "take"
            self.selected_take_id = str(key)
        else:
            return
        self._rebuild_takes_table()
        item = self._take_by_key(key)
        self.status_label.setText(f"Selected {item.get('name') if item else key}")
        self._update_action_states()

    def _selected_play_item(self) -> dict[str, Any] | None:
        if self.selected_review_kind == "take" and self.selected_take_id:
            item = self._take_by_key(self.selected_take_id)
            if item:
                return item
        original = self._original_take_row()
        return original if original.get("path") else None

    def set_level(self, key: str) -> None:
        if key in LEVEL_LABELS:
            self.create_level_key = key
            self._mark_variation_changed(f"Variation changed: {LEVEL_LABELS[key]}. Play the new numbered row in Takes to create/hear it.")

    def set_expression(self, label: str) -> None:
        values = dict(EXPRESSION_LEVELS)
        self.expression_label = label
        self.project.expressiveness = int(values.get(label, 0))
        self._mark_variation_changed(f"Expression changed: {label}. Play the new numbered row in Takes to create/hear it.")

    def set_timing_feel(self, label: str) -> None:
        if label not in TIMING_FEELS:
            label = "Natural"
        self.project.timing_feel = label
        self._mark_variation_changed(f"Timing changed: {label}. Play the new numbered row in Takes to create/hear it.")

    def set_score_dynamics_enabled(self, checked: bool) -> None:
        if bool(getattr(self.project, "score_dynamics_enabled", False)) == bool(checked):
            return
        self.project.score_dynamics_enabled = bool(checked)
        self.project.score_guidance_enabled = bool(checked)
        if checked and not self._score_guidance_available():
            if getattr(self.project, "score_source_path", None):
                self._set_score_analysis_notice("Use score guide requested — analyzing the attached score first…", "busy")
                self.status_label.setText("Analyzing score for score guide…")
                self.analyze_score_source()
            else:
                self.project.score_dynamics_enabled = False
                self.project.score_guidance_enabled = False
                self.status_label.setText("Attach a matching PDF score first.")
        else:
            self._mark_variation_changed("Score guide changed. Play the new pending TAKE row below to create/hear it.")
        self._refresh_source_references()
        self._update_action_states()

    def set_score_guidance_strength(self, label: str) -> None:
        if label not in {"Light", "Medium", "Strong"}:
            label = "Light"
        if str(getattr(self.project, "score_guidance_strength", "Light")) == label:
            return
        self.project.score_guidance_strength = label
        self._mark_variation_changed(f"Score influence changed: {label}. Play the new pending TAKE row below to create/hear it.")
        self._refresh_source_references()
        self._update_action_states()

    def _score_guidance_available(self) -> bool:
        score_path = getattr(self.project, "score_source_path", None)
        score_format = (getattr(self.project, "score_source_format", None) or "").lower()
        if not score_path or not Path(score_path).exists():
            return False
        if score_format == "lilypond":
            return True
        if score_format == "pdf":
            return bool(
                getattr(self.project, "score_guide_available", False)
                or getattr(self.project, "score_omr_dynamic_sequence", [])
                or getattr(self.project, "score_omr_articulation_sequence", [])
                or getattr(self.project, "score_omr_tempo_words", [])
            )
        return False

    def _score_guidance_active(self) -> bool:
        return bool(getattr(self.project, "score_dynamics_enabled", False) and self._score_guidance_available())

    def _score_guidance_plan(self):
        if not self._score_guidance_active():
            return None
        score_path = getattr(self.project, "score_source_path")
        strength = str(getattr(self.project, "score_guidance_strength", "Light") or "Light")
        score_format = (getattr(self.project, "score_source_format", None) or "").lower()
        if score_format == "pdf":
            omr_sequence = list(getattr(self.project, "score_omr_dynamic_sequence", []) or [])
            omr_articulations = list(getattr(self.project, "score_omr_articulation_sequence", []) or [])
            omr_tempos = list(getattr(self.project, "score_omr_tempo_words", []) or [])
            if omr_sequence or omr_articulations or omr_tempos:
                return score_guidance_from_dynamic_sequence(
                    source_path=str(getattr(self.project, "score_omr_output_path", None) or score_path),
                    source_format="PDF OMR",
                    dynamic_sequence=omr_sequence,
                    articulation_sequence=omr_articulations,
                    tempo_words=omr_tempos,
                    dynamics_enabled=True,
                    strength=strength,
                    summary_prefix=f"OMR score guide ({getattr(self.project, 'score_focus_summary', None) or self._score_focus_label()})",
                    detail_suffix=f"Generated internally from the attached PDF score focus: {getattr(self.project, 'score_focus_summary', None) or self._score_focus_label()}.",
                    alignment_shift_percent=float(getattr(self.project, "score_alignment_shift_percent", 0.0) or 0.0),
                    alignment_stretch_percent=float(getattr(self.project, "score_alignment_stretch_percent", 100.0) or 100.0),
                )
            return create_pdf_score_guidance(
                score_path,
                dynamics_enabled=True,
                strength=strength,
                page_range=self._score_focus_page_range(),
                alignment_shift_percent=float(getattr(self.project, "score_alignment_shift_percent", 0.0) or 0.0),
                alignment_stretch_percent=float(getattr(self.project, "score_alignment_stretch_percent", 100.0) or 100.0),
            )
        return create_lilypond_score_guidance(
            score_path,
            dynamics_enabled=True,
            strength=strength,
        )

    def _current_score_guidance_snapshot(self) -> dict[str, Any]:
        return {
            "dynamics_enabled": bool(getattr(self.project, "score_dynamics_enabled", False)),
            "active": self._score_guidance_active(),
            "strength": str(getattr(self.project, "score_guidance_strength", "Light") or "Light"),
            "source_format": getattr(self.project, "score_source_format", None),
            "source_path": getattr(self.project, "score_source_path", None),
            "summary": getattr(self.project, "score_analysis_summary", None) or getattr(self.project, "score_intent_summary", None),
            "alignment": getattr(self.project, "score_alignment_summary", None),
            "guide_available": bool(getattr(self.project, "score_guide_available", False)),
            "omr_output": getattr(self.project, "score_omr_output_path", None),
            "omr_summary": getattr(self.project, "score_omr_summary", None),
            "score_focus": getattr(self.project, "score_focus_summary", None) or self._score_focus_label(),
            "score_focus_mode": getattr(self.project, "score_focus_mode", None),
            "score_focus_start_page": getattr(self.project, "score_focus_start_page", None),
            "score_focus_end_page": getattr(self.project, "score_focus_end_page", None),
            "guide_kinds": list(self._score_guidance_plan().guidance_kinds) if self._score_guidance_plan() else [],
            "analysis_details": getattr(self.project, "score_analysis_details_summary", None),
            "alignment_confidence": getattr(self.project, "score_alignment_confidence", None),
            "alignment_repair": getattr(self.project, "score_alignment_repair_summary", None),
            "alignment_shift_percent": getattr(self.project, "score_alignment_shift_percent", 0),
            "alignment_stretch_percent": getattr(self.project, "score_alignment_stretch_percent", 100),
            "alignment_shift_beats": getattr(self.project, "score_alignment_shift_beats", 0.0),
            "alignment_length_beats": getattr(self.project, "score_alignment_length_beats", 0.0),
        }


    def set_audio_guidance_enabled(self, checked: bool) -> None:
        if bool(getattr(self.project, "reference_audio_guidance_enabled", False)) == bool(checked):
            return
        if checked and not self._reference_audio_guidance_available():
            self.project.reference_audio_guidance_enabled = False
            if hasattr(self, "audio_guidance_check"):
                self.audio_guidance_check.blockSignals(True)
                self.audio_guidance_check.setChecked(False)
                self.audio_guidance_check.blockSignals(False)
            self.status_label.setText("Load a reference audio file with usable dynamics/attack/pacing contours first.")
        else:
            self.project.reference_audio_guidance_enabled = bool(checked)
            self._mark_variation_changed("Audio guide changed. Play the new pending TAKE row below to create/hear it.")
        self._refresh_source_references()
        self._update_action_states()

    def set_audio_guidance_strength(self, label: str) -> None:
        if label not in {"Light", "Medium", "Strong"}:
            label = "Light"
        if str(getattr(self.project, "reference_audio_guidance_strength", "Light")) == label:
            return
        self.project.reference_audio_guidance_strength = label
        self._mark_variation_changed(f"Audio influence changed: {label}. Play the new pending TAKE row below to create/hear it.")
        self._refresh_source_references()
        self._update_action_states()

    def _reference_audio_guidance_available(self) -> bool:
        audio_path = getattr(self.project, "reference_audio_path", None)
        loudness = list(getattr(self.project, "reference_audio_loudness_curve", []) or [])
        attack = list(getattr(self.project, "reference_audio_attack_curve", []) or [])
        motion = list(getattr(self.project, "reference_audio_motion_curve", []) or [])
        return bool(audio_path and Path(audio_path).exists() and any(len(curve) >= 4 for curve in (loudness, attack, motion)))

    def _reference_audio_guidance_active(self) -> bool:
        return bool(getattr(self.project, "reference_audio_guidance_enabled", False) and self._reference_audio_guidance_available())

    def _reference_audio_guidance_plan(self):
        if not self._reference_audio_guidance_active():
            return None
        return create_reference_audio_guidance(
            source_path=str(getattr(self.project, "reference_audio_path", "") or ""),
            source_format=str(getattr(self.project, "reference_audio_format", None) or "Audio"),
            loudness_curve=list(getattr(self.project, "reference_audio_loudness_curve", []) or []),
            attack_curve=list(getattr(self.project, "reference_audio_attack_curve", []) or []),
            motion_curve=list(getattr(self.project, "reference_audio_motion_curve", []) or []),
            strength=str(getattr(self.project, "reference_audio_guidance_strength", "Light") or "Light"),
            peak_percent=getattr(self.project, "reference_audio_peak_percent", None),
            rms_percent=getattr(self.project, "reference_audio_rms_percent", None),
            alignment_shift_percent=float(getattr(self.project, "reference_audio_alignment_shift_percent", 0.0) or 0.0),
            alignment_stretch_percent=float(getattr(self.project, "reference_audio_alignment_stretch_percent", 100.0) or 100.0),
        )

    def _current_audio_guidance_snapshot(self) -> dict[str, Any]:
        plan = self._reference_audio_guidance_plan()
        return {
            "enabled": bool(getattr(self.project, "reference_audio_guidance_enabled", False)),
            "active": self._reference_audio_guidance_active(),
            "strength": str(getattr(self.project, "reference_audio_guidance_strength", "Light") or "Light"),
            "source_format": getattr(self.project, "reference_audio_format", None),
            "source_path": getattr(self.project, "reference_audio_path", None),
            "summary": plan.summary if plan else getattr(self.project, "reference_audio_analysis_summary", None),
            "curve_segments": len(list(getattr(self.project, "reference_audio_loudness_curve", []) or [])),
            "attack_segments": len(list(getattr(self.project, "reference_audio_attack_curve", []) or [])),
            "motion_segments": len(list(getattr(self.project, "reference_audio_motion_curve", []) or [])),
            "peak_percent": getattr(self.project, "reference_audio_peak_percent", None),
            "rms_percent": getattr(self.project, "reference_audio_rms_percent", None),
            "alignment_shift_percent": getattr(self.project, "reference_audio_alignment_shift_percent", 0.0),
            "alignment_stretch_percent": getattr(self.project, "reference_audio_alignment_stretch_percent", 100.0),
            "alignment_shift_beats": getattr(self.project, "reference_audio_alignment_shift_beats", 0.0),
            "alignment_length_beats": getattr(self.project, "reference_audio_alignment_length_beats", 0.0),
        }

    def _sync_audio_guidance_widgets(self) -> None:
        if not hasattr(self, "audio_guidance_check"):
            return
        available = self._reference_audio_guidance_available()
        active = bool(getattr(self.project, "reference_audio_guidance_enabled", False)) and available
        self.audio_guidance_check.blockSignals(True)
        self.audio_guidance_check.setChecked(active)
        self.audio_guidance_check.blockSignals(False)
        strength = str(getattr(self.project, "reference_audio_guidance_strength", "Light") or "Light")
        if strength not in {"Light", "Medium", "Strong"}:
            strength = "Light"
            self.project.reference_audio_guidance_strength = strength
        self.audio_guidance_strength_combo.blockSignals(True)
        self.audio_guidance_strength_combo.setCurrentText(strength)
        self.audio_guidance_strength_combo.blockSignals(False)
        self.audio_guidance_check.setToolTip(
            "Use analyzed reference-audio dynamics, attack, and pacing contours to guide new takes. This is still conservative, not full note-level audio alignment yet."
            if available else "Audio guide appears only after usable dynamics/attack/pacing contours exist. Load WAV or an FFmpeg-decodable audio file."
        )

    def set_score_focus_mode(self, label: str) -> None:
        if not hasattr(self, "score_focus_combo"):
            return
        mode = self._score_focus_mode_from_label(label)
        if getattr(self.project, "score_focus_mode", "all") != mode:
            self.project.score_focus_mode = mode
        self.set_score_focus_pages(refresh=False)
        self._clear_score_analysis_for_focus_change()
        self._sync_score_focus_widgets()
        self._refresh_all()
        if mode == "all":
            self._set_score_analysis_notice("Focus: all pages. For full movements/suites, choose the page range before Analyze Score.", "info")
        else:
            self._set_score_analysis_notice(f"Focus: {self._score_focus_label()}. Click Analyze Score when ready.", "info")

    def set_score_focus_pages(self, refresh: bool = True) -> None:
        if not hasattr(self, "score_focus_start_edit"):
            return
        def parse(text: str) -> int | None:
            try:
                value = int(str(text or "").strip())
                return value if value > 0 else None
            except Exception:
                return None
        mode = getattr(self.project, "score_focus_mode", "all") or "all"
        start = parse(self.score_focus_start_edit.text())
        end = parse(self.score_focus_end_edit.text())
        if mode in {"single", "range"} and not start:
            start = 1
        if mode == "single":
            end = start
        elif mode == "range" and start and not end:
            end = start
        self.project.score_focus_start_page = start
        self.project.score_focus_end_page = end
        self.project.score_focus_summary = self._score_focus_label()
        if refresh:
            self._clear_score_analysis_for_focus_change()
            self._sync_score_focus_widgets()
            self._refresh_all()

    def _score_focus_mode_from_label(self, label: str) -> str:
        text = str(label or "").strip().lower()
        if text.startswith("single"):
            return "single"
        if text.startswith("page range"):
            return "range"
        return "all"

    def _score_focus_label_for_mode(self, mode: str) -> str:
        return {"single": "Single page", "range": "Page range"}.get(mode, "All pages")

    def _score_focus_page_range(self) -> tuple[int, int] | None:
        mode = getattr(self.project, "score_focus_mode", "all") or "all"
        page_count = getattr(self.project, "score_focus_page_count", None)
        if mode == "all":
            return None
        start = getattr(self.project, "score_focus_start_page", None)
        end = getattr(self.project, "score_focus_end_page", None)
        try:
            start_i = int(start) if start else None
            end_i = int(end) if end else None
        except Exception:
            return None
        if not start_i:
            return None
        if mode == "single":
            end_i = start_i
        if not end_i:
            end_i = start_i
        return normalize_page_range((start_i, end_i), page_count)

    def _score_focus_label(self) -> str:
        page_count = getattr(self.project, "score_focus_page_count", None)
        return page_range_label(self._score_focus_page_range(), page_count)

    def _clear_score_analysis_for_focus_change(self) -> None:
        # A focus change invalidates PDF-derived analysis and OMR output. The
        # attached PDF stays, but the app should not reuse dynamics from the
        # wrong movement/page range.
        if (getattr(self.project, "score_source_format", "") or "").lower() != "pdf":
            return
        self.project.score_analysis_status = self.project.score_source_status
        self.project.score_analysis_summary = self.project.score_source_summary
        self.project.score_analysis_detail = self.project.score_source_detail
        self.project.score_alignment_summary = None
        self.project.score_alignment_detail = None
        self.project.score_analysis_details_summary = None
        self.project.score_analysis_details_detail = None
        self.project.score_alignment_confidence = None
        self.project.score_alignment_repair_needed = False
        self.project.score_alignment_repair_summary = None
        self.project.score_guide_available = False
        self.project.score_guidance_enabled = False
        self.project.score_dynamics_enabled = False
        self.project.score_omr_output_path = None
        self.project.score_omr_summary = None
        self.project.score_omr_detail = None
        self.project.score_omr_dynamic_sequence = []
        self.project.score_omr_articulation_sequence = []
        self.project.score_omr_tempo_words = []
        self.project.score_omr_fermata_words = []

    def _sync_score_focus_widgets(self) -> None:
        if not hasattr(self, "score_focus_combo"):
            return
        mode = getattr(self.project, "score_focus_mode", "all") or "all"
        label = self._score_focus_label_for_mode(mode)
        self.score_focus_combo.blockSignals(True)
        self.score_focus_combo.setCurrentText(label)
        self.score_focus_combo.blockSignals(False)
        page_count = getattr(self.project, "score_focus_page_count", None)
        start = getattr(self.project, "score_focus_start_page", None)
        end = getattr(self.project, "score_focus_end_page", None)
        if mode == "single" and not start:
            start = 1
        if mode == "range" and not start:
            start = 1
        self.score_focus_start_edit.blockSignals(True)
        self.score_focus_end_edit.blockSignals(True)
        self.score_focus_start_edit.setText("" if mode == "all" or not start else str(start))
        self.score_focus_end_edit.setText("" if mode != "range" or not end else str(end))
        self.score_focus_start_edit.blockSignals(False)
        self.score_focus_end_edit.blockSignals(False)
        tooltip = f"Current score focus: {self._score_focus_label()}."
        if page_count and page_count > 6:
            tooltip += " This PDF has multiple pages; focus the movement before analysis if needed."
        for widget in (self.score_focus_combo, self.score_focus_start_edit, self.score_focus_end_edit):
            widget.setToolTip(tooltip)

    def _apply_score_progressive_disclosure(self) -> None:
        """Hide score/reference actions until their prerequisites are met.

        Disabled controls were still visually noisy and invited the question
        "why doesn't this work?". The main workspace now exposes only the next
        valid action: load a score first, then analyze it, then create a
        score-guided take once guidance exists.
        """
        if not hasattr(self, "load_score_source_btn"):
            return
        score_path = getattr(self.project, "score_source_path", None)
        score_loaded = bool(score_path)
        score_format = (getattr(self.project, "score_source_format", None) or "").lower()
        pdf_loaded = bool(score_loaded and score_format == "pdf")
        guide_available = self._score_guidance_available()
        midi_loaded = bool(getattr(self.project, "source_midi_path", None))
        omr_summary = bool(getattr(self.project, "score_omr_summary", None))
        project_omr_status = str(getattr(self.project, "score_omr_engine_status", "") or "").lower()
        project_omr_ready = any(word in project_omr_status for word in ("ready", "installed", "confirmed", "complete", "success")) or omr_summary
        omr_ready = bool(locate_omr_engine(self.settings.get("audiveris_path")).available or project_omr_ready)
        # Avoid duplicate "OMR ready" badges. The inline analysis notice is the
        # proximal status for Analyze Score; the OMR row only appears when setup
        # is missing/actionable.
        show_omr_setup = bool(pdf_loaded and not omr_ready)
        show_omr_status = bool(show_omr_setup)
        audio_loaded = bool(getattr(self.project, "reference_audio_path", None))

        self.clear_score_source_btn.setVisible(score_loaded)
        self.analyze_score_btn.setVisible(score_loaded)
        self.open_score_pdf_btn.setVisible(pdf_loaded)
        for widget in (
            self.score_focus_label,
            self.score_focus_combo,
            self.score_focus_start_edit,
            self.score_focus_to_label,
            self.score_focus_end_edit,
        ):
            widget.setVisible(pdf_loaded)
        focus_mode = getattr(self.project, "score_focus_mode", "all") or "all"
        self.score_focus_start_edit.setVisible(pdf_loaded and focus_mode in {"single", "range"})
        self.score_focus_to_label.setVisible(pdf_loaded and focus_mode == "range")
        self.score_focus_end_edit.setVisible(pdf_loaded and focus_mode == "range")

        guide_checked = bool(getattr(self.project, "score_dynamics_enabled", False))
        show_guide_controls = bool(score_loaded)
        has_details = bool(getattr(self.project, "score_analysis_details_summary", None))
        repair_summary = getattr(self.project, "score_alignment_repair_summary", None)
        self.score_dynamics_check.setVisible(show_guide_controls)
        # The text is already in the checkbox. Never show the old orphan label.
        self.score_guidance_strength_label.setVisible(False)
        self.score_guidance_strength_combo.setVisible(show_guide_controls and guide_checked and guide_available)
        self.score_analysis_details_label.setVisible(has_details)
        # Alignment is not a one-time trim. Once analysis exists, keep the visual
        # repair prompt available so the user can revisit/refine it.
        audio_guide_available = self._reference_audio_guidance_available()
        score_analyzed = bool(
            getattr(self.project, "score_analysis_summary", None)
            or getattr(self.project, "score_omr_summary", None)
            or getattr(self.project, "score_alignment_summary", None)
            or getattr(self.project, "score_analysis_detail", None)
            or getattr(self.project, "score_guide_available", False)
            or has_details
        )
        self.fix_alignment_btn.setText("Align…")
        # Visual alignment needs only the playable MIDI plus a loaded reference.
        # PDF rendering/bar mapping is independent of OMR analysis; requiring an
        # analyzed score caused Align to disappear until audio was loaded.
        alignment_available = bool(midi_loaded and (score_loaded or audio_loaded))
        self.fix_alignment_btn.setVisible(alignment_available)
        self.fix_alignment_btn.setEnabled(alignment_available)
        if hasattr(self, "alignment_row_widget"):
            self.alignment_row_widget.setVisible(True)
        if hasattr(self, "alignment_source_summary_label"):
            source_names: list[str] = []
            if score_loaded:
                source_names.append("PDF")
            if audio_loaded:
                source_names.append("audio")
            focus_note = f" · focus: {self._score_focus_label()}" if score_loaded else ""
            self.alignment_source_summary_label.setText(
                ("MIDI + " + " + ".join(source_names) + focus_note) if source_names else "Load a PDF or audio reference."
            )
        # There is no separate score-take button. Enabling Use score creates
        # the normal pending numbered take below; user presses Play on that row.
        self.score_guided_take_btn.setVisible(False)

        show_analysis_notice = bool(score_loaded or getattr(self, "_score_analysis_busy", False) or getattr(self.project, "score_analysis_summary", None) or getattr(self.project, "score_omr_summary", None))
        self.score_analysis_inline_label.setVisible(show_analysis_notice)

        self.score_omr_engine_label.setVisible(show_omr_status)
        self.score_omr_setup_btn.setVisible(show_omr_setup)
        self.score_omr_help_btn.setVisible(show_omr_setup)

        # Reference performance is a primary source lane now. Keep the load action
        # and compact status visible so users can always find audio reference setup;
        # only hide the *guidance* controls until analysis produced usable contours.
        audio_guide_available = self._reference_audio_guidance_available()
        audio_guide_checked = bool(getattr(self.project, "reference_audio_guidance_enabled", False)) and audio_guide_available
        self.load_reference_audio_btn.setVisible(True)
        self.clear_reference_audio_btn.setVisible(audio_loaded)
        self.reference_audio_label.setVisible(True)
        if hasattr(self, "audio_analysis_progress"):
            self.audio_analysis_progress.setVisible(bool(getattr(self, "_audio_analysis_busy", False)))
        # Do not expose Use audio guide until it can actually work. MP3/FLAC/etc.
        # become guide-ready only when FFmpeg produced usable contours.
        self.audio_guidance_check.setVisible(bool(audio_loaded or audio_guide_available))
        self.audio_guidance_check.setEnabled(audio_guide_available)
        self.audio_guidance_strength_label.setVisible(False)
        self.audio_guidance_strength_combo.setVisible(audio_guide_available and audio_guide_checked)

        # Menu actions follow the same prerequisites where practical.
        for name, enabled in [
            ("clear_score_action", score_loaded),
            ("load_audio_action", True),
            ("clear_audio_action", audio_loaded),
        ]:
            action = getattr(self, name, None)
            if action is not None:
                action.setEnabled(bool(enabled))

    def _sync_score_guidance_widgets(self) -> None:
        if not hasattr(self, "score_dynamics_check"):
            return
        self.score_dynamics_check.blockSignals(True)
        self.score_dynamics_check.setChecked(bool(getattr(self.project, "score_dynamics_enabled", False)))
        self.score_dynamics_check.blockSignals(False)
        strength = str(getattr(self.project, "score_guidance_strength", "Light") or "Light")
        if strength not in {"Light", "Medium", "Strong"}:
            strength = "Light"
            self.project.score_guidance_strength = strength
        self.score_guidance_strength_combo.blockSignals(True)
        self.score_guidance_strength_combo.setCurrentText(strength)
        self.score_guidance_strength_combo.blockSignals(False)
        available = self._score_guidance_available()
        tooltip = "Use detected score markings to guide newly created takes." if available else "Attach a PDF score and click Analyze Score. Scanned PDFs need the Audiveris OMR bridge."
        self.score_dynamics_check.setToolTip(tooltip + " Original remains unchanged.")
        self.score_guidance_strength_combo.setToolTip("Score influence strength for new takes." if available else "Analyze a usable score source first.")
        guide_checked = bool(getattr(self.project, "score_dynamics_enabled", False))
        self.score_guided_take_btn.setEnabled(False)
        self.analyze_score_btn.setEnabled(bool(getattr(self.project, "score_source_path", None)) and not getattr(self, "_score_analysis_busy", False))
        self.open_score_pdf_btn.setEnabled(bool((getattr(self.project, "score_source_format", None) or "").lower() == "pdf" and getattr(self.project, "score_source_path", None)))
        self._apply_score_progressive_disclosure()

    def _variation_signature(self) -> str:
        payload = {
            "level": self.create_level_key,
            "expression": self.expression_label,
            "expressiveness": int(self.project.expressiveness if self.project.expressiveness is not None else 0),
            "timing": self.project.timing_feel,
            "mixer": {
                "programs": self.project.track_program_overrides,
                "volumes": self.project.track_volume_overrides,
                "pans": self.project.track_pan_overrides,
                "octaves": self.project.track_octave_overrides,
                "solos": self.project.track_solo_overrides,
                "mutes": self.project.track_mute_overrides,
            },
            "score_dynamics_enabled": bool(getattr(self.project, "score_dynamics_enabled", False)),
            "score_guidance_strength": getattr(self.project, "score_guidance_strength", "Light"),
            "score_source_path": getattr(self.project, "score_source_path", None),
            "score_alignment_summary": getattr(self.project, "score_alignment_summary", None),
            "score_omr_output_path": getattr(self.project, "score_omr_output_path", None),
            "score_omr_dynamic_sequence": getattr(self.project, "score_omr_dynamic_sequence", []),
            "score_omr_articulation_sequence": getattr(self.project, "score_omr_articulation_sequence", []),
            "score_omr_tempo_words": getattr(self.project, "score_omr_tempo_words", []),
            "score_alignment_shift_percent": getattr(self.project, "score_alignment_shift_percent", 0),
            "score_alignment_stretch_percent": getattr(self.project, "score_alignment_stretch_percent", 100),
            "reference_audio_guidance_enabled": bool(getattr(self.project, "reference_audio_guidance_enabled", False)),
            "reference_audio_guidance_strength": getattr(self.project, "reference_audio_guidance_strength", "Light"),
            "reference_audio_path": getattr(self.project, "reference_audio_path", None),
            "reference_audio_loudness_curve": getattr(self.project, "reference_audio_loudness_curve", []),
            "reference_audio_attack_curve": getattr(self.project, "reference_audio_attack_curve", []),
            "reference_audio_motion_curve": getattr(self.project, "reference_audio_motion_curve", []),
            "reference_audio_alignment_shift_percent": getattr(self.project, "reference_audio_alignment_shift_percent", 0.0),
            "reference_audio_alignment_stretch_percent": getattr(self.project, "reference_audio_alignment_stretch_percent", 100.0),
        }
        return json.dumps(payload, sort_keys=True)

    def _ensure_pending_variation_seed(self) -> int:
        if self._pending_variation_seed is None:
            self._pending_variation_seed = int(time.time() * 1000) % 2_147_483_647
        return int(self._pending_variation_seed)

    def _mark_variation_changed(self, message: str | None = None) -> None:
        self._variation_dirty = bool(self.project.source_midi_path) and self._variation_signature() != self._last_audition_signature
        if self._variation_dirty:
            self._ensure_pending_variation_seed()
        else:
            self._pending_variation_seed = None
        if message:
            self.status_label.setText(message)
        self._refresh_candidate_status()
        if hasattr(self, "takes_layout"):
            self._rebuild_takes_table()
        self._update_action_states()

    def _refresh_candidate_status(self) -> None:
        level = LEVEL_LABELS.get(self.create_level_key, "Light")
        settings = f"{level} · Expression {self.expression_label} · Timing {self.project.timing_feel}"
        if self._score_guidance_active():
            settings += f" · Score guide {getattr(self.project, 'score_guidance_strength', 'Light')}"
        if self._reference_audio_guidance_active():
            settings += f" · Audio guide {getattr(self.project, 'reference_audio_guidance_strength', 'Light')}"
        if self._variation_dirty:
            next_no = self._next_variation_number()
            self.candidate_status_label.setText(f"Changed: {settings} · play row {next_no}")
        else:
            self.candidate_status_label.setText(settings)

    def create_preview(self) -> None:
        """Create a numbered take immediately and play it.

        When a variation or mixer change is worth
        auditioning, pressing Play on the pending numbered row creates that numbered
        version, stores its exact settings, and plays it. Bad versions can be deleted.
        """
        if not self.project.source_midi_path:
            self.status_label.setText("Open a MIDI first")
            return
        if not self._playback_ready or self._playback_preparing:
            self.status_label.setText("Preparing playback… version Play will unlock when ready")
            return
        level = self.create_level_key
        try:
            self.status_label.setText(f"Creating {LEVEL_LABELS[level]} version…")
            QApplication.processEvents()
            self.project.audition_counter = int(getattr(self.project, "audition_counter", 0) or 0) + 1
            version_no = int(self.project.audition_counter)
            target = self._work_versions_dir() / f"take_{version_no:03d}.mid"
            seed = self._ensure_pending_variation_seed()
            score_guidance = self._score_guidance_plan()
            audio_guidance = self._reference_audio_guidance_plan()
            create_variation(
                self.project.source_midi_path,
                target,
                level,
                seed=seed,
                expressiveness=int(self.project.expressiveness if self.project.expressiveness is not None else 0),
                timing_feel=self.project.timing_feel,
                score_guidance=score_guidance,
                reference_audio_guidance=audio_guidance,
            )
            report = performance_parameter_report(level, int(self.project.expressiveness if self.project.expressiveness is not None else 0), self.project.timing_feel)
            signature = self._variation_signature()
            take_id = f"take_{version_no}"
            settings = self._current_take_settings_snapshot()
            score_detail = score_guidance.summary if score_guidance and score_guidance.is_active else ""
            audio_detail = audio_guidance.summary if audio_guidance and audio_guidance.is_active else ""
            combined_detail = " · ".join(bit for bit in [score_detail, audio_detail] if bit)
            take = {
                "id": take_id,
                "name": str(version_no),
                "version_no": version_no,
                "path": str(target),
                "level": level,
                "expression": self.expression_label,
                "timing": self.project.timing_feel,
                "signature": signature,
                "note": self._version_note_text(LEVEL_LABELS[level], self.expression_label, self.project.timing_feel, score_detail=combined_detail),
                "details": (report.compact_text() + ((" · " + combined_detail) if combined_detail else "")),
                "report": report.to_dict(),
                "created_at": time.strftime("%H:%M"),
                "favorite": False,
                "best": False,
                **settings,
            }
            self.project.takes.append(take)
            self.project.candidate_take = None
            self._last_audition_signature = signature
            self._variation_dirty = False
            self._pending_variation_seed = None
            self.selected_review_kind = "take"
            self.selected_take_id = take_id
            self._refresh_all()
            self.status_label.setText(f"Created {version_no}")
            self._play_item(take)
        except Exception as exc:
            self.status_label.setText("Create version failed")
            QMessageBox.warning(self, "Create version failed", str(exc))

    def _append_take_from_candidate(self, candidate: dict[str, Any]) -> str:
        # Compatibility helper for older project files. New versions are
        # saved directly by create_preview().
        version_no = int(candidate.get("version_no") or self._next_variation_number())
        take = dict(candidate)
        take["id"] = f"take_{version_no}"
        take["name"] = str(version_no)
        take.setdefault("favorite", False)
        take.setdefault("best", False)
        take.setdefault("volume_percent", self._master_volume_percent())
        take.setdefault("tempo_percent", int(self.project.playback_tempo_percent or 100))
        take.setdefault("mixer", self._current_take_settings_snapshot()["mixer"])
        self.project.takes.append(take)
        return str(take["id"])

    def save_candidate_to_takes(self) -> None:
        candidate = self.project.candidate_take
        if not candidate or not candidate.get("path"):
            self.status_label.setText("No preview to save; use the numbered row in Takes.")
            return
        take_id = self._append_take_from_candidate(candidate)
        self.project.candidate_take = None
        self.selected_review_kind = "take"
        self.selected_take_id = take_id
        self._refresh_all()
        self.status_label.setText(f"Saved {self._take_by_key(take_id).get('name') if self._take_by_key(take_id) else ''}")

    def play_candidate(self) -> None:
        self.status_label.setText("Use Play on a numbered row in Takes.")

    def delete_take_row(self, key: str) -> None:
        item = self._take_by_key(key)
        if not item:
            return
        if self.playback_engine.version_key == key:
            self.stop_playback(silent=True)
        self.project.takes = [take for take in (self.project.takes or []) if str(take.get("id")) != str(key)]
        self.export_checked_keys.discard(key)
        if self.selected_take_id == key:
            self.selected_review_kind = "original"
            self.selected_take_id = None
        self._refresh_all()
        self.status_label.setText(f"Deleted {item.get('name')}")

    def delete_all_takes(self) -> None:
        if not self.project.takes:
            self.status_label.setText("No numbered versions to delete")
            return
        if self.playback_engine.is_playing() or self.playback_engine.is_paused():
            self.stop_playback(silent=True)
        count = len(self.project.takes)
        self.project.takes = []
        self.project.candidate_take = None
        self.export_checked_keys.clear()
        self.selected_review_kind = "original"
        self.selected_take_id = None
        self._refresh_all()
        self.status_label.setText(f"Deleted {count} version(s)")

    def select_all_exports(self) -> None:
        for item in [self._original_take_row()] + list(self.project.takes or []):
            key = str(item.get("id") or "original")
            if item.get("path"):
                self.export_checked_keys.add(key)
        self._refresh_all()
        self.status_label.setText("Selected all playable rows for export")

    def select_none_exports(self) -> None:
        self.export_checked_keys.clear()
        self._refresh_all()
        self.status_label.setText("Cleared export selection")

    def _confirm_delete_unchecked_takes(self, remove_count: int, keep_count: int) -> bool:
        if remove_count <= 0:
            self.status_label.setText("No unchecked numbered versions to delete")
            return False
        if self.settings.get("skip_delete_unchecked_confirm", False):
            return True
        box = QMessageBox(self)
        box.setWindowTitle("Delete unchecked takes?")
        box.setIcon(QMessageBox.Warning)
        box.setText(f"Delete {remove_count} unchecked numbered take(s)?")
        box.setInformativeText(
            f"Checked takes stay. {keep_count} checked take(s) will remain. Original is never deleted."
        )
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.No)
        dont_ask = QCheckBox("Don’t ask again for Delete unchecked")
        box.setCheckBox(dont_ask)
        accepted = box.exec() == QMessageBox.Yes
        if accepted and dont_ask.isChecked():
            self.settings.set("skip_delete_unchecked_confirm", True)
        return accepted

    def delete_unchecked_takes(self) -> None:
        if not self.project.takes:
            self.status_label.setText("No numbered versions to delete")
            return
        before = len(self.project.takes)
        checked = [take for take in (self.project.takes or []) if str(take.get("id") or "") in self.export_checked_keys]
        remove_count = before - len(checked)
        if not self._confirm_delete_unchecked_takes(remove_count, len(checked)):
            return
        if self.playback_engine.is_playing() or self.playback_engine.is_paused():
            self.stop_playback(silent=True)
        keep = []
        for take in self.project.takes or []:
            key = str(take.get("id") or "")
            if key in self.export_checked_keys:
                keep.append(take)
            else:
                self.export_checked_keys.discard(key)
                if self.selected_take_id == key:
                    self.selected_review_kind = "original"
                    self.selected_take_id = None
        self.project.takes = keep
        removed = before - len(keep)
        self._refresh_all()
        self.status_label.setText(f"Deleted {removed} unchecked version(s); checked takes remain")

    def toggle_favorites_filter(self) -> None:
        self.show_favorites_only = bool(self.favorite_filter_btn.isChecked())
        self._rebuild_takes_table()
        self._update_action_states()
        count = sum(1 for take in (self.project.takes or []) if bool(take.get("favorite")))
        self.status_label.setText(f"Showing {count} favorite version(s)" if self.show_favorites_only else "Showing all versions")

    def use_take_settings(self, key: str) -> None:
        item = self._take_by_key(key)
        if not item or not key.startswith("take_"):
            return
        if self.playback_engine.is_playing() or self.playback_engine.is_paused():
            self.stop_playback(silent=True)
        self.create_level_key = str(item.get("level") or self.create_level_key or "minimal")
        if self.create_level_key not in self.level_buttons:
            self.create_level_key = "minimal"
        self.expression_label = str(item.get("expression") or self.expression_label or "Medium")
        if self.expression_label not in self.expression_buttons:
            self.expression_label = "None"
        self.project.timing_feel = str(item.get("timing") or self.project.timing_feel or "Natural")
        if self.project.timing_feel not in self.timing_buttons:
            self.project.timing_feel = "Natural"
        expression_values = dict(EXPRESSION_LEVELS)
        self.project.expressiveness = int(expression_values.get(self.expression_label, self.project.expressiveness or 0))

        mixer = self._mixer_from_take_item(item)
        self.project.track_program_overrides = dict(mixer.get("programs", {}) or {})
        self.project.track_volume_overrides = dict(mixer.get("volumes", {}) or {})
        self.project.track_pan_overrides = dict(mixer.get("pans", {}) or {})
        self.project.track_octave_overrides = dict(mixer.get("octaves", {}) or {})
        self.project.track_solo_overrides = dict(mixer.get("solos", {}) or {})
        self.project.track_mute_overrides = dict(mixer.get("mutes", {}) or {})
        self.project.master_volume = self._take_volume_percent(item)
        self.project.playback_tempo_percent = int(item.get("tempo_percent", self.project.playback_tempo_percent or 100) or 100)
        guidance = item.get("score_guidance") or {}
        if guidance:
            self.project.score_dynamics_enabled = bool(guidance.get("dynamics_enabled", False))
            strength = str(guidance.get("strength") or "Light")
            self.project.score_guidance_strength = strength if strength in {"Light", "Medium", "Strong"} else "Light"
        audio_guidance = item.get("audio_guidance") or {}
        if audio_guidance:
            self.project.reference_audio_guidance_enabled = bool(audio_guidance.get("enabled", False) and audio_guidance.get("active", False))
            strength = str(audio_guidance.get("strength") or "Light")
            self.project.reference_audio_guidance_strength = strength if strength in {"Light", "Medium", "Strong"} else "Light"

        self.volume_slider.blockSignals(True)
        self.tempo_slider.blockSignals(True)
        self.volume_slider.setValue(self._master_volume_percent())
        self.volume_label.setText(f"{self._master_volume_percent()}%")
        self.tempo_slider.setValue(int(self.project.playback_tempo_percent or 100))
        self.volume_slider.blockSignals(False)
        self.tempo_slider.blockSignals(False)
        for level_key, btn in self.level_buttons.items():
            btn.setChecked(level_key == self.create_level_key)
        for expr_key, btn in self.expression_buttons.items():
            btn.setChecked(expr_key == self.expression_label)
        for timing_key, btn in self.timing_buttons.items():
            btn.setChecked(timing_key == self.project.timing_feel)
        self._mixer_revision += 1
        self._last_audition_signature = self._variation_signature()
        self._variation_dirty = False
        self.selected_review_kind = "take"
        self.selected_take_id = key
        self._refresh_all()
        self.status_label.setText(f"Using {item.get('name')} settings as the next starting point")

    def toggle_take_favorite(self, key: str) -> None:
        item = self._take_by_key(key)
        if not item or not key.startswith("take_"):
            return
        item["favorite"] = not bool(item.get("favorite"))
        self._refresh_all()
        self.status_label.setText(f"{'Favorited' if item.get('favorite') else 'Unfavorited'} {item.get('name')}")

    def _best_take(self) -> dict[str, Any] | None:
        for take in self.project.takes or []:
            if bool(take.get("best")) and take.get("path"):
                return take
        return None

    def toggle_take_best(self, key: str) -> None:
        item = self._take_by_key(key)
        if not item or not key.startswith("take_"):
            return
        was_best = bool(item.get("best"))
        for take in self.project.takes or []:
            take["best"] = False
        item["best"] = not was_best
        self._refresh_all()
        self.status_label.setText(f"{'Best set to' if item.get('best') else 'Best cleared from'} {item.get('name')}")

    def edit_take_note(self, key: str) -> None:
        item = self._take_by_key(key)
        if not item or not key.startswith("take_"):
            return
        current = str(item.get("user_note") or "")
        text, ok = QInputDialog.getText(self, "Take note", "Short note for this version:", text=current)
        if not ok:
            return
        item["user_note"] = text.strip()[:80]
        self._refresh_all()
        self.status_label.setText(f"Updated note for {item.get('name')}")

    def duplicate_take(self, key: str) -> None:
        item = self._take_by_key(key)
        if not item or not key.startswith("take_") or not item.get("path"):
            return
        self.project.audition_counter = int(getattr(self.project, "audition_counter", 0) or 0) + 1
        version_no = int(self.project.audition_counter)
        new_id = f"take_{version_no}"
        new_take = dict(item)
        new_take["id"] = new_id
        new_take["name"] = str(version_no)
        new_take["version_no"] = version_no
        new_take["favorite"] = False
        new_take["best"] = False
        new_take["user_note"] = (str(item.get("user_note") or "") + " copy").strip()[:80]
        new_take["created_at"] = time.strftime("%H:%M")
        try:
            source_path = Path(str(item.get("path")))
            if source_path.exists():
                target = self._work_versions_dir() / f"take_{version_no:03d}_duplicate.mid"
                shutil.copy2(source_path, target)
                new_take["path"] = str(target)
        except Exception:
            pass
        self.project.takes.append(new_take)
        self.selected_review_kind = "take"
        self.selected_take_id = new_id
        self._refresh_all()
        self.status_label.setText(f"Duplicated {item.get('name')} as {version_no}")

    def move_take(self, key: str, direction: int) -> None:
        takes = list(self.project.takes or [])
        index = next((i for i, take in enumerate(takes) if str(take.get("id")) == str(key)), -1)
        if index < 0:
            return
        new_index = max(0, min(len(takes) - 1, index + int(direction)))
        if new_index == index:
            return
        takes[index], takes[new_index] = takes[new_index], takes[index]
        self.project.takes = takes
        self._refresh_all()
        self.status_label.setText(f"Moved {self._take_by_key(key).get('name') if self._take_by_key(key) else key}")

    def preview_selected(self) -> None:
        if self.playback_engine.is_playing():
            self.pause_playback()
            return
        item = self._selected_play_item()
        if item:
            self._play_item(item)

    def play_take_row(self, key: str) -> None:
        if key == "pending":
            self.create_preview()
            return
        item = self._take_by_key(key)
        if not item or not item.get("path"):
            self.status_label.setText("Nothing to play")
            return
        if self.playback_engine.is_playing() and self.playback_engine.label == item.get("name"):
            self.pause_playback()
            return
        self.select_take_row(key)
        self._play_item(item)

    def _play_item(self, item: dict[str, Any], *, start_tick_override: int | None = None) -> None:
        path = item.get("path")
        if not path:
            return
        if not self._playback_ready or self._playback_preparing:
            self.status_label.setText("Preparing playback… Play will unlock when ready")
            return
        if not self._ensure_soundfont():
            return
        version_key = str(item.get("id") or "take")
        mixer = self._mixer_from_take_item(item)
        volume_percent = self._take_volume_percent(item)
        tempo_percent = int(item.get("tempo_percent", self.project.playback_tempo_percent or 100) or 100)
        try:
            self._playback_mixer_contexts[version_key] = mixer
            self._active_playback_tempo_percent = tempo_percent
            self._apply_live_mixer_maps(
                dict(mixer.get("programs", {})),
                dict(mixer.get("volumes", {})),
                dict(mixer.get("pans", {})),
                dict(mixer.get("solos", {})),
                dict(mixer.get("mutes", {})),
            )
            snapshot = self.playback_engine.play(
                path,
                str(item.get("name") or "MIDI"),
                version_key,
                self.project.selected_soundfont_path,
                audio_gain_for_volume_percent(volume_percent),
                tempo_percent,
                start_percent=int(self.project.playback_start_percent or 0) if start_tick_override is None else None,
                start_tick=start_tick_override,
            )
            self._sync_playback_snapshot(snapshot)
            self.playback_timer.start()
            self.status_label.setText(f"Playing {item.get('name')} · volume {volume_percent}%")
        except FluidSynthError as exc:
            self.status_label.setText("Playback failed")
            QMessageBox.warning(self, "Playback failed", str(exc))
        self._update_action_states()

    def _restart_live_playback_with_project_mixer(self) -> bool:
        """Restart the active live preview so MIDI-transform mixer edits are audible immediately.

        Program, volume, pan, solo, and mute can be sent directly to FluidSynth.
        Octave shift changes note numbers, so it must rebuild the prepared MIDI.
        This restarts playback at the current transport position as a temporary
        mixer preview; Original and saved takes remain immutable until the user
        creates/plays the pending TAKE row.
        """
        if not self.playback_engine.is_playing():
            return False
        snapshot = self.playback_engine.snapshot()
        source = self.playback_engine.source_midi
        soundfont = self.playback_engine.soundfont_path or self.project.selected_soundfont_path
        if not source or not soundfont:
            return False
        mixer = self._mixer_from_take_item(None)
        version_key = "__mixer_preview__"
        try:
            self._playback_mixer_contexts[version_key] = mixer
            tempo = int(self.playback_engine.tempo_percent or self.project.playback_tempo_percent or 100)
            gain = float(self.playback_engine.volume_gain or self._volume_gain())
            restarted = self.playback_engine.play(
                source,
                "Mixer preview",
                version_key,
                soundfont,
                gain,
                tempo,
                start_percent=max(0, min(99, int(snapshot.percent or 0))),
            )
            self._sync_playback_snapshot(restarted)
            self.playback_timer.start()
            return True
        except Exception as exc:
            self.status_label.setText(f"Mixer preview restart failed: {exc}")
            return False

    def _ensure_soundfont(self) -> bool:
        if self.project.selected_soundfont_path and Path(self.project.selected_soundfont_path).exists():
            return True
        QMessageBox.information(self, "Instrument library needed", "Choose an instrument library (.sf2 or .sf3) so MIDI Performer can play audio.")
        self.choose_soundfont()
        return bool(self.project.selected_soundfont_path and Path(self.project.selected_soundfont_path).exists())

    def _volume_gain(self) -> float:
        return audio_gain_for_volume_percent(self._master_volume_percent())

    def _mixer_from_take_item(self, item: dict[str, Any] | None) -> dict[str, dict]:
        if item and isinstance(item.get("mixer"), dict):
            mixer = item.get("mixer") or {}
            return {
                "programs": dict(mixer.get("programs", {})),
                "volumes": dict(mixer.get("volumes", {})),
                "pans": dict(mixer.get("pans", {})),
                "octaves": dict(mixer.get("octaves", {})),
                "solos": dict(mixer.get("solos", {})),
                "mutes": dict(mixer.get("mutes", {})),
            }
        return {
            "programs": dict(self.project.track_program_overrides or {}),
            "volumes": dict(self.project.track_volume_overrides or {}),
            "pans": dict(self.project.track_pan_overrides or {}),
            "octaves": dict(self.project.track_octave_overrides or {}),
            "solos": dict(self.project.track_solo_overrides or {}),
            "mutes": dict(self.project.track_mute_overrides or {}),
        }

    def _prepared_midi_for_live_playback(self, source_path: str, label: str, version_key: str) -> str:
        mixer = self._playback_mixer_contexts.get(str(version_key))
        return self._prepared_midi_for_sound(source_path, label, mixer=mixer)

    def _prepared_midi_for_sound(self, source_path: str, label: str, mixer: dict[str, dict] | None = None) -> str:
        source = Path(source_path)
        current = source
        mixer = mixer if mixer is not None else self._mixer_from_take_item(None)
        programs = dict(mixer.get("programs", {}))
        volumes = dict(mixer.get("volumes", {}))
        pans = dict(mixer.get("pans", {}))
        solos = dict(mixer.get("solos", {}))
        mutes = dict(mixer.get("mutes", {}))
        octaves = dict(mixer.get("octaves", {}))
        has_track_mix = bool(programs or volumes or pans or solos or mutes or octaves)
        if has_track_mix:
            digest = hashlib.sha1(self._mixer_signature_text(mixer).encode("utf-8")).hexdigest()[:8]
            target = self._work_versions_dir() / f"{source.stem}_{self._safe_label(label)}_mixer_{digest}.mid"
            current = apply_track_mixer_overrides(current, target, programs, volumes, pans, solos, mutes, octaves)
        return str(current)

    def _prepared_export_midi_for_item(self, item: dict[str, Any], label: str, bake_master_volume: bool = True) -> str:
        mixer = self._mixer_from_take_item(item)
        current = Path(self._prepared_midi_for_sound(str(item.get("path")), label, mixer=mixer))
        volume_percent = self._take_volume_percent(item, default=100)
        if bake_master_volume and volume_percent != 100:
            digest = hashlib.sha1(f"volume:{volume_percent}:{current}".encode("utf-8")).hexdigest()[:8]
            target = self._work_versions_dir() / f"{current.stem}_vol_{volume_percent}_{digest}.mid"
            current = apply_master_volume_percent(current, target, volume_percent)
        tempo_percent = int(item.get("tempo_percent", 100) or 100)
        if tempo_percent != 100:
            digest = hashlib.sha1(f"tempo:{tempo_percent}:{current}".encode("utf-8")).hexdigest()[:8]
            target = self._work_versions_dir() / f"{current.stem}_tempo_{tempo_percent}_{digest}.mid"
            current = apply_tempo_percent(current, target, tempo_percent)
        return str(current)

    def _mixer_signature_text(self, mixer: dict[str, dict] | None = None) -> str:
        mixer = mixer if mixer is not None else self._mixer_from_take_item(None)
        return json.dumps({
            "revision": self._mixer_revision,
            "programs": mixer.get("programs", {}),
            "volumes": mixer.get("volumes", {}),
            "pans": mixer.get("pans", {}),
            "octaves": mixer.get("octaves", {}),
            "solos": mixer.get("solos", {}),
            "mutes": mixer.get("mutes", {}),
        }, sort_keys=True)

    def _safe_label(self, label: str) -> str:
        return "".join(ch if ch.isalnum() else "_" for ch in label.lower()).strip("_") or "play"

    def _sync_playback_snapshot(self, snapshot: Any) -> None:
        pct = int(snapshot.percent or 0)
        self.project.playback_start_percent = pct
        self.position_slider.blockSignals(True)
        self.position_slider.setValue(pct)
        self.position_slider.blockSignals(False)
        self.position_label.setText(f"{pct}%")
        label = snapshot.label or self._current_target_label()
        duration = snapshot.duration_seconds or self._selected_duration_seconds()
        if snapshot.state == PlaybackState.PLAYING:
            self.playback_status_label.setText(
                f"{label} · playing · {self._format_seconds(snapshot.seconds)} / {self._format_seconds(duration)} · tempo {int(self._active_playback_tempo_percent or self.project.playback_tempo_percent or 100)}%"
            )
        elif snapshot.state == PlaybackState.PAUSED:
            self.playback_status_label.setText(f"{label} · paused · {pct}%")
        else:
            self.playback_status_label.setText(self._compact_stopped_status(label, pct, duration))
        self._update_action_states()

    def _poll_playback_state(self) -> None:
        snapshot = self.playback_engine.poll()
        if snapshot.state == PlaybackState.PLAYING:
            self._sync_playback_snapshot(snapshot)
            self._update_take_play_buttons()
            return
        self.playback_timer.stop()
        if snapshot.finished:
            self.project.playback_start_percent = 0
            self.position_slider.blockSignals(True)
            self.position_slider.setValue(0)
            self.position_slider.blockSignals(False)
            self.position_label.setText("0%")
            if getattr(snapshot, "error", None):
                error_text = str(snapshot.error)
                self.playback_status_label.setText("Playback stopped with an audio-engine error")
                self.status_label.setText(f"Playback error: {error_text}")
            else:
                self.playback_status_label.setText(self._compact_stopped_status(self._current_target_label(), 0, self._selected_duration_seconds()))
                self.status_label.setText("Stopped")
        else:
            self._sync_playback_snapshot(snapshot)
        self._update_action_states()

    def _current_target_label(self) -> str:
        item = self._selected_play_item()
        if not item:
            return "Original"
        return str(item.get("name") or "Original")

    def _selected_duration_seconds(self) -> float:
        item = self._selected_play_item()
        path = item.get("path") if item else self.project.source_midi_path
        if not path:
            return 0.0
        try:
            if path == self.project.source_midi_path:
                summary = self._midi_summary()
                return float(summary.length_seconds) if summary else 0.0
            return float(summarize_midi(path).length_seconds)
        except Exception:
            return 0.0

    def _compact_stopped_status(self, label: str, pct: int = 0, duration_seconds: float = 0.0) -> str:
        if not self.project.source_midi_path:
            return "Load MIDI to play"
        if pct <= 0:
            return f"{label} · stopped · 0:00 / {self._format_seconds(duration_seconds)}"
        return f"{label} · stopped · start {pct}%"

    def _format_seconds(self, seconds: float) -> str:
        seconds = max(0, int(round(seconds or 0)))
        return f"{seconds // 60}:{seconds % 60:02d}"

    def set_master_volume(self, value: int) -> None:
        value = normalize_volume_percent(value)
        self.project.master_volume = value
        self.volume_label.setText(f"{value}%")
        self.settings.set("master_volume", value)
        if self.playback_engine.is_playing() or self.playback_engine.is_paused():
            self.playback_engine.change_gain(self._volume_gain())
        self.status_label.setText(f"Volume {value}% · 100% is neutral; lower reduces output")

    def set_tempo_percent(self, value: int) -> None:
        self.project.playback_tempo_percent = int(value)
        self.settings.set("playback_tempo_percent", int(value))
        self.tempo_label.setText(f"{int(value)}%")
        self._update_midi_summary()
        if self.playback_engine.is_playing() or self.playback_engine.is_paused():
            self._active_playback_tempo_percent = int(value)
            snapshot = self.playback_engine.change_tempo(int(value))
            self._sync_playback_snapshot(snapshot)
        self.status_label.setText(f"Tempo {int(value)}%")

    def set_playback_position(self, value: int) -> None:
        self.project.playback_start_percent = max(0, min(99, int(value)))
        self.position_label.setText(f"{self.project.playback_start_percent}%")
        if not self.playback_engine.is_playing():
            self.playback_status_label.setText(self._compact_stopped_status(self._current_target_label(), self.project.playback_start_percent, self._selected_duration_seconds()))

    def restart_playback_from_shuttle(self) -> None:
        if self.playback_engine.is_playing() or self.playback_engine.is_paused():
            snapshot = self.playback_engine.seek(int(self.project.playback_start_percent or 0))
            self._sync_playback_snapshot(snapshot)

    def rewind_playback(self) -> None:
        self.position_slider.setValue(0)
        self.restart_playback_from_shuttle()

    def fast_forward_playback(self) -> None:
        self.position_slider.setValue(min(99, int(self.position_slider.value()) + 10))
        self.restart_playback_from_shuttle()

    def pause_playback(self) -> None:
        if self.playback_engine.is_playing():
            snapshot = self.playback_engine.pause()
            self._sync_playback_snapshot(snapshot)

    def stop_playback(self, silent: bool = False, reset_to_start: bool = True) -> None:
        snapshot = self.playback_engine.stop(reset_to_start=reset_to_start)
        self.playback_timer.stop()
        pct = 0 if reset_to_start else int(snapshot.percent or 0)
        self.project.playback_start_percent = pct
        self.position_slider.blockSignals(True)
        self.position_slider.setValue(pct)
        self.position_slider.blockSignals(False)
        self.position_label.setText(f"{pct}%")
        if not silent:
            self.status_label.setText("Stopped")
            self.playback_status_label.setText(self._compact_stopped_status(self._current_target_label(), pct, self._selected_duration_seconds()))
        self._update_action_states()


    def _update_prepare_widgets(self) -> None:
        show = bool(self._playback_preparing or self._playback_prepare_error)
        self.prepare_status_label.setVisible(show)
        self.prepare_progress.setVisible(bool(self._playback_preparing))
        if self._playback_preparing:
            frame = self._spinner_frames[self._spinner_index % len(self._spinner_frames)]
            self.prepare_status_label.setText(f"{frame} Preparing playback…")
            self.prepare_status_label.setToolTip("Playback engine, instrument library, and MIDI timeline are being prepared before Play is enabled.")
        elif self._playback_prepare_error:
            self.prepare_status_label.setText("Playback not ready")
            self.prepare_status_label.setToolTip(self._playback_prepare_error)
        else:
            self.prepare_status_label.setText("")
            self.prepare_status_label.setToolTip("")

    def _animate_preparing_playback(self) -> None:
        if not self._playback_preparing:
            self.spinner_timer.stop()
            self._update_prepare_widgets()
            return
        self._spinner_index = (self._spinner_index + 1) % len(self._spinner_frames)
        self._update_prepare_widgets()

    def _invalidate_playback_ready(self, error: str | None = None) -> None:
        self._prepare_generation += 1
        self._playback_ready = False
        self.show_favorites_only = False
        self._playback_preparing = False
        self._playback_prepare_error = error
        self._prepare_result = None
        self.prepare_poll_timer.stop()
        self.spinner_timer.stop()
        self._update_prepare_widgets()

    def _begin_playback_prepare(self, status_prefix: str = "") -> bool:
        if not self.project.source_midi_path:
            self._invalidate_playback_ready()
            return False
        if not self.project.selected_soundfont_path or not Path(self.project.selected_soundfont_path).exists():
            self._invalidate_playback_ready("Select an instrument library to prepare playback.")
            self.status_label.setText("Select an instrument library to prepare playback")
            self._refresh_all()
            return False
        self.stop_playback(silent=True)
        self._prepare_generation += 1
        generation = self._prepare_generation
        source = str(self.project.source_midi_path)
        soundfont = str(self.project.selected_soundfont_path)
        gain = self._volume_gain()
        tempo = int(self.project.playback_tempo_percent or 100)
        self._playback_ready = False
        self._playback_preparing = True
        self._playback_prepare_error = None
        self._prepare_result = None
        self._spinner_index = 0
        self._playback_mixer_contexts["original"] = {"programs": {}, "volumes": {}, "pans": {}, "solos": {}, "mutes": {}}
        self.status_label.setText(f"{status_prefix}Preparing playback…")
        self._refresh_all()
        self.spinner_timer.start()
        self.prepare_poll_timer.start()

        def worker() -> None:
            started = time.monotonic()
            try:
                self.playback_engine.preload_midi(
                    source,
                    "Original",
                    "original",
                    Path(soundfont),
                    gain,
                    tempo,
                )
                backend_name = f"audio driver {self.settings.get('audio_driver', 'default')}"
                self._prepare_result = {
                    "generation": generation,
                    "ok": True,
                    "backend": backend_name,
                    "elapsed": time.monotonic() - started,
                    "prefix": status_prefix,
                }
            except Exception as exc:
                self._prepare_result = {
                    "generation": generation,
                    "ok": False,
                    "error": str(exc),
                    "elapsed": time.monotonic() - started,
                    "prefix": status_prefix,
                }

        self._prepare_thread = threading.Thread(target=worker, name="midi-performer-prepare", daemon=True)
        self._prepare_thread.start()
        return True

    def _poll_playback_prepare(self) -> None:
        result = self._prepare_result
        if not result:
            return
        self._prepare_result = None
        if int(result.get("generation", -1)) != int(self._prepare_generation):
            return
        self.prepare_poll_timer.stop()
        self.spinner_timer.stop()
        self._playback_preparing = False
        if result.get("ok"):
            self._playback_ready = True
            self._playback_prepare_error = None
            backend_name = str(result.get("backend") or "audio backend")
            elapsed = float(result.get("elapsed") or 0.0)
            prefix = str(result.get("prefix") or "")
            self.status_label.setText(f"{prefix}MIDI prepared · {backend_name} · audio opens on Play · {elapsed:.1f}s")
            self.playback_status_label.setText(self._compact_stopped_status("Original", 0, self._selected_duration_seconds()))
        else:
            self._playback_ready = False
            self._playback_prepare_error = str(result.get("error") or "Unknown preparation error")
            self.status_label.setText(f"Playback not ready: {self._playback_prepare_error}")
        self._refresh_all()

    def _warm_playback_backend(self, status_prefix: str = "") -> bool:
        # Compatibility wrapper used by older call sites and tests. This is
        # an async playback-ready gate instead of showing a loaded/playable MIDI too soon.
        return self._begin_playback_prepare(status_prefix=status_prefix)

    def load_midi(self) -> None:
        start = self.settings.get("last_midi_directory") or str(Path.home())
        path, _ = QFileDialog.getOpenFileName(self, "Open MIDI", start, "MIDI Files (*.mid *.midi);;All Files (*.*)")
        if path:
            self.settings.set("last_midi_directory", str(Path(path).parent))
            self._remember_recent_path("recent_midi_paths", path)
            self._set_midi_path(path, "MIDI loaded")

    def _set_midi_path(self, path: str, status: str) -> None:
        self.stop_playback(silent=True)
        self.status_label.setText(f"Loading MIDI: {Path(path).name}")
        QApplication.processEvents()
        self._invalidate_midi_cache()
        self.project.source_midi_path = path
        self._update_score_alignment_if_possible()
        self.settings.set("last_midi_path", path)
        self._remember_recent_path("recent_midi_paths", path)
        self.project.track_program_overrides = {}
        self.project.track_volume_overrides = {}
        self.project.track_pan_overrides = {}
        self.project.track_solo_overrides = {}
        self.project.track_mute_overrides = {}
        self.export_checked_keys.clear()
        self._playback_mixer_contexts.clear()
        self.project.playback_start_percent = 0
        self.project.reset_versions()
        self.selected_review_kind = "original"
        self.selected_take_id = None
        self.position_slider.setValue(0)
        self._variation_dirty = False
        self._last_audition_signature = None
        self._playback_ready = False
        self.show_favorites_only = False
        self._playback_preparing = False
        self._playback_prepare_error = None
        self._refresh_all()
        if not self._begin_playback_prepare(status_prefix=f"{status} · "):
            self.status_label.setText(status)

    def choose_soundfont(self) -> None:
        start = self.settings.get("last_soundfont_directory") or str(self._repo_root() / "assets" / "soundfonts")
        path, _ = QFileDialog.getOpenFileName(self, "Choose Instrument Library", start, "SoundFont Files (*.sf2 *.sf3);;All Files (*.*)")
        if path:
            self.project.selected_soundfont_path = path
            self.settings.set("last_soundfont_path", path)
            self.settings.set("last_soundfont_directory", str(Path(path).parent))
            self._remember_recent_path("recent_soundfont_paths", path)
            if self.project.source_midi_path:
                self._invalidate_playback_ready()
            self._refresh_all()
            if self.project.source_midi_path:
                if not self._begin_playback_prepare(status_prefix=f"Instrument library selected: {Path(path).name} · "):
                    self.status_label.setText(f"Instrument library selected: {Path(path).name}")
            else:
                self.status_label.setText(f"Instrument library selected: {Path(path).name}")

    def load_score_source(self) -> None:
        start = self.settings.get("last_score_source_directory") or self.settings.get("last_midi_directory") or str(Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Load Score Source",
            start,
            "PDF Scores (*.pdf);;Optional technical score sources (*.ly *.ily *.musicxml *.xml *.mxl);;All Files (*.*)",
        )
        if path:
            self._set_score_source(path)

    def _set_score_source(self, path: str) -> None:
        old_score_path = getattr(self.project, "score_source_path", None)
        try:
            info = read_score_source(path)
        except Exception as exc:
            QMessageBox.warning(self, "Score source", f"Could not load score source.\n{exc}")
            return
        self.project.score_source_path = info.path
        self.project.score_source_format = info.format
        self.project.score_source_status = info.status
        self.project.score_source_summary = info.summary
        self.project.score_source_detail = info.detail
        self.project.score_intent_summary = info.intent_summary
        self.project.score_intent_detail = info.intent_detail
        self.project.score_analysis_status = info.status
        self.project.score_analysis_summary = info.intent_summary or info.summary
        self.project.score_analysis_detail = info.intent_detail or info.detail
        self.project.score_alignment_summary = None
        self.project.score_alignment_detail = None
        self.project.score_analysis_details_summary = None
        self.project.score_analysis_details_detail = None
        self.project.score_alignment_confidence = None
        self.project.score_alignment_repair_needed = False
        self.project.score_alignment_repair_summary = None
        if old_score_path != info.path:
            self.project.score_omr_engine_status = None
            self.project.score_omr_engine_detail = None
            self.project.score_omr_output_path = None
            self.project.score_omr_summary = None
            self.project.score_omr_detail = None
            self.project.score_alignment_shift_percent = 0
            self.project.score_alignment_stretch_percent = 100
            self.project.score_alignment_shift_beats = 0.0
            self.project.score_alignment_length_beats = 0.0
            self.project.score_omr_dynamic_sequence = []
            self.project.score_omr_articulation_sequence = []
            self.project.score_omr_tempo_words = []
            self.project.score_omr_fermata_words = []
        if (info.format or "").lower() == "pdf":
            self.project.score_focus_page_count = info.page_count
            if old_score_path != info.path:
                self.project.score_focus_mode = "all"
                self.project.score_focus_start_page = None
                self.project.score_focus_end_page = None
            self.project.score_focus_summary = self._score_focus_label()
        else:
            self.project.score_focus_mode = "all"
            self.project.score_focus_start_page = None
            self.project.score_focus_end_page = None
            self.project.score_focus_page_count = None
            self.project.score_focus_summary = None
        self.project.score_guide_available = bool((info.format or "").lower() in {"lilypond", "pdf"} and (info.dynamics or info.articulations or info.tempo_words))
        if not self.project.score_guide_available:
            self.project.score_dynamics_enabled = False
            self.project.score_guidance_enabled = False
        self._update_score_alignment_if_possible()
        self.settings.set("last_score_source_directory", str(Path(path).parent))
        self._remember_recent_path("recent_score_source_paths", path)
        self._refresh_all()
        if self.project.score_alignment_summary:
            self.status_label.setText(f"{info.status} · {self.project.score_alignment_summary}")
        else:
            self.status_label.setText(info.status)

    def clear_score_source(self) -> None:
        self.project.score_source_path = None
        self.project.score_source_format = None
        self.project.score_source_status = None
        self.project.score_source_summary = None
        self.project.score_source_detail = None
        self.project.score_intent_summary = None
        self.project.score_intent_detail = None
        self.project.score_alignment_summary = None
        self.project.score_alignment_detail = None
        self.project.score_analysis_details_summary = None
        self.project.score_analysis_details_detail = None
        self.project.score_alignment_confidence = None
        self.project.score_alignment_repair_needed = False
        self.project.score_alignment_repair_summary = None
        self.project.score_alignment_shift_percent = 0
        self.project.score_alignment_stretch_percent = 100
        self.project.score_alignment_shift_beats = 0.0
        self.project.score_alignment_length_beats = 0.0
        self.project.score_analysis_status = None
        self.project.score_analysis_summary = None
        self.project.score_analysis_detail = None
        self.project.score_guide_available = False
        self.project.score_guidance_enabled = False
        self.project.score_dynamics_enabled = False
        self.project.score_omr_engine_status = None
        self.project.score_omr_engine_detail = None
        self.project.score_omr_output_path = None
        self.project.score_omr_summary = None
        self.project.score_omr_detail = None
        self.project.score_omr_dynamic_sequence = []
        self.project.score_omr_articulation_sequence = []
        self.project.score_omr_tempo_words = []
        self.project.score_omr_fermata_words = []
        self.project.score_focus_mode = "all"
        self.project.score_focus_start_page = None
        self.project.score_focus_end_page = None
        self.project.score_focus_page_count = None
        self.project.score_focus_summary = None
        self._refresh_all()
        self.status_label.setText("Score source cleared")

    def _update_score_alignment_if_possible(self) -> None:
        self.project.score_alignment_summary = None
        self.project.score_alignment_detail = None
        self.project.score_analysis_details_summary = None
        self.project.score_analysis_details_detail = None
        self.project.score_alignment_confidence = None
        self.project.score_alignment_repair_needed = False
        self.project.score_alignment_repair_summary = None
        score_path = getattr(self.project, "score_source_path", None)
        midi_path = getattr(self.project, "source_midi_path", None)
        score_format = (getattr(self.project, "score_source_format", None) or "").lower()
        if not score_path or not midi_path:
            return
        try:
            if score_format == "lilypond":
                intent = parse_lilypond_file(score_path)
                report = compare_lilypond_to_midi(intent, midi_path)
            elif score_format == "pdf":
                report = compare_pdf_to_midi(score_path, midi_path, page_range=self._score_focus_page_range())
            else:
                return
        except Exception as exc:
            self.project.score_alignment_summary = "Score/MIDI fit check unavailable"
            self.project.score_alignment_detail = str(exc)
            return
        self.project.score_alignment_summary = report.summary
        self.project.score_alignment_detail = report.detail
        self._update_score_analysis_details_from_current_state()

    def _current_detected_score_sequences(self) -> tuple[list[str], list[str], list[str], list[str]]:
        dynamics = list(getattr(self.project, "score_omr_dynamic_sequence", []) or [])
        articulations = list(getattr(self.project, "score_omr_articulation_sequence", []) or [])
        tempos = list(getattr(self.project, "score_omr_tempo_words", []) or [])
        fermatas = list(getattr(self.project, "score_omr_fermata_words", []) or [])
        score_path = getattr(self.project, "score_source_path", None)
        score_format = (getattr(self.project, "score_source_format", None) or "").lower()
        if score_path and score_format == "pdf" and not (dynamics or articulations or tempos or fermatas):
            try:
                analysis = analyze_pdf_score(score_path, page_range=self._score_focus_page_range())
                dynamics = list(analysis.dynamic_sequence or [])
                articulations = list(analysis.articulation_words or [])
                tempos = list(analysis.tempo_words or [])
                fermatas = list(getattr(analysis, "fermata_words", []) or [])
            except Exception:
                pass
        return dynamics, articulations, tempos, fermatas

    def _update_score_analysis_details_from_current_state(self) -> None:
        dynamics, articulations, tempos, fermatas = self._current_detected_score_sequences()
        details = build_score_analysis_details(
            dynamics=dynamics,
            articulations=articulations,
            tempo_words=tempos,
            fermatas=fermatas,
            alignment_summary=getattr(self.project, "score_alignment_summary", None),
            alignment_detail=getattr(self.project, "score_alignment_detail", None),
            guidance_enabled=bool(getattr(self.project, "score_guide_available", False)),
        )
        self.project.score_analysis_details_summary = details.summary
        self.project.score_analysis_details_detail = details.detail
        self.project.score_alignment_confidence = details.alignment_confidence
        self.project.score_alignment_repair_needed = bool(details.repair_needed)

    def _refresh_score_analysis_details_widget(self) -> None:
        if not hasattr(self, "score_analysis_details_label"):
            return
        summary = getattr(self.project, "score_analysis_details_summary", None)
        detail = getattr(self.project, "score_analysis_details_detail", None) or ""
        repair = getattr(self.project, "score_alignment_repair_summary", None) or ""
        if summary:
            text = self._compact_score_summary(str(summary))
            if repair:
                text += " · repaired"
            self.score_analysis_details_label.setText(text)
            self.score_analysis_details_label.setToolTip("\n".join(part for part in [detail, repair] if part))
        else:
            self.score_analysis_details_label.setText("Details appear after analysis")
            self.score_analysis_details_label.setToolTip("After Analyze Score, this shows detected vs used markings and alignment confidence.")


    def _refresh_omr_setup_widgets(self) -> None:
        if not hasattr(self, "score_omr_engine_label"):
            return
        engine = locate_omr_engine(self.settings.get("audiveris_path"))
        score_format = (getattr(self.project, "score_source_format", None) or "").lower()
        project_omr_status = str(getattr(self.project, "score_omr_engine_status", "") or "").lower()
        project_omr_ready = any(word in project_omr_status for word in ("ready", "installed", "confirmed", "complete", "success")) or bool(getattr(self.project, "score_omr_summary", None))
        omr_ready = bool(engine.available or project_omr_ready)
        if omr_ready:
            self.score_omr_engine_label.setObjectName("ScoreAnalysisReady")
            detail = engine.detail if engine.available else str(getattr(self.project, "score_omr_engine_detail", "") or "OMR was verified during score analysis.")
            self.score_omr_engine_label.setText("OMR ready")
            self.score_omr_engine_label.setToolTip(detail)
            self.score_omr_setup_btn.setText("OMR…")
            self.score_omr_help_btn.setText("OMR ready")
            self.score_omr_help_btn.setEnabled(False)
            self.score_omr_help_btn.setToolTip("Audiveris is already installed/found. Use Change OMR if you need a different executable.")
        else:
            if score_format == "pdf":
                self.score_omr_engine_label.setObjectName("ScoreAnalysisWarn")
                self.score_omr_engine_label.setText("OMR missing")
            else:
                self.score_omr_engine_label.setObjectName("ScoreAnalysisInfo")
                self.score_omr_engine_label.setText("OMR optional")
            self.score_omr_engine_label.setToolTip(engine.detail)
            self.score_omr_setup_btn.setText("OMR…")
            self.score_omr_help_btn.setText("Install OMR…")
            self.score_omr_help_btn.setEnabled(True)
            self.score_omr_help_btn.setToolTip("Install Audiveris with Windows winget, then verify the OMR engine. If winget is unavailable, MIDI Performer opens the official install page.")
        self.score_omr_engine_label.style().unpolish(self.score_omr_engine_label)
        self.score_omr_engine_label.style().polish(self.score_omr_engine_label)
        self.score_omr_engine_label.update()

    def open_audiveris_site(self) -> None:
        QDesktopServices.openUrl(QUrl("https://audiveris.github.io/audiveris/"))
        self._set_score_analysis_notice("Opened official Audiveris docs. Install Audiveris, then use Choose OMR here if auto-detect does not find it.", "info")
        self.status_label.setText("Opened official Audiveris site")

    def install_audiveris_engine(self) -> None:
        if locate_omr_engine(self.settings.get("audiveris_path")).available:
            self._refresh_omr_setup_widgets()
            self.status_label.setText("Audiveris is already installed")
            self._set_score_analysis_notice("Audiveris is already installed/found. Analyze Score can use OMR.", "ready")
            return
        if self._dependency_install_busy:
            self.status_label.setText("Audiveris install/verify is already running")
            return
        reply = QMessageBox.question(
            self,
            "Install Audiveris",
            "MIDI Performer will try to install Audiveris using Windows Package Manager (winget), then verify Audiveris, FFmpeg, and FluidSynth.\n\nContinue?",
        )
        if reply != QMessageBox.Yes:
            return
        self._dependency_install_busy = True
        self._dependency_install_result = None
        self._dependency_spinner_index = 0
        self._dependency_install_dialog = ExportProgressDialog(self, "Installing Audiveris, then verifying dependencies…")
        self._dependency_install_dialog.setWindowTitle("Dependency Setup")
        self._dependency_install_dialog.setStyleSheet(self._stylesheet())
        self._dependency_install_dialog.show()
        self.status_label.setText("Installing Audiveris…")

        def worker() -> None:
            install = install_audiveris_with_winget()
            report = check_dependencies(self._repo_root(), self.settings.get("audiveris_path"), self.settings.get("fluidsynth_path"))
            self._dependency_install_result = {"install": install, "report": report}

        self._dependency_install_thread = threading.Thread(target=worker, name="midi-performer-install-audiveris", daemon=True)
        self._dependency_install_thread.start()
        self.dependency_poll_timer.start()

    def _poll_dependency_install_job(self) -> None:
        if not self._dependency_install_busy:
            self.dependency_poll_timer.stop()
            return
        self._dependency_spinner_index = (self._dependency_spinner_index + 1) % len(self._spinner_frames)
        frame = self._spinner_frames[self._dependency_spinner_index]
        message = f"{frame} Installing/verifying Audiveris, FFmpeg, and FluidSynth…"
        self.status_label.setText(message)
        if self._dependency_install_dialog:
            self._dependency_install_dialog.set_message(message)
        done = self._dependency_install_thread is not None and not self._dependency_install_thread.is_alive()
        if done and self._dependency_install_result is not None:
            self.dependency_poll_timer.stop()
            result = self._dependency_install_result
            self._dependency_install_result = None
            self._dependency_install_thread = None
            self._finish_dependency_install_job(result)

    def _finish_dependency_install_job(self, result: dict[str, Any]) -> None:
        dialog = self._dependency_install_dialog
        self._dependency_install_dialog = None
        if dialog is not None:
            dialog.close()
        self._dependency_install_busy = False
        install = result.get("install")
        report = result.get("report") or {}
        audiveris = report.get("audiveris") if isinstance(report, dict) else None
        ffmpeg = report.get("ffmpeg") if isinstance(report, dict) else None
        fluidsynth = report.get("fluidsynth") if isinstance(report, dict) else None
        omr_ok = bool(getattr(audiveris, "found", False))
        ffmpeg_ok = bool(getattr(ffmpeg, "found", False) and getattr(ffmpeg, "confirmed", False))
        fluidsynth_ok = bool(getattr(fluidsynth, "found", False) and getattr(fluidsynth, "confirmed", False))
        if omr_ok:
            self._set_score_analysis_notice("Audiveris is installed/found. Analyze Score can now run true OMR on scanned PDFs.", "ready")
            self.status_label.setText("Audiveris found; dependencies verified")
        else:
            self._set_score_analysis_notice("Audiveris was not found after install attempt. Use Choose OMR or the official install page.", "warn")
            self.status_label.setText("Audiveris setup still needs attention")
            self.open_audiveris_site()
        summary = [
            f"Audiveris: {'found' if omr_ok else 'not found'}",
            f"FluidSynth: {'confirmed' if fluidsynth_ok else 'not confirmed'}",
            f"FFmpeg: {'confirmed' if ffmpeg_ok else 'not confirmed'}",
        ]
        install_detail = getattr(install, "status", "Install result") + (f"\n\n{getattr(install, 'detail', '')}" if getattr(install, "detail", "") else "")
        QMessageBox.information(self, "Dependency setup", "\n".join(summary) + "\n\n" + install_detail[:1800])
        self._refresh_all()
        self.show_setup_status()

    def choose_score_omr_engine(self) -> None:
        current = str(self.settings.get("audiveris_path") or self._repo_root())
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose Audiveris executable or JAR",
            current,
            "Audiveris executable/JAR (*.exe *.bat *.cmd *.jar);;All Files (*.*)",
        )
        if not path:
            return
        self.settings.set("audiveris_path", path)
        status = locate_omr_engine(path)
        if status.available:
            self._set_score_analysis_notice("OMR engine ready. Click Analyze Score to run true score recognition on the attached PDF.", "ready")
            self.status_label.setText("OMR engine ready")
        else:
            self._set_score_analysis_notice(f"OMR path is not usable yet: {status.detail}", "warn")
            self.status_label.setText("OMR setup needs attention")
        self._refresh_all()

    def _set_score_analysis_notice(self, message: str, state: str = "info") -> None:
        if not hasattr(self, "score_analysis_inline_label"):
            self.status_label.setText(message)
            return
        object_name = {
            "ready": "ScoreAnalysisReady",
            "warn": "ScoreAnalysisWarn",
            "busy": "ScoreAnalysisBusy",
        }.get(state, "ScoreAnalysisInfo")
        self.score_analysis_inline_label.setObjectName(object_name)
        self.score_analysis_inline_label.setText(message)
        self.score_analysis_inline_label.setToolTip(message)
        if hasattr(self, "score_analysis_progress"):
            self.score_analysis_progress.setVisible(state == "busy")
        self.score_analysis_inline_label.style().unpolish(self.score_analysis_inline_label)
        self.score_analysis_inline_label.style().polish(self.score_analysis_inline_label)
        self.score_analysis_inline_label.update()

    def _refresh_score_analysis_notice_from_project(self) -> None:
        if not hasattr(self, "score_analysis_inline_label"):
            return
        score_path = getattr(self.project, "score_source_path", None)
        if not score_path:
            self._set_score_analysis_notice("Analysis appears after you attach a matching PDF score.", "info")
            return
        if self._score_analysis_busy:
            return
        omr_summary = getattr(self.project, "score_omr_summary", None)
        if omr_summary:
            alignment = getattr(self.project, "score_alignment_summary", None)
            confidence = getattr(self.project, "score_alignment_confidence", None)
            focus = getattr(self.project, "score_focus_summary", None) or self._score_focus_label()
            pieces = ["OMR ready", str(focus)]
            if confidence:
                pieces.append(f"confidence {confidence}")
            elif alignment:
                pieces.append(self._compact_score_summary(str(alignment)))
            text = " · ".join(p for p in pieces if p)
            self._set_score_analysis_notice(text, "ready" if self._score_guidance_available() else "info")
            self.score_analysis_inline_label.setToolTip(self._compact_score_summary(f"OMR ready: {omr_summary}" + (f" · {alignment}" if alignment else "")))
            return
        summary = getattr(self.project, "score_analysis_summary", None) or getattr(self.project, "score_source_summary", None)
        alignment = getattr(self.project, "score_alignment_summary", None)
        if self._score_guidance_available():
            self._set_score_analysis_notice(self._compact_score_summary(f"Score guide ready: {summary or 'detected markings'}" + (f" · {alignment}" if alignment else "")), "ready")
        elif (getattr(self.project, "score_source_format", None) or "").lower() == "pdf":
            engine = locate_omr_engine(self.settings.get("audiveris_path"))
            base = summary or "PDF attached"
            if engine.available:
                self._set_score_analysis_notice(f"{base}. Click Analyze Score to run true OMR recognition with Audiveris.", "info")
            else:
                self._set_score_analysis_notice(f"{base}. True OMR is not configured yet; choose Audiveris here, then Analyze Score.", "warn")
        else:
            self._set_score_analysis_notice(summary or "Score source attached.", "info")

    def _apply_focused_pdf_analysis(self, score_path: str):
        page_range = self._score_focus_page_range()
        analysis = analyze_pdf_score(score_path, page_range=page_range)
        self.project.score_focus_page_count = analysis.page_count
        self.project.score_focus_summary = analysis.analyzed_pages_label
        self.project.score_source_status = analysis.confidence
        self.project.score_source_summary = analysis.summary
        self.project.score_source_detail = analysis.detail
        self.project.score_intent_summary = analysis.compact_text()
        self.project.score_intent_detail = analysis.detail
        self.project.score_analysis_status = analysis.confidence
        self.project.score_analysis_summary = analysis.compact_text()
        self.project.score_analysis_detail = analysis.detail
        self.project.score_guide_available = bool(analysis.has_guidance)
        if not self.project.score_guide_available:
            self.project.score_dynamics_enabled = False
            self.project.score_guidance_enabled = False
        if self.project.source_midi_path:
            try:
                report = compare_pdf_to_midi(score_path, self.project.source_midi_path, page_range=page_range)
                self.project.score_alignment_summary = report.summary
                self.project.score_alignment_detail = report.detail
            except Exception as exc:
                self.project.score_alignment_summary = "Score/MIDI fit check unavailable"
                self.project.score_alignment_detail = str(exc)
        self._update_score_analysis_details_from_current_state()
        return analysis

    def analyze_score_source(self) -> None:
        score_path = getattr(self.project, "score_source_path", None)
        if not score_path:
            self._set_score_analysis_notice("Attach a matching PDF score here first, then Analyze Score.", "warn")
            self.status_label.setText("Attach a PDF score first")
            return
        if self._score_analysis_busy:
            self._set_score_analysis_notice("Score analysis is already running…", "busy")
            return

        score_format = (getattr(self.project, "score_source_format", None) or "").lower()
        self._set_score_source(score_path)
        score_format = (getattr(self.project, "score_source_format", None) or score_format).lower()
        if score_format != "pdf":
            if self.project.score_guide_available:
                self._set_score_analysis_notice("Score guide ready. Check Use score guide; a pending take appears below.", "ready")
                self.status_label.setText("Score guide ready")
            else:
                self._set_score_analysis_notice(getattr(self.project, "score_alignment_summary", None) or "Score analyzed.", "info")
            return

        analysis = self._apply_focused_pdf_analysis(score_path)
        focus = analysis.analyzed_pages_label
        engine = locate_omr_engine(self.settings.get("audiveris_path"))
        self.project.score_omr_engine_status = "ready" if engine.available else "not configured"
        self.project.score_omr_engine_detail = engine.detail
        if not engine.available:
            if self.project.score_guide_available:
                self._set_score_analysis_notice(
                    f"Basic embedded score markings are usable from {focus}. For scanned-note recognition, choose Audiveris here, then Analyze Score again.",
                    "ready",
                )
                self.status_label.setText(f"Basic score guide ready from {focus}; OMR not configured")
            else:
                self._set_score_analysis_notice(
                    f"True OMR is required for {focus}. Choose Audiveris with the OMR button beside Analyze Score, then run Analyze Score again.",
                    "warn",
                )
                self.status_label.setText("See Score panel: OMR setup needed")
            self._refresh_all()
            return

        self._start_score_omr_job(score_path)

    def _score_omr_output_dir(self) -> Path:
        base = Path(self.project.project_directory or self.project.output_directory or self.settings.get("last_project_directory") or self._repo_root() / "projects")
        target = base / "omr"
        target.mkdir(parents=True, exist_ok=True)
        return target

    def _start_score_omr_job(self, score_path: str) -> None:
        self._score_analysis_busy = True
        self._score_analysis_result = None
        self._score_analysis_spinner_index = 0
        focus_label = self._score_focus_label()
        self._set_score_analysis_notice(f"Analyzing score {focus_label} with Audiveris…", "busy")
        self.status_label.setText(f"Analyzing score {focus_label} with OMR…")
        self._update_action_states()
        configured = self.settings.get("audiveris_path")
        output_dir = self._score_omr_output_dir()
        focus_range = self._score_focus_page_range()
        focus_label = self._score_focus_label()

        def worker() -> None:
            omr_source = Path(score_path)
            payload: dict[str, Any] = {"focus_label": focus_label, "focus_range": focus_range}
            if focus_range:
                try:
                    focused_name = f"{Path(score_path).stem}_pages_{focus_range[0]}_{focus_range[1]}.pdf"
                    omr_source = export_pdf_page_range(score_path, output_dir / "focused" / focused_name, focus_range)
                    payload["focused_pdf_path"] = str(omr_source)
                except Exception as exc:
                    payload["focus_error"] = str(exc)
            result = run_pdf_omr(omr_source, output_dir, configured_path=configured)
            payload["omr_result"] = result
            if result.ok and result.output_path:
                try:
                    reference = read_musicxml(result.output_path)
                    sequence = score_reference_dynamic_sequence(reference)
                    articulation_sequence = score_reference_articulation_sequence(reference)
                    tempo_words = score_reference_tempo_words(reference)
                    fermata_words = score_reference_fermatas(reference)
                    summary, detail = score_reference_summary(reference)
                    payload.update({
                        "reference": reference,
                        "dynamic_sequence": sequence,
                        "articulation_sequence": articulation_sequence,
                        "tempo_words": tempo_words,
                        "fermata_words": fermata_words,
                        "summary": summary,
                        "detail": detail,
                    })
                    if self.project.source_midi_path:
                        payload["fit_report"] = compare_score_reference_to_midi(reference, self.project.source_midi_path)
                except Exception as exc:
                    payload["parse_error"] = str(exc)
            self._score_analysis_result = payload

        self._score_analysis_thread = threading.Thread(target=worker, name="midi-performer-omr", daemon=True)
        self._score_analysis_thread.start()
        self.score_analysis_poll_timer.start()

    def _poll_score_analysis_job(self) -> None:
        if not self._score_analysis_busy:
            self.score_analysis_poll_timer.stop()
            return
        self._score_analysis_spinner_index = (self._score_analysis_spinner_index + 1) % len(self._spinner_frames)
        frame = self._spinner_frames[self._score_analysis_spinner_index]
        self._set_score_analysis_notice(f"Analyzing {self._score_focus_label()}… {frame}", "busy")
        done = self._score_analysis_thread is not None and not self._score_analysis_thread.is_alive()
        if done and self._score_analysis_result is not None:
            self.score_analysis_poll_timer.stop()
            payload = self._score_analysis_result
            self._score_analysis_result = None
            self._score_analysis_thread = None
            self._finish_score_omr_job(payload)

    def _finish_score_omr_job(self, payload: dict[str, Any]) -> None:
        self._score_analysis_busy = False
        focus_label = str(payload.get("focus_label") or self._score_focus_label())
        self.project.score_focus_summary = focus_label
        result = payload.get("omr_result")
        self.project.score_omr_engine_status = getattr(result, "status", None)
        self.project.score_omr_engine_detail = getattr(result, "detail", None)
        self.project.score_omr_output_path = getattr(result, "output_path", None)
        if result and getattr(result, "ok", False) and getattr(result, "output_path", None):
            parse_error = payload.get("parse_error")
            if parse_error:
                self.project.score_omr_summary = "OMR output found, but parsing failed"
                self.project.score_omr_detail = str(parse_error)
                self.project.score_omr_dynamic_sequence = []
                self.project.score_omr_articulation_sequence = []
                self.project.score_omr_tempo_words = []
                self.project.score_omr_fermata_words = []
                self.project.score_guide_available = False
                self._set_score_analysis_notice(f"OMR created a score file, but MIDI Performer could not parse it: {parse_error}", "warn")
            else:
                sequence = list(payload.get("dynamic_sequence", []) or [])
                articulation_sequence = list(payload.get("articulation_sequence", []) or [])
                tempo_words = list(payload.get("tempo_words", []) or [])
                fermata_words = list(payload.get("fermata_words", []) or [])
                base_summary = str(payload.get("summary") or getattr(result, "summary", "OMR complete"))
                self.project.score_omr_summary = f"{base_summary} · {focus_label}"
                extra_detail = str(payload.get("detail") or getattr(result, "detail", ""))
                focus_error = str(payload.get("focus_error") or "")
                self.project.score_omr_detail = extra_detail + (f" Focus warning: {focus_error}" if focus_error else "")
                self.project.score_omr_dynamic_sequence = sequence
                self.project.score_omr_articulation_sequence = articulation_sequence
                self.project.score_omr_tempo_words = tempo_words
                self.project.score_omr_fermata_words = fermata_words
                self.project.score_analysis_summary = self.project.score_omr_summary
                self.project.score_analysis_detail = self.project.score_omr_detail
                report = payload.get("fit_report")
                if report:
                    self.project.score_alignment_summary = report.summary
                    self.project.score_alignment_detail = report.detail
                self.project.score_guide_available = bool(sequence or articulation_sequence or tempo_words)
                if self.project.score_guide_available:
                    kinds = []
                    if sequence:
                        kinds.append("dynamics")
                    if articulation_sequence:
                        kinds.append("articulations")
                    if tempo_words:
                        kinds.append("tempo text")
                    if fermata_words:
                        kinds.append("fermatas detected")
                    self._set_score_analysis_notice(
                        self._compact_score_summary(f"OMR ready · {focus_label} · {', '.join(kinds)}"),
                        "ready",
                    )
                    self.score_analysis_inline_label.setToolTip(
                        self._compact_score_summary(f"OMR ready from {focus_label}: {base_summary} · {', '.join(kinds)} guide available. Check Use score guide; a pending take appears below.")
                    )
                    self.status_label.setText("OMR score guide ready")
                else:
                    self._set_score_analysis_notice(
                        f"OMR complete for {focus_label}: {base_summary}. It found score notes, but no dynamics/articulations/tempo text to guide a take yet.",
                        "info",
                    )
                    self.status_label.setText("OMR complete")
        else:
            detail = getattr(result, "detail", "OMR did not produce usable score data.") if result else "OMR did not return a result."
            self.project.score_omr_summary = "OMR did not produce a usable score"
            self.project.score_omr_detail = detail
            self.project.score_omr_dynamic_sequence = []
            self.project.score_omr_articulation_sequence = []
            self.project.score_omr_tempo_words = []
            self.project.score_omr_fermata_words = []
            self.project.score_guide_available = False
            self._set_score_analysis_notice(f"OMR did not produce usable score data for {focus_label}. {detail}", "warn")
            self.status_label.setText("OMR needs attention")
        self._update_score_analysis_details_from_current_state()
        self._refresh_all()

    def _alignment_score_image_result(self, bar_count: int = 4) -> tuple[QImage | None, str, int, bool]:
        """Render the first readable score system and map real barlines to MIDI bars.

        Both PyMuPDF and QtPdf paths perform the same barline detection. This is
        important on Windows installations where PyMuPDF may not be available.
        """
        score_path = getattr(self.project, "score_source_path", None)
        source = Path(str(score_path)) if score_path else None
        if not source or source.suffix.lower() != ".pdf" or not source.exists():
            return None, "No PDF score loaded", 0, False
        requested_bars = max(4, min(16, int(bar_count or 4)))
        focus_range = self._score_focus_page_range()
        page_index = max(0, int(focus_range[0]) - 1) if focus_range else 0
        errors: list[str] = []

        def _bytes_for_gray_image(image: QImage) -> tuple[bytes, int, int, int, QImage]:
            gray = image.convertToFormat(QImage.Format_Grayscale8)
            size = int(gray.sizeInBytes())
            bits = gray.bits()
            try:
                data = bytes(bits[:size])
            except TypeError:
                data = bytes(bits)
            return data, gray.width(), gray.height(), gray.bytesPerLine(), gray

        def _mapped_result(image: QImage, positions: list[int], renderer: str) -> tuple[QImage, str, int, bool] | None:
            slices = mapped_measure_slices(positions, requested_bars, image.width())
            if not slices:
                return None
            mapped_bars = len(slices)
            per_bar = 240
            target_height = 160
            strip = QImage(per_bar * mapped_bars, target_height, QImage.Format_RGB32)
            strip.fill(QColor("#ffffff"))
            strip_painter = QPainter(strip)
            strip_painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
            for idx, (x0, x1) in enumerate(slices):
                measure = image.copy(x0, 0, max(1, x1 - x0), image.height())
                fitted = measure.scaled(per_bar - 4, target_height - 4, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                target_x = idx * per_bar + max(2, (per_bar - fitted.width()) // 2)
                target_y = max(2, (target_height - fitted.height()) // 2)
                strip_painter.drawImage(target_x, target_y, fitted)
            strip_painter.end()
            status = f"PDF page {page_index + 1} · {mapped_bars} measures mapped by {renderer}"
            return strip, status, mapped_bars, True

        try:
            import fitz  # type: ignore

            with fitz.open(str(source)) as doc:
                if doc.page_count <= 0:
                    return None, "PDF preview unavailable: the document has no pages", 0, False
                page_index = max(0, min(page_index, doc.page_count - 1))
                page = doc.load_page(page_index)
                rect = page.rect
                # Include the full first system: title/header is excluded while all
                # staves and the system barlines remain present.
                crop = fitz.Rect(
                    rect.x0 + rect.width * 0.035,
                    rect.y0 + rect.height * 0.155,
                    rect.x1 - rect.width * 0.035,
                    rect.y0 + rect.height * 0.36,
                )
                pix = page.get_pixmap(matrix=fitz.Matrix(2.5, 2.5), clip=crop, colorspace=fitz.csGRAY, alpha=False)
                if pix.width <= 0 or pix.height <= 0:
                    raise RuntimeError("PyMuPDF returned an empty score crop")
                image = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_Grayscale8).copy()
                positions = detect_system_barlines(
                    pix.samples,
                    pix.width,
                    pix.height,
                    pix.stride,
                    min_height_fraction=0.42,
                )
                mapped = _mapped_result(image, positions, "PDF renderer")
                if mapped is not None:
                    return mapped
                return image, f"PDF page {page_index + 1} · system visible; barlines not mapped", 0, False
        except Exception as exc:
            errors.append(f"PyMuPDF: {exc}")

        try:
            from PySide6.QtPdf import QPdfDocument

            document = QPdfDocument(self)
            load_error = document.load(str(source))
            if document.status() == QPdfDocument.Status.Loading:
                loop = QEventLoop()
                timeout = QTimer()
                timeout.setSingleShot(True)
                timeout.timeout.connect(loop.quit)

                def _pdf_status_changed(status) -> None:
                    if status in (QPdfDocument.Status.Ready, QPdfDocument.Status.Error):
                        loop.quit()

                document.statusChanged.connect(_pdf_status_changed)
                timeout.start(4000)
                loop.exec()
                try:
                    document.statusChanged.disconnect(_pdf_status_changed)
                except Exception:
                    pass
            if document.status() == QPdfDocument.Status.Ready and document.pageCount() > 0:
                page_index = max(0, min(page_index, document.pageCount() - 1))
                points = document.pagePointSize(page_index)
                target_width = 1900
                target_height = max(500, int(target_width * points.height() / max(1.0, points.width())))
                full_page = document.render(page_index, QSize(target_width, target_height))
                if full_page is not None and not full_page.isNull():
                    if full_page.hasAlphaChannel():
                        opaque_page = QImage(full_page.size(), QImage.Format_RGB32)
                        opaque_page.fill(QColor("#ffffff"))
                        opaque_painter = QPainter(opaque_page)
                        opaque_painter.drawImage(0, 0, full_page)
                        opaque_painter.end()
                        full_page = opaque_page
                    crop_rect = QRect(
                        int(full_page.width() * 0.035),
                        int(full_page.height() * 0.155),
                        int(full_page.width() * 0.93),
                        int(full_page.height() * 0.205),
                    ).intersected(full_page.rect())
                    image = full_page.copy(crop_rect)
                    if not image.isNull():
                        data, width, height, stride, gray = _bytes_for_gray_image(image)
                        positions = detect_system_barlines(
                            data,
                            width,
                            height,
                            stride,
                            min_height_fraction=0.42,
                        )
                        mapped = _mapped_result(gray, positions, "QtPdf")
                        if mapped is not None:
                            return mapped
                        return image.copy(), f"PDF page {page_index + 1} · system visible; barlines not mapped", 0, False
                errors.append("QtPdf returned an empty rendered page")
            else:
                errors.append(f"QtPdf status {document.status()} / load error {load_error}")
        except Exception as exc:
            errors.append(f"QtPdf: {exc}")

        detail = "; ".join(errors[-2:])
        return None, ("PDF preview unavailable" + (f": {detail}" if detail else ""))[:180], 0, False

    def _alignment_score_image(self, bar_count: int = 4) -> QImage | None:
        image, _status, _mapped_bars, _measure_mapped = self._alignment_score_image_result(bar_count)
        return image

    def fix_score_alignment(self) -> None:
        midi_path = getattr(self.project, "source_midi_path", None)
        if not midi_path or not Path(str(midi_path)).exists():
            self._set_score_analysis_notice("Open a MIDI before aligning reference sources.", "warn")
            return

        score_path_value = getattr(self.project, "score_source_path", None)
        score_path = Path(str(score_path_value)) if score_path_value else None
        score_loaded = bool(score_path and score_path.exists() and score_path.suffix.lower() == ".pdf")
        audio_path_value = getattr(self.project, "reference_audio_path", None)
        audio_path = Path(str(audio_path_value)) if audio_path_value else None
        audio_loaded = bool(audio_path and audio_path.exists())
        if not score_loaded and not audio_loaded:
            self._set_score_analysis_notice("Load a PDF score or reference audio before opening alignment.", "warn")
            return

        # Upgrade old low-resolution audio analysis before opening the shared
        # timeline. The preview remains honest if decoding is unavailable.
        existing_waveform = list(getattr(self.project, "reference_audio_waveform_curve", []) or [])
        if audio_loaded and len(existing_waveform) < 256:
            try:
                refreshed = read_reference_audio(str(audio_path), ffmpeg_path=self.player.find_ffmpeg())
                if len(getattr(refreshed, "waveform_curve", []) or []) > len(existing_waveform):
                    self.project.reference_audio_duration_seconds = refreshed.duration_seconds
                    self.project.reference_audio_peak_percent = refreshed.peak_percent
                    self.project.reference_audio_rms_percent = refreshed.rms_percent
                    self.project.reference_audio_loudness_curve = list(refreshed.loudness_curve or [])
                    self.project.reference_audio_waveform_curve = list(refreshed.waveform_curve or [])
                    self.project.reference_audio_attack_curve = list(refreshed.attack_curve or [])
                    self.project.reference_audio_motion_curve = list(refreshed.motion_curve or [])
                    self.project.reference_audio_analysis_summary = refreshed.analysis_summary
                    self.project.reference_audio_analysis_detail = refreshed.analysis_detail
            except Exception:
                pass

        total_bars_float = max(1.0, midi_total_bars(midi_path) or 1.0)
        total_bars = max(1, int(total_bars_float + 0.999999))
        total_midi_beats = max(1.0, midi_total_beats(midi_path) or 1.0)

        pdf_map: PdfMeasureMap | None = None
        pdf_map_error = ""
        if score_loaded:
            try:
                pdf_map = build_pdf_measure_map(score_path)
                if pdf_map.measure_count <= 0:
                    pdf_map_error = "The PDF rendered, but full-system measure barlines were not detected."
            except Exception as exc:
                pdf_map_error = f"PDF measure mapping unavailable: {exc}"

        def score_measure_to_midi_bar(measure_index: int) -> int:
            if not pdf_map or pdf_map.measure_count <= 0:
                return max(0, min(total_bars - 1, int(measure_index)))
            fraction = max(0.0, min(1.0, float(measure_index) / float(max(1, pdf_map.measure_count))))
            return max(0, min(total_bars - 1, int(round(fraction * total_bars_float))))

        def midi_bar_to_score_measure(bar_index: int) -> int:
            if not pdf_map or pdf_map.measure_count <= 0:
                return max(0, int(bar_index))
            fraction = max(0.0, min(1.0, float(bar_index) / float(max(1.0, total_bars_float))))
            return max(0, min(pdf_map.measure_count - 1, int(round(fraction * pdf_map.measure_count))))

        focus_range = self._score_focus_page_range() if score_loaded else None
        focus_min_bar = 0
        focus_max_bar = total_bars - 1
        if pdf_map and pdf_map.measure_count > 0 and focus_range:
            first_measure = pdf_map.first_measure_for_page(focus_range[0])
            last_measure = pdf_map.last_measure_for_page(focus_range[1])
            focus_min_bar = score_measure_to_midi_bar(first_measure)
            focus_max_bar = max(focus_min_bar, score_measure_to_midi_bar(last_measure))

        original_preview_start = int(getattr(self.project, "alignment_preview_start_bar", 0) or 0)
        original_preview_window = int(getattr(self.project, "alignment_preview_window_bars", 4) or 4)
        if focus_range:
            initial_start = focus_min_bar
        else:
            initial_start = max(focus_min_bar, min(focus_max_bar, original_preview_start))
        initial_window = original_preview_window if original_preview_window in {4, 8, 16} else 4

        original_score_enabled = bool(getattr(self.project, "score_guidance_enabled", False))
        original_score_dynamics = bool(getattr(self.project, "score_dynamics_enabled", False))
        original_audio_enabled = bool(getattr(self.project, "reference_audio_guidance_enabled", False))
        # When usable guides exist, alignment preview must audition their combined
        # result. Cancel restores the user's previous guide switches.
        if self._score_guidance_available():
            self.project.score_guidance_enabled = True
            self.project.score_dynamics_enabled = True
        if self._reference_audio_guidance_available():
            self.project.reference_audio_guidance_enabled = True
        self._sync_score_guidance_widgets()
        self._sync_audio_guidance_widgets()

        dialog = QDialog(self)
        dialog.setWindowTitle("Align Sources")
        available = QApplication.primaryScreen().availableGeometry() if QApplication.primaryScreen() else QRect(0, 0, 1200, 760)
        dialog.setMinimumWidth(860)
        dialog.resize(
            min(1180, max(900, available.width() - 80)),
            min(640, max(570, available.height() - 90)),
        )
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(11, 9, 11, 9)
        layout.setSpacing(6)
        source_title = "PDF + MIDI + Audio" if score_loaded and audio_loaded else ("PDF + MIDI" if score_loaded else "MIDI + Audio")
        dialog_title = self._section_title(f"Align {source_title}")
        dialog_title.setMaximumHeight(28)
        dialog_title.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        layout.addWidget(dialog_title)

        nav_widget = QWidget(dialog)
        nav_widget.setFixedHeight(34)
        nav = QHBoxLayout(nav_widget)
        nav.setContentsMargins(0, 0, 0, 0)
        nav.setSpacing(7)
        previous_btn = QPushButton("◀ Previous")
        previous_btn.setToolTip("Move back by the current 4/8/16-bar window.")
        next_btn = QPushButton("Next ▶")
        next_btn.setToolTip("Move forward by the current 4/8/16-bar window.")
        section_label = QLabel("Start bar")
        section_label.setObjectName("ControlLabelCompact")
        section_spin = QSpinBox()
        section_spin.setMinimum(focus_min_bar + 1)
        section_spin.setMaximum(focus_max_bar + 1)
        section_spin.setValue(initial_start + 1)
        section_spin.setMinimumWidth(72)
        window_label = QLabel("Window")
        window_label.setObjectName("ControlLabelCompact")
        window_combo = QComboBox()
        window_combo.addItems(["4 bars", "8 bars", "16 bars"])
        window_combo.setCurrentText(f"{initial_window} bars")
        adjust_label = QLabel("Adjust")
        adjust_label.setObjectName("ControlLabelCompact")
        adjust_combo = QComboBox()
        if score_loaded:
            adjust_combo.addItem("PDF score", "score")
        if audio_loaded:
            adjust_combo.addItem("Reference audio", "audio")
        source_summary = QLabel("")
        source_summary.setObjectName("SubtleText")
        source_summary.setWordWrap(False)
        nav.addWidget(previous_btn)
        nav.addWidget(next_btn)
        nav.addSpacing(8)
        nav.addWidget(section_label)
        nav.addWidget(section_spin)
        nav.addSpacing(8)
        nav.addWidget(window_label)
        nav.addWidget(window_combo)
        nav.addSpacing(8)
        nav.addWidget(adjust_label)
        nav.addWidget(adjust_combo)
        nav.addWidget(source_summary, 1)
        layout.addWidget(nav_widget)
        single_reference = adjust_combo.count() <= 1
        adjust_label.setVisible(not single_reference)
        adjust_combo.setVisible(not single_reference)

        preview_widget = AlignmentPreviewWidget(dialog)
        preview_widget.set_sources(score_loaded, audio_loaded)
        layout.addWidget(preview_widget)

        original_score = {
            "shift_percent": float(getattr(self.project, "score_alignment_shift_percent", 0.0) or 0.0),
            "stretch_percent": float(getattr(self.project, "score_alignment_stretch_percent", 100.0) or 100.0),
            "shift_beats": float(getattr(self.project, "score_alignment_shift_beats", 0.0) or 0.0),
            "length_beats": float(getattr(self.project, "score_alignment_length_beats", 0.0) or 0.0),
        }
        original_audio = {
            "shift_percent": float(getattr(self.project, "reference_audio_alignment_shift_percent", 0.0) or 0.0),
            "stretch_percent": float(getattr(self.project, "reference_audio_alignment_stretch_percent", 100.0) or 100.0),
            "shift_beats": float(getattr(self.project, "reference_audio_alignment_shift_beats", 0.0) or 0.0),
            "length_beats": float(getattr(self.project, "reference_audio_alignment_length_beats", 0.0) or 0.0),
        }
        original_pending_seed = self._pending_variation_seed

        def legacy_beats(state: dict[str, float]) -> tuple[float, float]:
            shift = state["shift_beats"] if abs(state["shift_beats"]) > 0.0001 else state["shift_percent"] / 10.0
            length = state["length_beats"] if abs(state["length_beats"]) > 0.0001 else (state["stretch_percent"] - 100.0) / 4.0
            return float(shift), float(length)

        score_shift, score_length = legacy_beats(original_score)
        audio_shift, audio_length = legacy_beats(original_audio)
        states: dict[str, dict[str, float]] = {
            "score": {"shift_beats": score_shift, "length_beats": score_length},
            "audio": {"shift_beats": audio_shift, "length_beats": audio_length},
        }

        def beats_to_shift_percent(beats: float) -> float:
            return round((float(beats) / total_midi_beats) * 100.0, 4)

        def beats_to_stretch_percent(beats: float) -> float:
            return round(((total_midi_beats + float(beats)) / total_midi_beats) * 100.0, 4)

        def apply_states_to_project() -> None:
            self.project.score_alignment_shift_beats = states["score"]["shift_beats"]
            self.project.score_alignment_length_beats = states["score"]["length_beats"]
            self.project.score_alignment_shift_percent = beats_to_shift_percent(states["score"]["shift_beats"])
            self.project.score_alignment_stretch_percent = beats_to_stretch_percent(states["score"]["length_beats"])
            self.project.reference_audio_alignment_shift_beats = states["audio"]["shift_beats"]
            self.project.reference_audio_alignment_length_beats = states["audio"]["length_beats"]
            self.project.reference_audio_alignment_shift_percent = beats_to_shift_percent(states["audio"]["shift_beats"])
            self.project.reference_audio_alignment_stretch_percent = beats_to_stretch_percent(states["audio"]["length_beats"])

        shift_slider = QSlider(Qt.Horizontal)
        length_slider = QSlider(Qt.Horizontal)
        shift_slider.setRange(-64, 64)
        max_shorter_steps = max(0, min(64, int(round(max(0.0, total_midi_beats - 1.0) * 2.0))))
        length_slider.setRange(-max_shorter_steps, 64)
        shift_slider.setSingleStep(1)
        length_slider.setSingleStep(1)
        shift_slider.setPageStep(4)
        length_slider.setPageStep(4)
        shift_text = QLabel("Score start")
        length_text_label = QLabel("Score end")
        shift_text.setObjectName("ControlLabelCompact")
        length_text_label.setObjectName("ControlLabelCompact")
        shift_value = QLabel("0 beats")
        length_value = QLabel("no length change")
        shift_value.setMinimumWidth(92)
        length_value.setMinimumWidth(118)

        def beat_label(steps: int) -> str:
            beats = int(steps) / 2.0
            if abs(beats) < 0.001:
                return "0 beats"
            return f"{abs(beats):g} beats {'later' if beats > 0 else 'earlier'}"

        def length_label(steps: int) -> str:
            beats = int(steps) / 2.0
            if abs(beats) < 0.001:
                return "no length change"
            return f"{abs(beats):g} beats {'longer' if beats > 0 else 'shorter'}"

        slider_widget = QWidget(dialog)
        slider_widget.setFixedHeight(46)
        slider_grid = QGridLayout(slider_widget)
        slider_grid.setContentsMargins(0, 0, 0, 0)
        slider_grid.setHorizontalSpacing(8)
        slider_grid.setVerticalSpacing(5)
        slider_grid.addWidget(shift_text, 0, 0)
        slider_grid.addWidget(QLabel("earlier"), 0, 1)
        slider_grid.addWidget(shift_slider, 0, 2)
        slider_grid.addWidget(QLabel("later"), 0, 3)
        slider_grid.addWidget(shift_value, 0, 4)
        slider_grid.addWidget(length_text_label, 1, 0)
        slider_grid.addWidget(QLabel("shorter"), 1, 1)
        slider_grid.addWidget(length_slider, 1, 2)
        slider_grid.addWidget(QLabel("longer"), 1, 3)
        slider_grid.addWidget(length_value, 1, 4)
        slider_grid.setColumnStretch(2, 1)
        layout.addWidget(slider_widget)

        feedback = QLabel("")
        feedback.setObjectName("ScoreAnalysisInfo")
        feedback.setWordWrap(True)
        feedback.setMaximumHeight(42)
        feedback.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        layout.addWidget(feedback)

        image_cache: dict[tuple[int, int], tuple[QImage | None, str, int]] = {}
        current_profile: list[MidiPreviewProfile | None] = [None]
        playing_window: dict[str, int] = {"start_tick": 0, "end_tick": 0}
        last_signature: list[tuple[float, float, float, float] | None] = [None]

        def preview_bars() -> int:
            try:
                return int(window_combo.currentText().split()[0])
            except Exception:
                return 4

        def current_start_bar() -> int:
            return max(focus_min_bar, min(focus_max_bar, section_spin.value() - 1))

        def selected_adjust_source() -> str:
            return str(adjust_combo.currentData() or ("score" if score_loaded else "audio"))

        def qimage_strip_for(start_bar: int, bars: int) -> tuple[QImage | None, str, int]:
            key = (start_bar, bars)
            if key in image_cache:
                return image_cache[key]
            if not score_loaded:
                result = (None, "No PDF score loaded", 0)
                image_cache[key] = result
                return result
            if not pdf_map or pdf_map.measure_count <= 0:
                # Never replace an attached score with a dependency-error pane.
                # The direct renderer still shows real notation even when a full
                # document measure map could not be built.
                fallback_image, fallback_status, fallback_mapped, _mapped = self._alignment_score_image_result(bars)
                if fallback_image is not None and not fallback_image.isNull():
                    result = (fallback_image, fallback_status + " · direct PDF view", fallback_mapped)
                else:
                    result = (None, pdf_map_error or fallback_status or "PDF preview unavailable", 0)
                image_cache[key] = result
                return result
            try:
                score_start = midi_bar_to_score_measure(start_bar)
                cells, status = render_pdf_measure_cells(score_path, pdf_map, start_measure=score_start, bar_count=bars)
                if not cells:
                    fallback_image, fallback_status, fallback_mapped, _mapped = self._alignment_score_image_result(bars)
                    if fallback_image is not None and not fallback_image.isNull():
                        result = (fallback_image, fallback_status + " · direct PDF view", fallback_mapped)
                    else:
                        result = (None, status, 0)
                    image_cache[key] = result
                    return result
                strip = compose_pdf_measure_strip(cells, bars)
                view_label = "detail view" if bars <= 8 else "overview"
                result = (strip, f"{status} · {view_label}", len(cells))
                image_cache[key] = result
                return result
            except Exception as exc:
                fallback_image, fallback_status, fallback_mapped, _mapped = self._alignment_score_image_result(bars)
                if fallback_image is not None and not fallback_image.isNull():
                    result = (fallback_image, fallback_status + " · direct PDF view", fallback_mapped)
                else:
                    result = (None, f"PDF section render failed: {exc}", 0)
                image_cache[key] = result
                return result

        def update_spin_limits() -> None:
            bars = preview_bars()
            maximum_start = max(focus_min_bar, focus_max_bar - bars + 1)
            section_spin.blockSignals(True)
            section_spin.setMinimum(focus_min_bar + 1)
            section_spin.setMaximum(maximum_start + 1)
            section_spin.setValue(max(focus_min_bar + 1, min(maximum_start + 1, section_spin.value())))
            section_spin.blockSignals(False)

        def refresh_preview(*, mark_pending: bool = False) -> None:
            bars = preview_bars()
            update_spin_limits()
            start_bar = current_start_bar()
            profile = midi_preview_profile(midi_path, bars=bars, start_bar=start_bar)
            current_profile[0] = profile
            preview_widget.set_preview_window(bars)
            preview_widget.set_midi_context(profile)
            preview_widget.set_adjusting_source(selected_adjust_source())
            preview_widget.set_score_alignment(
                round(states["score"]["shift_beats"] * 2.0),
                round(states["score"]["length_beats"] * 2.0),
            )
            preview_widget.set_audio_alignment(
                round(states["audio"]["shift_beats"] * 2.0),
                round(states["audio"]["length_beats"] * 2.0),
            )
            if score_loaded:
                score_image, score_status, mapped = qimage_strip_for(start_bar, bars)
                preview_widget.set_score_image(score_image, score_status, mapped_bars=mapped, measure_mapped=bool(mapped))
            preview_widget.set_audio_context(
                list(getattr(self.project, "reference_audio_loudness_curve", []) or []),
                list(getattr(self.project, "reference_audio_attack_curve", []) or []),
                list(getattr(self.project, "reference_audio_motion_curve", []) or []),
                "Reference audio",
                waveform=list(getattr(self.project, "reference_audio_waveform_curve", []) or []),
            )
            apply_states_to_project()
            self.project.alignment_preview_start_bar = start_bar
            self.project.alignment_preview_window_bars = bars
            page_note = ""
            if score_loaded and pdf_map and pdf_map.measure_count:
                measure_index = midi_bar_to_score_measure(start_bar)
                measure = pdf_map.measures[min(len(pdf_map.measures) - 1, measure_index)]
                page_note = f" · PDF page {measure.page_index + 1}"
            sources = ["MIDI"]
            if score_loaded:
                sources.append("PDF")
            if audio_loaded:
                sources.append("audio")
            source_summary.setText(f"{' + '.join(sources)} · bars {start_bar + 1}–{min(total_bars, start_bar + bars)}{page_note}")
            selected = selected_adjust_source()
            state = states[selected]
            selected_name = "PDF score" if selected == "score" else "Reference audio"
            feedback.setText(
                f"Adjusting {selected_name}: {beat_label(round(state['shift_beats'] * 2.0))}; "
                f"{length_label(round(state['length_beats'] * 2.0))}. "
                "Play Current Section auditions the pending take with every usable guide."
            )
            signature = (
                states["score"]["shift_beats"],
                states["score"]["length_beats"],
                states["audio"]["shift_beats"],
                states["audio"]["length_beats"],
            )
            if mark_pending and signature != last_signature[0]:
                last_signature[0] = signature
                self._mark_variation_changed(f"{selected_name} alignment changed — pending TAKE updated.")

        def load_adjust_source() -> None:
            selected = selected_adjust_source()
            state = states[selected]
            shift_slider.blockSignals(True)
            length_slider.blockSignals(True)
            shift_slider.setValue(max(-64, min(64, round(state["shift_beats"] * 2.0))))
            length_slider.setValue(max(-max_shorter_steps, min(64, round(state["length_beats"] * 2.0))))
            shift_slider.blockSignals(False)
            length_slider.blockSignals(False)
            source_name = "Score" if selected == "score" else "Audio"
            shift_text.setText(f"{source_name} start")
            length_text_label.setText(f"{source_name} end")
            shift_value.setText(beat_label(shift_slider.value()))
            length_value.setText(length_label(length_slider.value()))
            preview_widget.set_playing(False)
            refresh_preview(mark_pending=False)

        def sliders_changed(_value: int) -> None:
            selected = selected_adjust_source()
            states[selected]["shift_beats"] = shift_slider.value() / 2.0
            states[selected]["length_beats"] = length_slider.value() / 2.0
            shift_value.setText(beat_label(shift_slider.value()))
            length_value.setText(length_label(length_slider.value()))
            refresh_preview(mark_pending=True)

        shift_slider.valueChanged.connect(sliders_changed)
        length_slider.valueChanged.connect(sliders_changed)
        adjust_combo.currentIndexChanged.connect(lambda _index: load_adjust_source())
        window_combo.currentTextChanged.connect(lambda _text: refresh_preview(mark_pending=False))
        section_spin.valueChanged.connect(lambda _value: refresh_preview(mark_pending=False))
        previous_btn.clicked.connect(lambda: section_spin.setValue(max(section_spin.minimum(), section_spin.value() - preview_bars())))
        next_btn.clicked.connect(lambda: section_spin.setValue(min(section_spin.maximum(), section_spin.value() + preview_bars())))

        play_preview = QPushButton("Play Current Section")
        play_preview.setObjectName("PlayButton")
        stop_preview = QPushButton("Stop")
        stop_preview.setObjectName("StopButton")
        playback_cursor_timer = QTimer(dialog)
        playback_cursor_timer.setInterval(45)

        def update_alignment_cursor() -> None:
            snapshot = self.playback_engine.poll()
            start_tick = playing_window["start_tick"]
            end_tick = playing_window["end_tick"]
            playing = snapshot.state == PlaybackState.PLAYING
            cursor = AlignmentPreviewWidget.visual_cursor_fraction(
                int(snapshot.tick),
                start_tick,
                end_tick,
                current_profile[0],
                playing=playing,
            )
            preview_widget.set_cursor_fraction(cursor, visible=playing)
            if playing and int(snapshot.tick) >= end_tick:
                playback_cursor_timer.stop()
                self.stop_playback(silent=True)
                preview_widget.set_cursor_fraction(0.0, visible=False)
                feedback.setText(feedback.text().split(" · playing")[0] + " · section complete")
            elif not playing:
                playback_cursor_timer.stop()
                preview_widget.set_cursor_fraction(0.0, visible=False)

        def play_alignment_preview() -> None:
            refresh_preview(mark_pending=False)
            profile = current_profile[0]
            if profile is None:
                feedback.setText("The selected MIDI section could not be prepared.")
                return
            try:
                seed = self._ensure_pending_variation_seed()
                target = self._work_versions_dir() / f"alignment_pending_{seed}.mid"
                create_variation(
                    midi_path,
                    target,
                    self.create_level_key,
                    seed=seed,
                    expressiveness=int(self.project.expressiveness if self.project.expressiveness is not None else 0),
                    timing_feel=self.project.timing_feel,
                    score_guidance=self._score_guidance_plan(),
                    reference_audio_guidance=self._reference_audio_guidance_plan(),
                )
                target_profile = midi_preview_profile(target, bars=profile.bars, start_bar=profile.start_bar)
                playing_window["start_tick"] = int(target_profile.window_start_tick)
                playing_window["end_tick"] = int(target_profile.window_end_tick)
                item = {
                    "id": "__alignment_pending__",
                    "name": f"Pending {self._next_variation_number()} · bars {profile.start_bar + 1}–{profile.end_bar}",
                    "path": str(target),
                    "volume_percent": self._master_volume_percent(),
                    "tempo_percent": int(self.project.playback_tempo_percent or 100),
                    "mixer": self._current_take_settings_snapshot()["mixer"],
                }
                self._play_item(item, start_tick_override=target_profile.window_start_tick)
                preview_widget.set_cursor_fraction(0.0, visible=True)
                playback_cursor_timer.start()
                feedback.setText(feedback.text().split(" · playing")[0] + " · playing")
            except Exception as exc:
                preview_widget.set_playing(False)
                feedback.setText(f"Alignment preview playback failed: {exc}")

        def stop_alignment_preview() -> None:
            playback_cursor_timer.stop()
            self.stop_playback(silent=True)
            preview_widget.set_cursor_fraction(0.0, visible=False)
            feedback.setText(feedback.text().split(" · playing")[0] + " · stopped")

        playback_cursor_timer.timeout.connect(update_alignment_cursor)
        play_preview.clicked.connect(play_alignment_preview)
        stop_preview.clicked.connect(stop_alignment_preview)

        buttons = QHBoxLayout()
        buttons.addWidget(play_preview)
        buttons.addWidget(stop_preview)
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        accept = QPushButton("Accept Alignment")
        accept.setObjectName("ExportButton")
        buttons.addWidget(cancel)
        buttons.addWidget(accept)
        layout.addLayout(buttons)
        cancel.clicked.connect(dialog.reject)
        accept.clicked.connect(dialog.accept)

        update_spin_limits()
        load_adjust_source()
        dialog.setStyleSheet(self._stylesheet())
        result = dialog.exec()
        playback_cursor_timer.stop()
        self.stop_playback(silent=True)

        if result != QDialog.Accepted:
            self.project.score_alignment_shift_percent = original_score["shift_percent"]
            self.project.score_alignment_stretch_percent = original_score["stretch_percent"]
            self.project.score_alignment_shift_beats = original_score["shift_beats"]
            self.project.score_alignment_length_beats = original_score["length_beats"]
            self.project.reference_audio_alignment_shift_percent = original_audio["shift_percent"]
            self.project.reference_audio_alignment_stretch_percent = original_audio["stretch_percent"]
            self.project.reference_audio_alignment_shift_beats = original_audio["shift_beats"]
            self.project.reference_audio_alignment_length_beats = original_audio["length_beats"]
            self.project.score_guidance_enabled = original_score_enabled
            self.project.score_dynamics_enabled = original_score_dynamics
            self.project.reference_audio_guidance_enabled = original_audio_enabled
            self.project.alignment_preview_start_bar = original_preview_start
            self.project.alignment_preview_window_bars = original_preview_window
            self._pending_variation_seed = original_pending_seed
            self._sync_score_guidance_widgets()
            self._sync_audio_guidance_widgets()
            self._mark_variation_changed("Alignment canceled; previous pending TAKE restored.")
            self.status_label.setText("Alignment canceled")
            return

        apply_states_to_project()
        self.project.score_alignment_repair_summary = alignment_repair_summary(
            self.project.score_alignment_shift_percent,
            self.project.score_alignment_stretch_percent,
            shift_beats=self.project.score_alignment_shift_beats,
            length_beats=self.project.score_alignment_length_beats,
        )
        self.project.score_alignment_repair_needed = False
        self._mark_variation_changed("Shared score/audio alignment accepted; pending TAKE updated.")
        self._refresh_all()
        self.status_label.setText("Score, MIDI and audio alignment accepted")

    def create_score_guided_take(self) -> None:
        if not self.project.source_midi_path:
            self.status_label.setText("Open a MIDI first")
            return
        if not getattr(self.project, "score_source_path", None):
            self.status_label.setText("Attach a matching PDF score first")
            return
        if not self._score_guidance_available():
            self.analyze_score_source()
        if not self._score_guidance_available():
            self._set_score_analysis_notice(
                "Score take is not ready yet. Analyze Score needs detected dynamics, articulations, or tempo text from embedded PDF text or OMR first.",
                "warn",
            )
            return
        self.project.score_dynamics_enabled = True
        self.project.score_guidance_enabled = True
        self._sync_score_guidance_widgets()
        self.create_preview()

    def open_score_pdf(self) -> None:
        score_path = getattr(self.project, "score_source_path", None)
        if not score_path or (getattr(self.project, "score_source_format", None) or "").lower() != "pdf":
            self.status_label.setText("No PDF score attached")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(score_path))))

    def load_reference_audio(self) -> None:
        start = self.settings.get("last_reference_audio_directory") or self.settings.get("last_midi_directory") or str(Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Load Reference Performance",
            start,
            "Audio Files (*.wav *.mp3 *.flac *.aiff *.aif *.m4a);;WAV Audio (*.wav);;All Files (*.*)",
        )
        if path:
            self._set_reference_audio(path)

    def _set_reference_audio(self, path: str) -> None:
        if self._audio_analysis_busy:
            self.status_label.setText("Reference audio analysis is already running")
            return
        self._audio_analysis_busy = True
        self._audio_analysis_result = None
        self._audio_analysis_spinner_index = 0
        if hasattr(self, "audio_analysis_progress"):
            self.audio_analysis_progress.setVisible(True)
        if hasattr(self, "reference_audio_label"):
            self.reference_audio_label.setText(f"Audio: analyzing {Path(path).name}…")
            self.reference_audio_label.setToolTip("Decoding and analyzing reference audio. This can take a few seconds for long MP3 files.")
        self.status_label.setText("Analyzing reference audio…")
        ffmpeg_path = self.player.find_ffmpeg()

        def worker() -> None:
            try:
                info = read_reference_audio(path, ffmpeg_path=ffmpeg_path)
                self._audio_analysis_result = {"ok": True, "path": path, "info": info}
            except Exception as exc:  # noqa: BLE001
                self._audio_analysis_result = {"ok": False, "path": path, "error": str(exc)}

        self._audio_analysis_thread = threading.Thread(target=worker, name="midi-performer-reference-audio", daemon=True)
        self._audio_analysis_thread.start()
        self.audio_analysis_poll_timer.start()
        self._update_action_states()

    def _poll_audio_analysis_job(self) -> None:
        if not self._audio_analysis_busy:
            self.audio_analysis_poll_timer.stop()
            return
        self._audio_analysis_spinner_index = (self._audio_analysis_spinner_index + 1) % len(self._spinner_frames)
        frame = self._spinner_frames[self._audio_analysis_spinner_index]
        if hasattr(self, "reference_audio_label"):
            self.reference_audio_label.setText(f"Audio: analyzing… {frame}")
        done = self._audio_analysis_thread is not None and not self._audio_analysis_thread.is_alive()
        if done and self._audio_analysis_result is not None:
            self.audio_analysis_poll_timer.stop()
            payload = self._audio_analysis_result
            self._audio_analysis_result = None
            self._audio_analysis_thread = None
            self._finish_reference_audio_analysis(payload)

    def _finish_reference_audio_analysis(self, payload: dict[str, Any]) -> None:
        self._audio_analysis_busy = False
        if hasattr(self, "audio_analysis_progress"):
            self.audio_analysis_progress.setVisible(False)
        if not payload.get("ok"):
            self._refresh_all()
            QMessageBox.warning(self, "Reference audio", f"Could not load reference audio.\n{payload.get('error') or 'Unknown error'}")
            self.status_label.setText("Reference audio analysis failed")
            return
        info = payload.get("info")
        if info is None:
            self._refresh_all()
            self.status_label.setText("Reference audio analysis failed")
            return
        path = str(payload.get("path") or getattr(info, "path", ""))
        previous_audio_path = str(getattr(self.project, "reference_audio_path", "") or "")
        self.project.reference_audio_path = info.path
        if previous_audio_path and Path(previous_audio_path) != Path(str(info.path)):
            self.project.reference_audio_alignment_shift_percent = 0.0
            self.project.reference_audio_alignment_stretch_percent = 100.0
            self.project.reference_audio_alignment_shift_beats = 0.0
            self.project.reference_audio_alignment_length_beats = 0.0
        self.project.reference_audio_format = info.format
        compact = info.compact_text()
        self.project.reference_audio_summary = compact.split(" · ", 1)[1] if " · " in compact else compact
        self.project.reference_audio_duration_seconds = info.duration_seconds
        self.project.reference_audio_analysis_summary = info.analysis_summary
        self.project.reference_audio_analysis_detail = info.analysis_detail
        self.project.reference_audio_peak_percent = info.peak_percent
        self.project.reference_audio_rms_percent = info.rms_percent
        self.project.reference_audio_loudness_curve = list(info.loudness_curve or [])
        self.project.reference_audio_waveform_curve = list(getattr(info, "waveform_curve", []) or [])
        self.project.reference_audio_attack_curve = list(getattr(info, "attack_curve", []) or [])
        self.project.reference_audio_motion_curve = list(getattr(info, "motion_curve", []) or [])
        if not any(len(curve or []) >= 4 for curve in (self.project.reference_audio_loudness_curve, self.project.reference_audio_attack_curve, self.project.reference_audio_motion_curve)):
            self.project.reference_audio_guidance_enabled = False
        if any(len(curve or []) >= 4 for curve in (self.project.reference_audio_loudness_curve, self.project.reference_audio_attack_curve, self.project.reference_audio_motion_curve)):
            self.project.reference_audio_detail = "Reference performance analyzed. Use audio guide can shape newly created takes with dynamics, attack, and pacing proxies; full note-level audio alignment comes later."
            status = f"Reference audio analyzed — {info.analysis_summary}"
        else:
            self.project.reference_audio_detail = info.analysis_detail or "Reference performance attached, but no usable contour is available yet."
            status = "Reference performance attached — audio guide unavailable"
        if path:
            self.settings.set("last_reference_audio_directory", str(Path(path).parent))
            self._remember_recent_path("recent_reference_audio_paths", path)
        self._refresh_all()
        self.status_label.setText(status)

    def clear_reference_audio(self) -> None:
        self.project.reference_audio_path = None
        self.project.reference_audio_format = None
        self.project.reference_audio_summary = None
        self.project.reference_audio_detail = None
        self.project.reference_audio_duration_seconds = None
        self.project.reference_audio_analysis_summary = None
        self.project.reference_audio_analysis_detail = None
        self.project.reference_audio_peak_percent = None
        self.project.reference_audio_rms_percent = None
        self.project.reference_audio_loudness_curve = []
        self.project.reference_audio_waveform_curve = []
        self.project.reference_audio_attack_curve = []
        self.project.reference_audio_motion_curve = []
        self.project.reference_audio_alignment_shift_percent = 0.0
        self.project.reference_audio_alignment_stretch_percent = 100.0
        self.project.reference_audio_alignment_shift_beats = 0.0
        self.project.reference_audio_alignment_length_beats = 0.0
        self.project.reference_audio_guidance_enabled = False
        self._refresh_all()
        self.status_label.setText("Reference audio cleared")


    def _apply_live_mixer_maps(
        self,
        programs: dict[str, int],
        volumes: dict[str, int],
        pans: dict[str, int],
        solos: dict[str, bool],
        mutes: dict[str, bool],
    ) -> None:
        try:
            snapshot = self.playback_engine.apply_live_mixer(programs, volumes, pans, solos, mutes)
            if self.playback_engine.is_playing() or self.playback_engine.is_paused():
                self._sync_playback_snapshot(snapshot)
                self.status_label.setText("Live mixer updated")
        except Exception as exc:
            self.status_label.setText(f"Live mixer unavailable: {exc}")

    def _apply_project_mixer_live(self) -> None:
        self._apply_live_mixer_maps(
            dict(self.project.track_program_overrides or {}),
            dict(self.project.track_volume_overrides or {}),
            dict(self.project.track_pan_overrides or {}),
            dict(self.project.track_solo_overrides or {}),
            dict(self.project.track_mute_overrides or {}),
        )

    def open_mixer(self) -> None:
        if not self.project.source_midi_path:
            QMessageBox.information(self, "Track Mixer", "Open a MIDI file before using the mixer.")
            return
        if not self._playback_ready or self._playback_preparing:
            QMessageBox.information(self, "Track Mixer", "MIDI Performer is preparing playback. The mixer opens once the MIDI is ready to play.")
            return
        dialog = MixerDialog(self)
        dialog.setStyleSheet(self._stylesheet())
        dialog.exec()
        self._refresh_all()

    def _checked_export_items(self) -> list[dict[str, Any]]:
        rows = [self._original_take_row()] + list(self.project.takes or [])
        checked: list[dict[str, Any]] = []
        for item in rows:
            key = str(item.get("id") or "original")
            cb = self.take_checkboxes.get(key)
            if cb and cb.isChecked() and item.get("path"):
                checked.append(item)
        return checked

    def show_export_dialog(self) -> None:
        checked = self._checked_export_items()
        if not checked:
            self.status_label.setText("No checked takes to export")
            return
        self._show_export_dialog_for_items(checked, "Export checked takes")

    def show_export_best_dialog(self) -> None:
        best = self._best_take()
        if not best:
            self.status_label.setText("No Best take marked")
            QMessageBox.information(self, "Export Best", "Mark one numbered version as Best first.")
            return
        self._show_export_dialog_for_items([best], "Export Best take")

    def _show_export_dialog_for_items(self, items: list[dict[str, Any]], title: str = "Export takes") -> None:
        start = str(self.project.output_directory or self.settings.get("last_output_directory") or self._repo_root() / "exports")
        stem = self._safe_label(Path(self.project.source_midi_path or "midi_performer_export").stem)
        if len(items) == 1 and bool(items[0].get("best")):
            stem = f"{stem}_best"
        default_kind = str(self.settings.get("last_export_kind") or "wav")
        dialog = ExportOptionsDialog(self, start, stem, len(items), default_kind=default_kind)
        dialog.setWindowTitle(title)
        dialog.setStyleSheet(self._stylesheet())
        if dialog.exec() != QDialog.Accepted:
            return
        self._export_take_items(items, dialog.export_kind, dialog.folder, dialog.base_name)

    def export_checked_takes(self, export_kind: str = "midi", folder: str | None = None, base_name: str | None = None) -> None:
        checked = self._checked_export_items()
        if not checked:
            self.status_label.setText("No checked takes to export")
            return
        self._export_take_items(checked, export_kind, folder, base_name)

    def _export_take_items(self, items: list[dict[str, Any]], export_kind: str = "midi", folder: str | None = None, base_name: str | None = None, run_async: bool = True) -> None:
        export_kind = (export_kind or "midi").lower()
        if export_kind not in {"midi", "wav", "mp3"}:
            export_kind = "midi"
        checked = [self._clone_export_item(item) for item in items if item.get("path")]
        if not checked:
            self.status_label.setText("No playable takes to export")
            return
        if self._export_busy:
            self.status_label.setText("Export already running…")
            return
        if export_kind in {"wav", "mp3"} and not self._ensure_soundfont():
            return
        start = str(self.project.output_directory or self.settings.get("last_output_directory") or self._repo_root() / "exports")
        if folder is None:
            folder = QFileDialog.getExistingDirectory(self, "Choose export folder", start)
        if not folder:
            return
        base = self._safe_label(str(base_name or Path(self.project.source_midi_path or "midi_performer_export").stem or "midi_performer_export"))
        self.project.output_directory = folder
        self.settings.set("last_output_directory", folder)
        self.settings.set("last_export_kind", export_kind)
        label = {"midi": "MIDI", "wav": "WAV audio", "mp3": "MP3 audio"}[export_kind]
        best_text = " Best" if len(checked) == 1 and bool(checked[0].get("best")) else ""
        message = f"Exporting {len(checked)}{best_text} row(s) as {label}…"
        self.status_label.setText(message)
        self.playback_status_label.setText(message)
        if not run_async:
            result = self._run_export_job(checked, export_kind, folder, base)
            self._finish_export_job(result)
            return
        self._export_busy = True
        self._export_result = None
        self._export_spinner_index = 0
        self._update_action_states()
        self._export_progress_dialog = ExportProgressDialog(self, message)
        self._export_progress_dialog.setStyleSheet(self._stylesheet())
        self._export_progress_dialog.show()

        def worker() -> None:
            self._export_result = self._run_export_job(checked, export_kind, folder, base)

        self._export_thread = threading.Thread(target=worker, daemon=True)
        self._export_thread.start()
        self.export_poll_timer.start()

    def _clone_export_item(self, item: dict[str, Any]) -> dict[str, Any]:
        clone = dict(item)
        mixer = clone.get("mixer")
        if isinstance(mixer, dict):
            clone["mixer"] = {
                "programs": dict(mixer.get("programs", {}) or {}),
                "volumes": dict(mixer.get("volumes", {}) or {}),
                "pans": dict(mixer.get("pans", {}) or {}),
                "solos": dict(mixer.get("solos", {}) or {}),
                "mutes": dict(mixer.get("mutes", {}) or {}),
            }
        return clone

    def _run_export_job(self, checked: list[dict[str, Any]], export_kind: str, folder: str, base: str) -> dict[str, Any]:
        label = {"midi": "MIDI", "wav": "WAV audio", "mp3": "MP3 audio"}[export_kind]
        exported_paths: list[Path] = []
        try:
            for item in checked:
                path = item.get("path")
                if not path:
                    continue
                take_label = self._safe_label(str(item.get("name") or "take"))
                # MIDI export has no universal player gain, so it bakes the saved master volume into velocities.
                # WAV/MP3 export does not bake master volume; it uses the same FluidSynth gain as live playback.
                prepared = Path(self._prepared_export_midi_for_item(item, f"export_{take_label}", bake_master_volume=(export_kind == "midi")))
                export_stem = f"{base}_{take_label}"
                volume_percent = self._take_volume_percent(item, default=100)
                audio_gain = audio_gain_for_volume_percent(volume_percent)
                if export_kind == "midi":
                    target = Path(folder) / f"{export_stem}.mid"
                    shutil.copy2(prepared, target)
                elif export_kind == "wav":
                    target = Path(folder) / f"{export_stem}.wav"
                    self.player.render_wav(
                        prepared,
                        self.project.selected_soundfont_path,
                        target,
                        volume_gain=audio_gain,
                    )
                else:
                    target = Path(folder) / f"{export_stem}.mp3"
                    self.player.render_mp3(
                        prepared,
                        self.project.selected_soundfont_path,
                        target,
                        volume_gain=audio_gain,
                    )
                exported_paths.append(target)
            manifest = self._write_export_manifest(Path(folder), checked, export_kind, exported_paths)
            return {"ok": True, "label": label, "kind": export_kind, "count": len(exported_paths), "folder": str(folder), "manifest": str(manifest)}
        except Exception as exc:
            return {"ok": False, "label": label, "kind": export_kind, "error": str(exc), "folder": str(folder)}

    def _poll_export_job(self) -> None:
        if not self._export_busy:
            self.export_poll_timer.stop()
            return
        self._export_spinner_index = (self._export_spinner_index + 1) % len(self._spinner_frames)
        frame = self._spinner_frames[self._export_spinner_index]
        current = self.status_label.text().lstrip("".join(self._spinner_frames)).strip()
        if current:
            self.status_label.setText(f"{frame} {current}")
        if self._export_progress_dialog:
            base = self.playback_status_label.text().lstrip("".join(self._spinner_frames)).strip() or "Exporting…"
            self._export_progress_dialog.set_message(f"{frame} {base}")
        thread_done = self._export_thread is not None and not self._export_thread.is_alive()
        if thread_done and self._export_result is not None:
            self.export_poll_timer.stop()
            result = self._export_result
            self._export_result = None
            self._export_thread = None
            self._finish_export_job(result)

    def _finish_export_job(self, result: dict[str, Any]) -> None:
        dialog = self._export_progress_dialog
        self._export_progress_dialog = None
        if dialog is not None:
            dialog.close()
        self._export_busy = False
        label = str(result.get("label") or "Export")
        kind = str(result.get("kind") or "export")
        if result.get("ok"):
            count = int(result.get("count") or 0)
            self.status_label.setText(f"Exported {count} {label} file(s)")
            self.playback_status_label.setText(f"Exported {count} {kind.upper()} file(s)")
            manifest_path = result.get("manifest")
            self._offer_open_export_folder(Path(str(result.get("folder") or ".")), Path(str(manifest_path)) if manifest_path else None)
        else:
            self.status_label.setText(f"{label} export failed")
            QMessageBox.warning(self, f"{label} export failed", str(result.get("error") or "Unknown export error"))
        self._update_action_states()

    def _write_export_manifest(self, folder: Path, checked: list[dict[str, Any]], export_kind: str, exported_paths: list[Path]) -> Path:
        folder.mkdir(parents=True, exist_ok=True)
        manifest = folder / "midi_performer_export_manifest.txt"
        lines = [
            "MIDI Performer Export Manifest",
            f"Source MIDI: {Path(self.project.source_midi_path or 'MIDI').name}",
            f"Format: {export_kind.upper()}",
            "",
        ]
        by_id = {str(item.get("id") or "original"): path.name for item, path in zip(checked, exported_paths)}
        for item in checked:
            key = str(item.get("id") or "original")
            lines.append(f"{item.get('name', 'Original')}")
            if bool(item.get("best")):
                lines.append("  Best: yes")
            if bool(item.get("favorite")):
                lines.append("  Favorite: yes")
            if item.get("user_note"):
                lines.append(f"  Note: {item.get('user_note')}")
            lines.append(f"  File: {by_id.get(key, '')}")
            lines.append(f"  Saved sound: {self._take_row_display_text(item)}")
            lines.append(f"  Tempo: {int(item.get('tempo_percent', 100) or 100)}%")
            item_volume = self._take_volume_percent(item)
            lines.append(f"  Volume: {item_volume}%")
            lines.append(f"  Live/WAV/MP3 gain: {audio_gain_for_volume_percent(item_volume):.3f}")
            lines.append(f"  Instruments: {self._take_instrument_summary(item, include_unchanged=(key == 'original'), max_items=99)}")
            lines.append("")
        manifest.write_text("\n".join(lines), encoding="utf-8")
        return manifest

    def _offer_open_export_folder(self, folder: Path, manifest: Path | None = None) -> None:
        msg = QMessageBox(self)
        msg.setWindowTitle("Export complete")
        detail = f"Manifest: {manifest.name}" if manifest else ""
        msg.setText(f"Export complete.\n{detail}")
        open_button = msg.addButton("Open folder", QMessageBox.AcceptRole)
        msg.addButton("Close", QMessageBox.RejectRole)
        msg.exec()
        if msg.clickedButton() == open_button:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _update_action_states(self) -> None:
        source_loaded = bool(self.project.source_midi_path)
        export_busy = bool(getattr(self, "_export_busy", False))
        playable = bool(source_loaded and self._playback_ready and not self._playback_preparing and not export_busy)
        playing = self.playback_engine.is_playing()
        paused = self.playback_engine.is_paused()
        selected = self._selected_play_item() if playable else None
        selected_exists = bool(selected and selected.get("path"))
        redirect_to_audition = bool(playable and self._variation_dirty and self.selected_review_kind == "original")
        self.preview_btn.setEnabled(playable and selected_exists and not redirect_to_audition)
        self.stop_btn.setEnabled(playable and (playing or paused or int(self.project.playback_start_percent or 0) > 0))
        self.rewind_btn.setEnabled(playable)
        self.ff_btn.setEnabled(playable)
        self.position_slider.setEnabled(playable)
        self.volume_slider.setEnabled(playable)
        self.tempo_slider.setEnabled(playable)
        self.reset_volume_btn.setEnabled(playable)
        self.reset_tempo_btn.setEnabled(playable)
        self.create_preview_btn.setEnabled(playable and not playing)
        self.create_preview_btn.setObjectName("AuditionReadyButton" if self._variation_dirty and playable else "PrimaryButton")
        self.play_preview_btn.setEnabled(False)
        self.save_take_btn.setEnabled(False)
        self.mixer_btn.setEnabled(playable)
        self.delete_all_takes_btn.setEnabled(bool(self.project.takes) and not playing and playable)
        self.delete_unchecked_takes_btn.setEnabled(bool(self.project.takes) and not playing and playable)
        has_exportable = any(bool(item.get("path")) for item in ([self._original_take_row()] + list(self.project.takes or []))) if playable else False
        self.select_all_export_btn.setEnabled(has_exportable and not playing and playable)
        self.select_none_export_btn.setEnabled(bool(self.export_checked_keys) and not playing and playable)
        self.favorite_filter_btn.setEnabled(bool(self.project.takes) and playable)
        self.favorite_filter_btn.setText("Show all" if self.show_favorites_only else "★ only")
        self._refresh_takes_summary()
        self.export_best_btn.setEnabled(bool(self._best_take()) and not playing and playable)
        self.open_project_btn.setEnabled(not playing and not export_busy)
        self.save_project_btn.setEnabled(not playing and not export_busy)
        self.load_score_source_btn.setEnabled(not playing and not export_busy)
        self.load_reference_audio_btn.setEnabled(not playing and not export_busy and not getattr(self, "_audio_analysis_busy", False))
        self.clear_score_source_btn.setEnabled(bool(getattr(self.project, "score_source_path", None)) and not playing and not export_busy)
        self.analyze_score_btn.setEnabled(bool(getattr(self.project, "score_source_path", None)) and not playing and not export_busy)
        self.open_score_pdf_btn.setEnabled(bool((getattr(self.project, "score_source_format", None) or "").lower() == "pdf" and getattr(self.project, "score_source_path", None)) and not playing and not export_busy)
        score_loaded_for_controls = bool(getattr(self.project, "score_source_path", None))
        score_controls_enabled = score_loaded_for_controls and not playing and not export_busy
        self.score_dynamics_check.setEnabled(score_controls_enabled)
        self.score_guidance_strength_combo.setEnabled(self._score_guidance_available() and score_controls_enabled and bool(getattr(self.project, "score_dynamics_enabled", False)))
        self.score_guided_take_btn.setEnabled(False)
        audio_guide_available = self._reference_audio_guidance_available()
        has_alignment_details = bool(getattr(self.project, "score_analysis_details_summary", None) or getattr(self.project, "score_alignment_summary", None) or audio_guide_available)
        self.fix_alignment_btn.setEnabled(has_alignment_details and not playing and not export_busy)
        audio_controls_enabled = bool(getattr(self.project, "reference_audio_path", None)) and not playing and not export_busy and not getattr(self, "_audio_analysis_busy", False)
        self.clear_reference_audio_btn.setEnabled(audio_controls_enabled)
        self.audio_guidance_check.setEnabled(audio_controls_enabled and audio_guide_available)
        self.audio_guidance_strength_combo.setEnabled(audio_controls_enabled and audio_guide_available and bool(getattr(self.project, "reference_audio_guidance_enabled", False)))
        self._apply_score_progressive_disclosure()
        can_export = False
        for key, cb in self.take_checkboxes.items():
            item = self._take_by_key(key)
            if cb.isEnabled() and cb.isChecked() and item and item.get("path"):
                can_export = True
                break
        self.export_checked_btn.setEnabled(can_export and not playing and playable)
        self.export_wav_btn.setEnabled(can_export and not playing and playable)
        self.export_mp3_btn.setEnabled(can_export and not playing and playable)
        label = selected.get("name") if selected else "Original"
        self.transport_target_label.setText(f"Plays: {label}")
        if not playing and not paused:
            if export_busy:
                self.playback_status_label.setText("Exporting…")
            elif self._playback_preparing:
                self.playback_status_label.setText("Preparing playback…")
            elif source_loaded and not playable:
                self.playback_status_label.setText("Preparing before Play")
            else:
                self.playback_status_label.setText(self._compact_stopped_status(str(label), int(self.project.playback_start_percent or 0), self._selected_duration_seconds()))
        if playing:
            self.preview_btn.setText("Pause")
            self.preview_btn.setProperty("mode", "pause")
        elif paused:
            self.preview_btn.setText("Resume")
            self.preview_btn.setProperty("mode", "play")
        else:
            if not playable and source_loaded:
                self.preview_btn.setText("Preparing…")
                self.preview_btn.setToolTip("Playback is being prepared. Play will unlock when the MIDI is ready to start immediately.")
            elif redirect_to_audition:
                self.preview_btn.setText("Use row Play")
                self.preview_btn.setToolTip("Variation settings changed. Use the new numbered row in Takes to create and hear it.")
            else:
                self.preview_btn.setText(f"Play {label}")
                self.preview_btn.setToolTip("Play or pause the selected row in Takes.")
            self.preview_btn.setProperty("mode", "play")
        self.stop_btn.setProperty("playing", playing)
        for btn in [
            self.stop_btn, self.preview_btn, self.export_checked_btn, self.export_best_btn, self.export_wav_btn,
            self.export_mp3_btn, self.create_preview_btn, self.save_take_btn, self.mixer_btn,
            self.select_all_export_btn, self.select_none_export_btn, self.favorite_filter_btn,
            self.open_project_btn, self.save_project_btn, self.delete_unchecked_takes_btn,
            self.delete_all_takes_btn, self.analyze_score_btn, self.score_guided_take_btn, self.open_score_pdf_btn,
            self.score_omr_setup_btn, self.score_omr_help_btn,
        ]:
            btn.style().unpolish(btn)
            btn.style().polish(btn)
            btn.update()
        self._update_take_play_buttons()

    def _update_take_play_buttons(self) -> None:
        playable = bool(self.project.source_midi_path and self._playback_ready and not self._playback_preparing)
        playing = self.playback_engine.is_playing()
        paused = self.playback_engine.is_paused()
        active_id = self.playback_engine.version_key
        for key, btn in self.take_play_buttons.items():
            if not playable:
                btn.setEnabled(False)
            elif key == "pending":
                btn.setEnabled(True)
            else:
                item = self._take_by_key(key)
                btn.setEnabled(bool(item and item.get("path")))
            if playing and active_id == key:
                btn.setText("Pause")
                btn.setProperty("mode", "pause")
            elif paused and active_id == key:
                btn.setText("Resume")
                btn.setProperty("mode", "play")
            else:
                btn.setText("Play")
                btn.setProperty("mode", "play")
            btn.style().unpolish(btn)
            btn.style().polish(btn)
            btn.update()
        for key, btn in self.take_stop_buttons.items():
            btn.setEnabled((playing or paused) and active_id == key)

    def new_project(self) -> None:
        self.stop_playback(silent=True)
        self._invalidate_midi_cache()
        soundfont = self.project.selected_soundfont_path
        volume = self.project.master_volume
        tempo = self.project.playback_tempo_percent
        self.project = MidiPerformerProject()
        self.project.selected_soundfont_path = soundfont
        self.project.master_volume = normalize_volume_percent(volume)
        self.project.playback_tempo_percent = tempo
        self.project.output_directory = self.settings.get("last_output_directory")
        self.project.project_directory = self.settings.get("last_project_directory")
        self.project.current_project_path = None
        self.selected_review_kind = "original"
        self.selected_take_id = None
        self.export_checked_keys.clear()
        self._playback_mixer_contexts.clear()
        self._invalidate_playback_ready()
        self._variation_dirty = False
        self._last_audition_signature = None
        self._mixer_revision = 0
        self.volume_slider.setValue(normalize_volume_percent(volume))
        self.tempo_slider.setValue(int(tempo or 100))
        self._sync_score_guidance_widgets()
        self._refresh_all()
        self.status_label.setText("New project ready")

    def open_project(self) -> None:
        start = str(self.project.project_directory or self.settings.get("last_project_directory") or self._repo_root() / "projects")
        path, _ = QFileDialog.getOpenFileName(self, "Open MIDI Performer Project", start, "MIDI Performer Project (*.emiproj);;JSON Files (*.json);;All Files (*.*)")
        if not path:
            return
        self._load_project_path(path)

    def _load_project_path(self, path: str) -> None:
        try:
            self.stop_playback(silent=True)
            self._invalidate_midi_cache()
            self.project = MidiPerformerProject.load(path)
            self.project.current_project_path = str(Path(path))
            self.project.project_directory = str(Path(path).parent)
            self.settings.set("last_project_directory", str(Path(path).parent))
            self._remember_recent_path("recent_project_paths", str(Path(path)))
            self._sync_volume_widget_from_project()
            self.tempo_slider.setValue(int(self.project.playback_tempo_percent or 100))
            self._sync_score_guidance_widgets()
            self.selected_review_kind = "original"
            self.selected_take_id = None
            self.export_checked_keys.clear()
            self._playback_mixer_contexts.clear()
            self._invalidate_playback_ready()
            self._variation_dirty = False
            self._last_audition_signature = None
            self._mixer_revision = 0
            self._refresh_all()
            if self.project.source_midi_path:
                if not self._begin_playback_prepare(status_prefix=f"Opened project: {Path(path).name} · "):
                    self.status_label.setText(f"Opened project: {Path(path).name}")
            else:
                self.status_label.setText(f"Opened project: {Path(path).name}")
        except Exception as exc:
            QMessageBox.warning(self, "Open failed", str(exc))

    def _save_project_to_path(self, target: Path) -> None:
        if target.suffix.lower() != ".emiproj":
            target = target.with_suffix(".emiproj")
        self.project.project_directory = str(target.parent)
        self.project.current_project_path = str(target)
        self.settings.set("last_project_directory", str(target.parent))
        self._remember_recent_path("recent_project_paths", str(target))
        target.parent.mkdir(parents=True, exist_ok=True)
        self.project.save(target)
        self._refresh_project_label()
        self.status_label.setText(f"Saved project: {target.name}")

    def save_project(self) -> None:
        current = getattr(self.project, "current_project_path", None)
        if current:
            try:
                self._save_project_to_path(Path(current))
            except Exception as exc:
                QMessageBox.warning(self, "Save failed", str(exc))
            return
        self.save_project_as()

    def save_project_as(self) -> None:
        start = str(self.project.project_directory or self.settings.get("last_project_directory") or self._repo_root() / "projects")
        default = f"{Path(self.project.source_midi_path or 'midi_performer').stem}.emiproj"
        path, _ = QFileDialog.getSaveFileName(self, "Save MIDI Performer Project", str(Path(start) / default), "MIDI Performer Project (*.emiproj);;JSON Files (*.json);;All Files (*.*)")
        if not path:
            return
        try:
            self._save_project_to_path(Path(path))
        except Exception as exc:
            QMessageBox.warning(self, "Save failed", str(exc))

    def _setup_status_report(self) -> list[dict[str, Any]]:
        midi_path = self.project.source_midi_path
        sf_path = self.project.selected_soundfont_path
        deps = check_dependencies(self._repo_root(), self.settings.get("audiveris_path"), self.settings.get("fluidsynth_path"), self.settings.get("last_soundfont_path"), self.settings.get("last_soundfont_directory"))
        fs_check = deps["fluidsynth"]
        ffmpeg_check = deps["ffmpeg"]
        omr_check = deps["audiveris"]
        sf_check = deps["soundfont"]
        if not sf_path and sf_check.found:
            sf_path = sf_check.path
        fluidsynth = fs_check.path if fs_check.found else None
        ffmpeg = ffmpeg_check.path if ffmpeg_check.found else None
        source_ready = bool(midi_path and Path(midi_path).exists())
        score_path = getattr(self.project, "score_source_path", None)
        audio_path = getattr(self.project, "reference_audio_path", None)
        score_ready = bool(score_path and Path(score_path).exists())
        audio_ready = bool(audio_path and Path(audio_path).exists())
        sf_ready = bool(sf_path and Path(sf_path).exists())
        playback_ready = bool(source_ready and sf_ready and fluidsynth and self._playback_ready and not self._playback_preparing)
        audio_driver = str(self.settings.get("audio_driver", "default") or "default")
        audio_verified = bool(self.settings.get("audio_driver_verified", False))
        wav_ready = bool(source_ready and sf_ready and fluidsynth)
        mp3_ready = bool(wav_ready and ffmpeg)
        score_format = (getattr(self.project, "score_source_format", None) or "").lower()
        score_needs_omr = bool(score_ready and score_format == "pdf" and not getattr(self.project, "score_guide_available", False) and not getattr(self.project, "score_omr_dynamic_sequence", []))

        score_detail = "Optional: attach a matching PDF score for score-guided takes."
        if score_ready:
            score_detail = str(
                (getattr(self.project, "score_source_format", None) or "Score")
                + " · "
                + (getattr(self.project, "score_intent_summary", None) or getattr(self.project, "score_source_summary", None) or Path(score_path).name)
                + ((" · " + getattr(self.project, "score_alignment_summary", None)) if getattr(self.project, "score_alignment_summary", None) else "")
            )

        if omr_check.found:
            omr_state = "ready" if omr_check.confirmed else "attention"
            omr_detail = (omr_check.detail or omr_check.path or "Audiveris found")
        elif score_needs_omr:
            omr_state = "attention"
            omr_detail = "Needed for this PDF: click Install Audiveris here, or choose Audiveris beside Analyze Score."
        else:
            omr_state = "optional"
            omr_detail = "Optional until a scanned/image-only PDF needs full note recognition."

        fs_state = "ready" if fs_check.found else "attention"
        fs_label = "Installed" if fs_check.found else "Needs setup"
        ffmpeg_state = "ready" if ffmpeg_check.found and ffmpeg_check.confirmed else ("attention" if wav_ready and not ffmpeg_check.found else "optional")
        ffmpeg_label = "Confirmed" if ffmpeg_check.found and ffmpeg_check.confirmed else ("Found" if ffmpeg_check.found else ("Needs setup" if wav_ready else "Optional"))

        return [
            {
                "name": "MIDI source",
                "ready": source_ready,
                "state": "ready" if source_ready else "waiting",
                "label": "Ready" if source_ready else "Waiting",
                "detail": str(Path(midi_path).name if source_ready else "Open a MIDI file when you are ready to play/export."),
            },
            {
                "name": "Instrument Library",
                "ready": sf_ready,
                "state": "ready" if sf_ready else "attention",
                "label": "Ready" if sf_ready else "Needs setup",
                "detail": str((sf_check.detail if sf_check.found else "Choose or assign an existing .sf2 or .sf3 instrument library in Settings > Playback and Score Components.")),
            },
            {
                "name": "Volume chain",
                "ready": True,
                "state": "ready",
                "label": "Audited",
                "detail": volume_chain_summary(self._master_volume_percent(), self.project.track_volume_overrides),
            },
            {
                "name": "Score source",
                "ready": score_ready,
                "state": "ready" if score_ready else "optional",
                "label": "Ready" if score_ready else "Optional",
                "detail": score_detail,
            },
            {
                "name": "Audiveris OMR",
                "ready": bool(omr_check.found),
                "state": omr_state,
                "label": "Installed" if omr_check.found else ("Needs setup" if omr_state == "attention" else "Optional"),
                "detail": omr_detail,
            },
            {
                "name": "Reference performance",
                "ready": audio_ready,
                "state": "ready" if audio_ready else "optional",
                "label": "Ready" if audio_ready else "Optional",
                "detail": str((Path(audio_path).name + (" · " + (getattr(self.project, "reference_audio_analysis_summary", None) or getattr(self.project, "reference_audio_summary", None) or "attached"))) if audio_ready else "Optional: attach a local WAV/MP3 reference performance. WAV loudness contour is analyzed now; MIDI mimic comes later."),
            },
            {
                "name": "FluidSynth",
                "ready": bool(fs_check.found),
                "state": fs_state,
                "label": fs_label,
                "detail": (fs_check.detail + " Executable presence does not prove audible output.") if fs_check.found else "FluidSynth not found. Choose its existing location in Settings > File Locations.",
            },
            {
                "name": "Windows audio route",
                "ready": bool(audio_verified and fs_check.found),
                "state": "ready" if audio_verified and fs_check.found else "attention",
                "label": "Verified" if audio_verified and fs_check.found else "Test needed",
                "detail": (f"Audible test confirmed with driver: {audio_driver}." if audio_verified else f"Selected driver: {audio_driver}. Click Test Playback to play a direct synth chord."),
            },
            {
                "name": "Playback data gate",
                "ready": playback_ready,
                "state": "ready" if playback_ready else "waiting",
                "label": "Prepared" if playback_ready else "Waiting",
                "detail": "MIDI timeline prepared; a fresh Windows audio process opens when Play is pressed." if playback_ready else ("Still preparing MIDI data." if self._playback_preparing else "Load MIDI + instrument library, then wait for timeline preparation."),
            },
            {
                "name": "MIDI export",
                "ready": source_ready,
                "state": "ready" if source_ready else "waiting",
                "label": "Ready" if source_ready else "Waiting",
                "detail": "Ready for checked rows." if source_ready else "Waiting for a loaded MIDI/take.",
            },
            {
                "name": "WAV export",
                "ready": wav_ready,
                "state": "ready" if wav_ready else ("attention" if source_ready and sf_ready and not fluidsynth else "waiting"),
                "label": "Ready" if wav_ready else ("Needs setup" if source_ready and sf_ready and not fluidsynth else "Waiting"),
                "detail": "Ready. Start-click cleanup is applied after rendering." if wav_ready else "Waiting for MIDI, instrument library, and FluidSynth.",
            },
            {
                "name": "FFmpeg / MP3 export",
                "ready": mp3_ready,
                "state": "ready" if mp3_ready else ffmpeg_state,
                "label": "Ready" if mp3_ready else ffmpeg_label,
                "detail": ("Ready with FFmpeg: " + str(ffmpeg)) if mp3_ready else (ffmpeg_check.detail if ffmpeg_check.found else "FFmpeg not confirmed. MP3 export needs ffmpeg.exe in vendor\\ffmpeg or on PATH."),
            },
        ]

    def _setup_status_summary(self, report: list[dict[str, Any]]) -> str:
        ready = {str(item.get("name")): bool(item.get("ready")) for item in report}
        pieces = []
        if ready.get("MIDI export"):
            pieces.append("MIDI export ready")
        if ready.get("WAV export"):
            pieces.append("WAV export ready")
        if ready.get("FFmpeg / MP3 export"):
            pieces.append("MP3 export ready")
        if not pieces:
            return "Open a MIDI and choose an instrument library before export testing."
        return " · ".join(pieces)

    def _offer_first_run_audio_setup(self) -> None:
        """Open an explicit first-run setup when playback is incomplete."""
        deps = check_dependencies(
            self._repo_root(),
            self.settings.get("audiveris_path"),
            self.settings.get("fluidsynth_path"),
            self.settings.get("last_soundfont_path"),
            self.settings.get("last_soundfont_directory"),
        )
        if deps["fluidsynth"].confirmed and deps["soundfont"].confirmed:
            return
        dialog = FirstRunSetupDialog(self)
        dialog.setStyleSheet(self._stylesheet())
        dialog.exec()

    def show_setup_status(self) -> None:
        dialog = SetupStatusDialog(self)
        dialog.setStyleSheet(self._stylesheet())
        dialog.exec()

    def run_audio_diagnostics(self, dialog_parent: QWidget | None = None) -> None:
        """Find a genuinely audible FluidSynth route on the target Windows PC."""
        owner = dialog_parent or self
        deps = check_dependencies(
            self._repo_root(),
            self.settings.get("audiveris_path"),
            self.settings.get("fluidsynth_path"),
            self.settings.get("last_soundfont_path"),
            self.settings.get("last_soundfont_directory"),
        )
        fs_check = deps["fluidsynth"]
        sf_check = deps["soundfont"]
        if not fs_check.found or not sf_check.confirmed:
            missing = []
            if not fs_check.found:
                missing.append("FluidSynth")
            if not sf_check.confirmed:
                missing.append("an instrument library")
            answer = QMessageBox.question(
                owner,
                "Audio setup required",
                "MIDI audio cannot be tested until " + " and ".join(missing) + " are ready.\n\nOpen the built-in audio setup now?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if answer == QMessageBox.Yes:
                if isinstance(owner, QDialog):
                    owner.accept()
                self.show_settings()
            return
        if sf_check.path:
            self.project.selected_soundfont_path = sf_check.path
        soundfont = str(self.project.selected_soundfont_path or "")
        if not soundfont or not Path(soundfont).exists():
            QMessageBox.warning(owner, "Playback Test", "The selected instrument library cannot be found.")
            return

        self.stop_playback(silent=True)
        backend = self.playback_engine.backend
        candidate_getter = getattr(backend, "available_driver_candidates", None)
        candidates = candidate_getter() if callable(candidate_getter) else [str(self.settings.get("audio_driver", "default") or "default")]
        failures: list[str] = []

        for driver in candidates:
            driver = str(driver or "default")
            self.status_label.setText(f"Testing Windows audio driver: {driver}…")
            QApplication.processEvents()
            try:
                result = self.playback_engine.test_audio_tone(
                    soundfont,
                    volume_gain=max(0.65, self._volume_gain()),
                    driver=driver,
                )
            except Exception as exc:
                failures.append(f"{driver}: {exc}")
                try:
                    backend.close()
                except Exception:
                    pass
                continue

            heard = QMessageBox.question(
                owner,
                "Playback Test",
                f"A short piano chord was played using the {driver} audio route.\n\nDid you hear it?\n\n"
                f"FluidSynth process: {'running' if result.get('running') else 'stopped'} · "
                f"commands sent: {result.get('commands', 0)} · note-ons: {result.get('note_on', 0)}",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            try:
                backend.close()
            except Exception:
                pass
            if heard == QMessageBox.Yes:
                self.settings.set("audio_driver", driver)
                self.settings.set("audio_driver_verified", True)
                self.playback_engine.set_audio_driver(driver)
                self.status_label.setText(f"Audio route verified: {driver}")
                if self.project.source_midi_path:
                    self._begin_playback_prepare(status_prefix=f"Audio verified ({driver}) · ")
                QMessageBox.information(
                    self,
                    "Audio route verified",
                    f"MIDI Performer will now use the Windows audio driver '{driver}'. Normal Play starts a fresh FluidSynth process on this route.",
                )
                return
            failures.append(f"{driver}: process ran and commands were sent, but no chord was heard")

        self.settings.set("audio_driver_verified", False)
        diagnostics = self.playback_engine.audio_diagnostics()
        log_path = str(diagnostics.get("log_path") or (self._repo_root() / "logs" / "audio_diagnostics.log"))
        self.status_label.setText("No audible FluidSynth route was confirmed")
        detail = "\n".join(failures[-6:]) or "No driver completed the test."
        QMessageBox.warning(
            owner,
            "Audio route not confirmed",
            "MIDI Performer did not confirm an audible FluidSynth route.\n\n"
            f"Results:\n{detail}\n\nDiagnostic log:\n{log_path}",
        )

    def show_keyboard_shortcuts(self) -> None:
        QMessageBox.information(
            self,
            "Keyboard Shortcuts",
            "Open MIDI: Ctrl+O\n"
            "New Project: Ctrl+N\n"
            "Open Project: Ctrl+Shift+O\n"
            "Save Project: Ctrl+S\n"
            "Save Project As: Ctrl+Shift+S\n"
            "Export checked: Ctrl+E\n"
            "Export Best: Ctrl+Shift+E\n"
            "Load PDF/Score Source: Ctrl+L\n"
            "Load Reference Audio: Ctrl+R\n"
            "Play/Pause selected row: Space\n"
            "Stop: Esc\n"
            "Rewind: Home\n"
            "Setup / Export Status: Ctrl+,"
        )

    def show_instrument_libraries(self) -> None:
        dialog = InstrumentLibraryManagerDialog(self)
        dialog.setStyleSheet(self._stylesheet())
        dialog.exec()

    def show_settings(self) -> None:
        dialog = SettingsDialog(self)
        dialog.setStyleSheet(self._stylesheet())
        dialog.exec()
        self._refresh_all()

    def show_about(self) -> None:
        QMessageBox.information(
            self,
            "About MIDI Performer",
            f"{APP_NAME}\n"
            f"Version {APP_VERSION_DISPLAY}\n\n"
            f"{TAGLINE}\n"
            f"{GUIDING_PRINCIPLE}\n\n"
            "A desktop MIDI performance and interpretation tool. Load MIDI, choose an instrument library (SoundFont), preserve the Original, build numbered Takes, compare them, save projects, and export selected results.\n\n"
            "Score and reference-audio sources provide guidance only after successful analysis and when their Use controls are enabled."
        )

    def closeEvent(self, event) -> None:  # type: ignore[override]
        try:
            self.prepare_poll_timer.stop()
            self.spinner_timer.stop()
            self.playback_engine.close()
        finally:
            super().closeEvent(event)

    def _stylesheet(self) -> str:
        return """
        QMenuBar { background: #f3f5f9; color: #172033; padding: 2px 4px; }
        QMenuBar::item { padding: 4px 10px; background: transparent; }
        QMenuBar::item:selected { background: #e8eef8; border-radius: 4px; }
        QScrollArea#MainScroll { background: #f3f5f9; border: none; }
        QScrollArea#TakesScroll { background: #ffffff; border: 1px solid #e4eaf3; border-radius: 8px; }
        QScrollArea#MixerScroll { background: #ffffff; border: 1px solid #d9dfeb; border-radius: 7px; }
        QScrollArea#MixerScroll > QWidget > QWidget { background: #ffffff; color: #172033; }
        QWidget#MixerStripRow { background: #ffffff; color: #172033; }
        QScrollArea#TakesScroll > QWidget > QWidget { background: #ffffff; color: #172033; }
        QWidget#TakesContainer { background: #ffffff; color: #172033; }
        QWidget#AppRoot { background: #f3f5f9; color: #172033; font-family: Segoe UI, Arial, sans-serif; font-size: 10pt; }
        QFrame#Header, QFrame#Card { background: #ffffff; border: 1px solid #d9dfeb; border-radius: 10px; }
        QFrame#InnerPanel { background: #f8fbff; border: 1px solid #d7e5f8; border-radius: 8px; }
        QFrame#AudioTestPanel { background: #ffffff; border: 1px solid #b8cae3; border-radius: 8px; }
        QFrame#ChoiceGroup { background: #f8fbff; border: 2px solid #7898c4; border-radius: 8px; }
        QFrame#ControlGroup { background: #f8fbff; border: 2px solid #7898c4; border-radius: 8px; }
        QFrame#Header { background: #fbfcff; }
        QFrame#TrackStrip { background: #ffffff; border: 1px solid #d9dfeb; border-radius: 7px; }
        QFrame#Separator { color: #e2e7f0; }
        QLabel#Title { font-size: 17px; font-weight: 800; color: #14213d; }
        QLabel#Subtitle { color: #6b7280; font-weight: 600; }
        QLabel#SectionTitle { font-size: 12px; font-weight: 800; color: #111827; }
        QLabel#TableHeader { font-size: 10px; font-weight: 800; color: #475467; }
        QLabel#DialogText { color: #111827; font-size: 9pt; }
        QLabel#MixerReference { background: #eef4ff; border: 1px solid #cddbf3; border-radius: 6px; padding: 3px 6px; color: #344054; font-size: 9pt; }
        QLabel#SourceReference { background: #f8fbff; border: 1px solid #d7e5f8; border-radius: 6px; padding: 3px 6px; color: #344054; font-size: 9pt; }
        QLabel#ScoreAnalysisInfo { background: #f7fbff; border: 1px solid #d7e5f8; border-radius: 7px; padding: 4px 7px; color: #334155; font-size: 9pt; }
        QLabel#ScoreAnalysisReady { background: #edf9f0; border: 1px solid #78ca92; border-radius: 7px; padding: 4px 7px; color: #0f6b2c; font-size: 9pt; font-weight: 650; }
        QLabel#ScoreAnalysisWarn { background: #fff8e7; border: 1px solid #f2cf82; border-radius: 7px; padding: 4px 7px; color: #6f4e00; font-size: 9pt; }
        QLabel#ScoreAnalysisBusy { background: #eaf3ff; border: 2px solid #4f8edb; border-radius: 7px; padding: 4px 7px; color: #123f70; font-size: 9pt; font-weight: 750; }
        QProgressBar#ScoreAnalysisProgress { background: #e9f2ff; border: 1px solid #75a9e6; border-radius: 5px; }
        QProgressBar#ScoreAnalysisProgress::chunk { background: #2f5f9f; border-radius: 5px; }
        QLabel#TrackMeta { color: #667085; font-size: 8pt; padding: 0px 2px; }
        QLabel#ControlLabel { color: #344054; font-weight: 700; min-width: 68px; }
        QLabel#ControlLabelCompact { color: #344054; font-weight: 700; min-width: 52px; }
        QLabel#ControlLabelTop { color: #344054; font-weight: 800; font-size: 10px; }
        QLabel#CompactStatus { color: #5f4700; font-weight: 700; padding: 1px 2px; min-width: 44px; }
        QLabel#InlineStatus { background: #fffdf5; border: 1px solid #ead8a6; border-radius: 6px; padding: 4px 7px; color: #5f4700; font-weight: 700; }
        QLabel#MiniTitle { font-size: 11px; font-weight: 800; color: #374151; }
        QLabel#LoadedName { background: #eef4ff; border: 1px solid #cddbf3; border-radius: 5px; padding: 2px 5px; color: #183e73; font-weight: 700; font-size: 9pt; }
        QLabel#LoadedNameActive, QLabel#SelectedInstrument { background: #fff7e6; border: 2px solid #c47f00; border-radius: 6px; padding: 3px 6px; color: #4f2d00; font-weight: 900; font-size: 9pt; }
        QPushButton#InstrumentButton { background: #eef4ff; border: 1px solid #9bb8df; border-radius: 7px; padding: 5px 6px; color: #183e73; font-weight: 800; font-size: 9pt; text-align: left; }
        QPushButton#InstrumentButton:hover { background: #dfeaff; border-color: #2f5f9f; }
        QPushButton#InstrumentButtonActive { background: #fff7e6; border: 2px solid #c47f00; border-radius: 7px; padding: 5px 6px; color: #4f2d00; font-weight: 900; font-size: 9pt; text-align: left; }
        QPushButton#InstrumentButtonActive:hover { background: #ffedc7; }
        QLabel#SubtleText { color: #667085; }
        QLabel#FeedbackText { background: #f7fbff; border: 1px solid #d7e5f8; border-radius: 7px; padding: 4px 6px; color: #334155; }
        QLabel#MixerSummary { background: #f8fafc; border: 1px solid #d7e5f8; border-radius: 7px; padding: 4px 6px; color: #172033; font-size: 9pt; max-height: 118px; }
        QLabel#EmptyState { background: #f8fafc; border: 1px dashed #cbd5e1; border-radius: 8px; padding: 10px; color: #64748b; }
        QLabel#ValueLabel { min-width: 40px; font-weight: 700; color: #374151; }
        QLabel#ValueLabelMini { min-width: 28px; font-weight: 700; color: #374151; font-size: 9pt; }
        QLabel#ValueLabelWide { min-width: 70px; font-weight: 700; color: #374151; }
        QLabel#VersionStatus { background: #f1f4f8; border-radius: 6px; padding: 3px 6px; color: #374151; }
        QLabel#PreparingStatus { background: #fff8e6; border: 1px solid #f0d28a; border-radius: 7px; padding: 3px 7px; color: #5f4700; font-weight: 800; }
        QLabel#PreparingState { background: #fff8e6; border: 1px dashed #d6a437; border-radius: 8px; padding: 10px; color: #5f4700; font-weight: 800; }
        QLabel#StatusLine { background: #ffffff; border: 1px solid #d9dfeb; border-radius: 8px; padding: 4px 8px; color: #374151; }
        QLabel#ReadyPill { background: #e9f8ef; border: 1px solid #93d4a9; border-radius: 8px; padding: 3px 7px; color: #0f6b2b; font-weight: 900; font-size: 9pt; }
        QLabel#WaitingPill { background: #eef4ff; border: 1px solid #c9d7ee; border-radius: 8px; padding: 3px 7px; color: #25466f; font-weight: 900; font-size: 9pt; }
        QLabel#OptionalPill { background: #f4f6f8; border: 1px solid #d2d8e1; border-radius: 8px; padding: 3px 7px; color: #475467; font-weight: 900; font-size: 9pt; }
        QLabel#WarningPill { background: #fff8e6; border: 1px solid #e7c168; border-radius: 8px; padding: 3px 7px; color: #7a4d00; font-weight: 900; font-size: 9pt; }
        QProgressBar { background: #f1f4f8; border: 1px solid #d6dde9; border-radius: 4px; }
        QProgressBar::chunk { background: #2f5f9f; border-radius: 4px; }
        QPushButton { border: 1px solid #c7d0df; border-radius: 7px; padding: 2px 7px; background: #ffffff; color: #172033; font-weight: 700; }
        QPushButton:hover { background: #f0f5ff; }
        QPushButton:pressed { background: #e2ecff; }
        QPushButton:checked { background: #e6f0ff; border-color: #2f5f9f; color: #173d72; }
        QPushButton:disabled { background: #edf0f5; color: #9aa3b2; border-color: #dfe5ee; }
        QPushButton#PrimaryButton { background: #2f5f9f; color: white; border-color: #2f5f9f; }
        QPushButton#AuditionReadyButton { background: #138a36; color: white; border-color: #138a36; }
        QPushButton#ToolButton { background: #f8fafc; color: #334155; border-color: #94a3b8; }
        QPushButton#CompactToolButton { background: #f8fafc; color: #334155; border-color: #94a3b8; font-size: 9pt; padding: 2px 6px; min-height: 20px; }
        QPushButton#PlayButton { background: #138a36; color: white; border-color: #138a36; }
        QPushButton#PlayButton[mode="pause"] { background: #e5e7eb; color: #374151; border-color: #cbd5e1; }
        QPushButton#PlayButton:disabled { background: #edf0f5; color: #9aa3b2; border-color: #dfe5ee; }
        QPushButton#StopButton { background: #b42318; color: white; border-color: #b42318; }
        QPushButton#StopButton:disabled { background: #edf0f5; color: #9aa3b2; border-color: #dfe5ee; }
        QPushButton#ExportButton { background: #f7f1ff; border-color: #d8c3f4; color: #59408a; }
        QPushButton#ChoicePill { font-size: 8pt; padding: 1px 4px; }
        QPushButton#MiniButton { font-size: 9px; padding: 2px 4px; min-width: 44px; max-width: 52px; }
        QDialog { background: #f3f5f9; color: #172033; font-family: Segoe UI, Arial, sans-serif; font-size: 9pt; }
        QDialog QPushButton { min-height: 22px; font-size: 9pt; padding: 2px 7px; }
        QComboBox, QLineEdit { border: 1px solid #aab8d0; border-radius: 6px; padding: 2px 6px; background: #f8fbff; color: #111827; min-height: 19px; }
        QCheckBox { color: #334155; font-weight: 700; spacing: 5px; }
        QCheckBox#GuideCheck { background: #f8fafc; border: 1px solid #c7d0df; border-radius: 7px; padding: 3px 6px; min-height: 20px; }
        QCheckBox#GuideCheck:hover { background: #f0f5ff; border-color: #94a3b8; }
        QCheckBox#GuideCheck:checked { background: #e6f0ff; border-color: #2f5f9f; color: #173d72; }
        QCheckBox#GuideCheck:disabled { background: #edf0f5; border-color: #dfe5ee; color: #9aa3b2; }
        QComboBox QAbstractItemView { background: #ffffff; color: #111827; selection-background-color: #e6f0ff; selection-color: #111827; border: 1px solid #aab8d0; outline: none; }
        QListWidget { background: white; color: #111827; border: 1px solid #aab8d0; border-radius: 6px; }
        QListWidget::item { padding: 5px 7px; }
        QListWidget::item:selected { background: #fff1cc; color: #111827; border-left: 4px solid #c47f00; font-weight: 900; }
        QSlider::groove:horizontal { height: 6px; background: #dde4f0; border-radius: 3px; }
        QSlider::handle:horizontal { background: #2f5f9f; border: 2px solid white; width: 14px; margin: -5px 0; border-radius: 7px; }
        QSlider::groove:vertical { width: 6px; background: #dde4f0; border-radius: 3px; }
        QSlider::handle:vertical { background: #2f5f9f; border: 2px solid white; height: 14px; margin: 0 -5px; border-radius: 7px; }
        """


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
