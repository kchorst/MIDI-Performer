from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel,
    QSpinBox, QVBoxLayout,
)

from core.performance_polish import PERFORMANCE_PRESETS, PerformancePolishSettings, preset_settings


class PerformancePolishDialog(QDialog):
    """Focused whole-Take timing, dynamics, articulation, and groove controls."""

    def __init__(self, take_name: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Performance Polish")
        self.setMinimumWidth(520)
        root = QVBoxLayout(self)

        heading = QLabel(f"Finish the feel of: {take_name}")
        heading.setObjectName("SectionTitle")
        heading.setWordWrap(True)
        root.addWidget(heading)
        explanation = QLabel(
            "This creates a new Take and changes the actual MIDI timing, velocities, and note lengths. "
            "The selected Take and original songs remain unchanged."
        )
        explanation.setObjectName("InfoPanel")
        explanation.setWordWrap(True)
        root.addWidget(explanation)

        form = QFormLayout()
        self.profile = QComboBox()
        self.profile.addItems(tuple(PERFORMANCE_PRESETS.keys()))
        self.profile.setCurrentText("Natural")
        self.timing = self._spin(0, 35, 8, " ms")
        self.dynamics = self._spin(0, 30, 6, "%")
        self.articulation = self._spin(65, 115, 96, "%")
        self.swing = self._spin(0, 35, 0, "%")
        self.seed = self._spin(0, 999_999, 0)
        self.preserve_drums = QCheckBox("Keep percussion timing tight")
        self.preserve_drums.setChecked(True)
        self.profile.currentTextChanged.connect(self._load_preset)

        form.addRow("Feel preset", self.profile)
        form.addRow("Timing variation", self.timing)
        form.addRow("Dynamics variation", self.dynamics)
        form.addRow("Note-length articulation", self.articulation)
        form.addRow("Swing / laid-back delay", self.swing)
        form.addRow("Variation seed", self.seed)
        form.addRow("Percussion", self.preserve_drums)
        root.addLayout(form)

        self.summary = QLabel()
        self.summary.setObjectName("StatusPill")
        self.summary.setWordWrap(True)
        root.addWidget(self.summary)
        for widget in (self.timing, self.dynamics, self.articulation, self.swing, self.seed):
            widget.valueChanged.connect(self._update_summary)
        self.preserve_drums.toggled.connect(self._update_summary)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Create Performance Take")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName("PrimaryButton")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._update_summary()

    @staticmethod
    def _spin(minimum: int, maximum: int, value: int, suffix: str = "") -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        if suffix:
            spin.setSuffix(suffix)
        return spin

    def _load_preset(self, name: str) -> None:
        item = preset_settings(name)
        for widget in (self.timing, self.dynamics, self.articulation, self.swing):
            widget.blockSignals(True)
        self.timing.setValue(item.timing_ms)
        self.dynamics.setValue(item.dynamics_percent)
        self.articulation.setValue(item.articulation_percent)
        self.swing.setValue(item.swing_percent)
        self.preserve_drums.setChecked(item.preserve_percussion_timing)
        for widget in (self.timing, self.dynamics, self.articulation, self.swing):
            widget.blockSignals(False)
        self._update_summary()

    def settings(self) -> PerformancePolishSettings:
        return PerformancePolishSettings(
            profile=self.profile.currentText(),
            timing_ms=self.timing.value(),
            dynamics_percent=self.dynamics.value(),
            articulation_percent=self.articulation.value(),
            swing_percent=self.swing.value(),
            preserve_percussion_timing=self.preserve_drums.isChecked(),
            seed=self.seed.value(),
        ).normalized()

    def _update_summary(self) -> None:
        item = self.settings()
        self.summary.setText(" · ".join(item.summary_lines()[1:]))
