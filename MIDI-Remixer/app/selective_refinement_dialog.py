from __future__ import annotations

"""Guided, Assisted, and Workbench UI for selective section regeneration."""

from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.arrangement_intelligence import SECTION_NAMES
from core.remix_engine import RemixTake
from core.selective_refinement import (
    RefinementRequest,
    alternate_request,
    refinement_preset,
)


ISSUE_CHOICES: tuple[tuple[str, str], ...] = (
    ("clarity", "Make this section clearer"),
    ("reduce-backing", "Reduce the backing"),
    ("wrong-lead", "Use a clearer lead focus"),
    ("melody-conflict", "Reduce melody conflict"),
    ("bass-conflict", "Reduce bass conflict"),
    ("rhythm-off", "Simplify the rhythm"),
    ("harmony-wrong", "Simplify conflicting harmony"),
    ("starts-early", "Move the section later"),
    ("starts-late", "Move the section earlier"),
    ("too-busy", "Make the section less busy"),
    ("too-empty", "Make the section fuller"),
    ("weak-transition", "Strengthen the transition"),
    ("instrument-fit", "Try a different lead instrument"),
    ("weak-ending", "Strengthen the ending"),
)

PROGRAM_CHOICES: tuple[tuple[str, int | None], ...] = (
    ("Keep current instruments", None),
    ("Acoustic grand piano", 0),
    ("Electric piano", 4),
    ("Clean electric guitar", 27),
    ("Violin", 40),
    ("String ensemble", 48),
    ("Trumpet", 56),
    ("Tenor sax", 66),
    ("Flute", 73),
    ("Synth lead", 80),
)


class SelectiveRefinementDialog(QDialog):
    def __init__(
        self,
        take: RemixTake,
        *,
        mode: str = "assisted",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.take = take
        self.mode = mode if mode in {"guided", "fusion", "assisted", "workbench"} else "assisted"
        self.setWindowTitle("Refine One Section")
        self.setMinimumWidth(620 if self.mode != "guided" else 520)

        root = QVBoxLayout(self)
        explanation = QLabel(
            "Choose the part that sounds wrong. MIDI Remixer will create a new revision from this exact Take, "
            "change only that section and its boundary, and keep the current Take as A for comparison."
        )
        explanation.setObjectName("InfoPanel")
        explanation.setWordWrap(True)
        root.addWidget(explanation)

        form = QFormLayout()
        self.section_combo = QComboBox()
        for index, name in enumerate(SECTION_NAMES):
            self.section_combo.addItem(name, index)
        if take.changed_sections:
            prior = self.section_combo.findText(take.changed_sections[-1])
            if prior >= 0:
                self.section_combo.setCurrentIndex(prior)
        self.issue_combo = QComboBox()
        for code, label in ISSUE_CHOICES:
            self.issue_combo.addItem(label, code)
        form.addRow("Section", self.section_combo)
        form.addRow("What sounds wrong?", self.issue_combo)
        root.addLayout(form)

        self.advanced_box = QGroupBox("Audible controls")
        advanced = QFormLayout(self.advanced_box)
        self.lead_gain = self._spin(20, 180, 100, "%")
        self.backing_gain = self._spin(10, 180, 100, "%")
        self.bass_gain = self._spin(0, 180, 100, "%")
        self.drum_density = self._spin(0, 150, 100, "%")
        self.pitch_shift = self._spin(-12, 12, 0, " semitones")
        self.octave_shift = self._spin(-2, 2, 0, " octaves")
        self.timing_shift = self._spin(-8, 8, 0, " beats")
        self.transition = self._spin(0, 8, 0, " beats")
        self.duration_delta = self._spin(-4, 8, 0, " bars")
        self.instrument = QComboBox()
        for label, program in PROGRAM_CHOICES:
            self.instrument.addItem(label, program)
        self.instrument_target = QComboBox()
        self.instrument_target.addItem("Lead only", "lead")
        self.instrument_target.addItem("Backing only", "backing")
        self.instrument_target.addItem("All pitched tracks", "all")

        advanced.addRow("Lead level", self.lead_gain)
        advanced.addRow("Backing level", self.backing_gain)
        advanced.addRow("Bass intensity", self.bass_gain)
        advanced.addRow("Drum density", self.drum_density)
        advanced.addRow("Pitch correction", self.pitch_shift)
        advanced.addRow("Lead register", self.octave_shift)
        advanced.addRow("Entry timing", self.timing_shift)
        advanced.addRow("Transition envelope", self.transition)
        advanced.addRow("Section duration", self.duration_delta)
        advanced.addRow("Instrument substitution", self.instrument)
        advanced.addRow("Instrument target", self.instrument_target)
        root.addWidget(self.advanced_box)

        comparison_row = QHBoxLayout()
        self.create_alternate = QCheckBox("Also create a different C option")
        self.create_alternate.setChecked(self.mode != "guided")
        comparison_help = QLabel("A remains the parent Take; B and optional C are new session-owned revisions.")
        comparison_help.setObjectName("Subtitle")
        comparison_help.setWordWrap(True)
        comparison_row.addWidget(self.create_alternate)
        comparison_row.addWidget(comparison_help, 1)
        root.addLayout(comparison_row)

        if self.mode == "guided":
            self.advanced_box.setVisible(False)
            self.create_alternate.setChecked(False)
            self.create_alternate.setVisible(False)

        self.issue_combo.currentIndexChanged.connect(self._apply_preset)
        self._apply_preset()

        note = QLabel(
            "Level, timing, register, duration, percussion, and instrument changes are written into the new MIDI file. "
            "Preflight checks reject silent, incomplete, or unsafe revisions before they appear."
        )
        note.setObjectName("DetailsPanel")
        note.setWordWrap(True)
        root.addWidget(note)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        save = buttons.button(QDialogButtonBox.StandardButton.Save)
        save.setText("Create Section Revision")
        save.setObjectName("PrimaryButton")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    @staticmethod
    def _spin(minimum: int, maximum: int, value: int, suffix: str) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        if suffix:
            spin.setSuffix(suffix)
        return spin

    def _apply_preset(self, *_args) -> None:
        code = str(self.issue_combo.currentData() or "clarity")
        values = refinement_preset(code)
        mapping = {
            "lead_gain_percent": self.lead_gain,
            "backing_gain_percent": self.backing_gain,
            "bass_gain_percent": self.bass_gain,
            "drum_density_percent": self.drum_density,
            "pitch_shift_semitones": self.pitch_shift,
            "octave_shift": self.octave_shift,
            "timing_shift_beats": self.timing_shift,
            "transition_beats": self.transition,
            "duration_delta_bars": self.duration_delta,
        }
        for key, widget in mapping.items():
            if key in values and values[key] is not None:
                widget.setValue(int(values[key]))
        program = values.get("instrument_program")
        program_index = self.instrument.findData(program)
        self.instrument.setCurrentIndex(program_index if program_index >= 0 else 0)
        target_index = self.instrument_target.findData(values.get("instrument_target", "lead"))
        if target_index >= 0:
            self.instrument_target.setCurrentIndex(target_index)

    def _primary_request(self) -> RefinementRequest:
        section_index = int(self.section_combo.currentData())
        code = str(self.issue_combo.currentData() or "clarity")
        label = str(self.issue_combo.currentText())
        return RefinementRequest(
            section_name=str(self.section_combo.currentText()),
            section_index=section_index,
            section_count=len(SECTION_NAMES),
            issue_code=code,
            issue_label=label,
            lead_gain_percent=self.lead_gain.value(),
            backing_gain_percent=self.backing_gain.value(),
            bass_gain_percent=self.bass_gain.value(),
            drum_density_percent=self.drum_density.value(),
            pitch_shift_semitones=self.pitch_shift.value(),
            octave_shift=self.octave_shift.value(),
            timing_shift_beats=self.timing_shift.value(),
            transition_beats=self.transition.value(),
            duration_delta_bars=self.duration_delta.value(),
            instrument_program=self.instrument.currentData(),
            instrument_target=str(self.instrument_target.currentData() or "lead"),
            comparison_slot="B",
        ).normalized()

    def requests(self) -> tuple[RefinementRequest, ...]:
        primary = self._primary_request()
        if self.create_alternate.isChecked():
            return primary, alternate_request(primary)
        return (primary,)
