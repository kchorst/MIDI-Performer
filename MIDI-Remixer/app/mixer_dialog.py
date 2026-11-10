from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout,
    QHeaderView, QLabel, QMessageBox, QPushButton, QSpinBox, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QGridLayout, QApplication,
)

from core.operation_diagnostics import operation_failure, write_diagnostic
from core.midi_mixer import TrackMix, apply_take_mix, general_midi_programs, inspect_take_tracks, recommended_mix


class InstrumentationMixerDialog(QDialog):
    auditionRequested = Signal(str, str)
    pauseRequested = Signal()
    stopRequested = Signal()
    mixedTakeCreated = Signal(str)

    def __init__(self, take_path: str, output_directory: str | Path, parent=None) -> None:
        super().__init__(parent)
        self.take_path = str(take_path)
        self.output_directory = Path(output_directory)
        self.track_info = inspect_take_tracks(self.take_path)
        self._programs = general_midi_programs()
        self.setWindowTitle("Instrumentation & Dynamics Mixer")
        self.resize(1220, 680)
        self.setMinimumSize(820, 500)
        self._build_ui()
        self._load_recommended()
        self._fit_to_screen()
        self.set_transport_state("stopped")

    def _fit_to_screen(self) -> None:
        screen = self.parentWidget().screen() if self.parentWidget() else QApplication.primaryScreen()
        if not screen:
            return
        available = screen.availableGeometry()
        self.resize(min(self.width(), max(820, available.width() - 36)), min(self.height(), max(500, available.height() - 54)))
        if not available.intersects(self.frameGeometry()):
            self.move(available.center() - self.rect().center())

    def set_transport_state(self, state: str) -> None:
        active = state in {"playing", "paused"}
        self.stop_button.setProperty("transportState", "active" if active else "idle")
        self.stop_button.setEnabled(active)
        self.pause_button.setEnabled(active)
        for button in (self.stop_button, self.pause_button):
            button.style().unpolish(button)
            button.style().polish(button)
            button.update()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        heading = QHBoxLayout()
        title = QLabel("Instrumentation & Dynamics Mixer")
        title.setStyleSheet("font-size:18px; font-weight:700;")
        self.source_label = QLabel(Path(self.take_path).name)
        self.source_label.setToolTip(self.take_path)
        heading.addWidget(title); heading.addWidget(self.source_label,1)
        root.addLayout(heading)

        explanation = QLabel(
            "Automatic balancing is a starting point. Use instrument, volume, expression, pan, velocity, octave, mute, and solo controls to finish the selected Take. "
            "Polyphonic piano-reduction parts are flagged before assignment to nominal solo instruments."
        )
        explanation.setWordWrap(True)
        root.addWidget(explanation)

        columns = ["Part", "Role / song", "Instrument", "Volume", "Expression", "Pan", "Velocity", "Octave", "Mute", "Solo", "Instrumentation check"]
        self.table = QTableWidget(len(self.track_info), len(columns))
        self.table.setHorizontalHeaderLabels(columns)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        header = self.table.horizontalHeader(); header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        widths = [190,170,220,80,85,70,80,70,55,55,330]
        for i,w in enumerate(widths): self.table.setColumnWidth(i,w)
        root.addWidget(self.table,1)

        controls = QGridLayout()
        controls.setHorizontalSpacing(6)
        controls.setVerticalSpacing(5)
        auto = QPushButton("Auto-balance")
        reset = QPushButton("Reset Neutral")
        audition_original = QPushButton("Play Original Take")
        audition_original.setObjectName("PlayButton")
        audition_mix = QPushButton("Apply & Play Mix")
        audition_mix.setObjectName("PlayButton")
        self.pause_button = QPushButton("Pause / Resume")
        self.pause_button.setObjectName("PauseButton")
        self.stop_button = QPushButton("Stop")
        self.stop_button.setObjectName("StopButton")
        save = QPushButton("Save Mixed MIDI…")
        close = QPushButton("Close")
        auto.clicked.connect(self._load_recommended)
        reset.clicked.connect(self._load_neutral)
        audition_original.clicked.connect(lambda: self.auditionRequested.emit(self.take_path,"Original generated Take"))
        audition_mix.clicked.connect(self._apply_and_play)
        self.pause_button.clicked.connect(self.pauseRequested.emit)
        self.stop_button.clicked.connect(self.stopRequested.emit)
        save.clicked.connect(self._save_as)
        close.clicked.connect(self.close)
        controls.addWidget(auto, 0, 0)
        controls.addWidget(reset, 0, 1)
        controls.setColumnStretch(2, 1)
        controls.addWidget(audition_original, 0, 3)
        controls.addWidget(audition_mix, 0, 4)
        controls.addWidget(self.pause_button, 1, 3)
        controls.addWidget(self.stop_button, 1, 4)
        controls.addWidget(save, 1, 5)
        controls.addWidget(close, 1, 6)
        root.addLayout(controls)

    def _program_combo(self, selected: int, percussion: bool) -> QComboBox:
        combo=QComboBox()
        if percussion:
            combo.addItem("Percussion channel (fixed)",selected); combo.setEnabled(False)
        else:
            for number,name in self._programs: combo.addItem(f"{number+1:03d} — {name}",number)
            combo.setCurrentIndex(max(0,combo.findData(selected)))
        return combo

    @staticmethod
    def _spin(minimum:int, maximum:int, value:int, suffix:str="") -> QSpinBox:
        spin=QSpinBox(); spin.setRange(minimum,maximum); spin.setValue(value); spin.setSuffix(suffix); return spin

    def _populate(self, mixes: list[TrackMix]) -> None:
        by_index={m.track_index:m for m in mixes}
        for row,info in enumerate(self.track_info):
            mix=by_index.get(info.track_index,TrackMix(info.track_index,program=info.program))
            part=QTableWidgetItem(info.name); part.setData(Qt.ItemDataRole.UserRole,info.track_index); part.setToolTip(f"Range {info.min_note}–{info.max_note}; maximum polyphony {info.max_polyphony}")
            self.table.setItem(row,0,part)
            self.table.setItem(row,1,QTableWidgetItem(f"{info.likely_role} · {info.source_hint}"))
            self.table.setCellWidget(row,2,self._program_combo(mix.program if mix.program is not None else info.program,9 in info.channels))
            self.table.setCellWidget(row,3,self._spin(0,150,mix.volume,"%"))
            self.table.setCellWidget(row,4,self._spin(0,150,mix.expression,"%"))
            self.table.setCellWidget(row,5,self._spin(0,127,mix.pan))
            self.table.setCellWidget(row,6,self._spin(0,200,mix.velocity_percent,"%"))
            self.table.setCellWidget(row,7,self._spin(-4,4,mix.octave_shift))
            mute=QCheckBox(); mute.setChecked(mix.mute); self.table.setCellWidget(row,8,mute)
            solo=QCheckBox(); solo.setChecked(mix.solo); self.table.setCellWidget(row,9,solo)
            warning=QTableWidgetItem(info.instrumentation_warning); warning.setToolTip(info.instrumentation_warning); self.table.setItem(row,10,warning)

    def _load_recommended(self) -> None:
        self._populate(recommended_mix(self.track_info))

    def _load_neutral(self) -> None:
        self._populate([TrackMix(item.track_index,program=item.program) for item in self.track_info])

    def _current_mixes(self) -> list[TrackMix]:
        result=[]
        for row,info in enumerate(self.track_info):
            program=self.table.cellWidget(row,2); volume=self.table.cellWidget(row,3); expression=self.table.cellWidget(row,4); pan=self.table.cellWidget(row,5); velocity=self.table.cellWidget(row,6); octave=self.table.cellWidget(row,7); mute=self.table.cellWidget(row,8); solo=self.table.cellWidget(row,9)
            result.append(TrackMix(
                track_index=info.track_index,
                program=int(program.currentData()) if isinstance(program,QComboBox) and program.currentData() is not None else info.program,
                volume=volume.value(), expression=expression.value(), pan=pan.value(), velocity_percent=velocity.value(), octave_shift=octave.value(),
                mute=mute.isChecked(), solo=solo.isChecked(),
            ))
        return result

    def _default_mixed_path(self) -> Path:
        source=Path(self.take_path)
        return self.output_directory / f"{source.stem}-mixed.mid"

    def _apply_mix(self, target: Path) -> Path:
        return apply_take_mix(self.take_path, target, self._current_mixes())

    def _report_mix_error(self, operation: str, target: Path, exc: BaseException) -> None:
        parent = self.parentWidget()
        log_root = getattr(getattr(parent, "paths", None), "logs", self.output_directory)
        log_path = Path(log_root) / "mixer-errors.log"
        write_diagnostic(log_path, operation, exc, source=self.take_path, target=target)
        QMessageBox.critical(
            self,
            "Mixer",
            operation_failure(
                operation,
                exc,
                path=target,
                corrective_action="Review the selected track settings and confirm the Take MIDI still exists, then try again.",
            ),
        )

    def _apply_and_play(self) -> None:
        target = self._default_mixed_path()
        try:
            target = self._apply_mix(target)
        except (OSError, ValueError, RuntimeError, TypeError) as exc:
            self._report_mix_error("Apply mixer settings for audition", target, exc)
            return
        self.mixedTakeCreated.emit(str(target))
        self.auditionRequested.emit(str(target), "Current mixer result")

    def _save_as(self) -> None:
        default = self._default_mixed_path()
        path, _ = QFileDialog.getSaveFileName(self, "Save Mixed MIDI", str(default), "MIDI files (*.mid *.midi)")
        if not path:
            return
        target = Path(path)
        try:
            target = self._apply_mix(target)
        except (OSError, ValueError, RuntimeError, TypeError) as exc:
            self._report_mix_error("Save mixed MIDI", target, exc)
            return
        self.mixedTakeCreated.emit(str(target))
        QMessageBox.information(self, "Mixer", f"Mixed MIDI saved:\n{target}")
