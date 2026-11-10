from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
)

from core.guided_beginner import GuidedPairPlan


@dataclass(frozen=True)
class GuidedAdjustmentValues:
    target_bpm: int
    start_bar_a: int
    start_bar_b: int
    lead_gain_percent: int
    backing_gain_percent: int
    preserve_signature_intro: bool


class GuidedTakeAdjustmentDialog(QDialog):
    """Small, explicit controls that must create an audibly different Take.

    The previous Guided adjustment action translated broad feedback into engine
    flags that could already be enabled, so repeated requests such as "backing
    too loud" produced the same MIDI. This dialog exposes the actual generation
    inputs and returns concrete values for a new Trial.
    """

    def __init__(
        self,
        plan: GuidedPairPlan,
        *,
        lead_gain_percent: int = 100,
        backing_gain_percent: int = 100,
        preserve_signature_intro: bool = True,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.plan = plan
        self.setWindowTitle("Adjust This Take")
        self.setMinimumWidth(560)

        root = QVBoxLayout(self)
        explanation = QLabel(
            "Change the real generation settings below, then create a new adjusted Take. "
            "The current Take remains available for comparison and the original songs are never changed."
        )
        explanation.setWordWrap(True)
        explanation.setObjectName("InfoPanel")
        root.addWidget(explanation)

        form = QFormLayout()
        self.target_bpm = self._spin(40, 220, int(plan.target_bpm), " BPM")
        # Leave at least one complete phrase available after the selected start.
        # Choosing the last bar used to produce a technically valid but effectively
        # empty Take, which sounded like the adjustment had been ignored.
        minimum_tail = max(4, int(plan.section_bars))
        max_start_a = max(1, int(plan.source_a.bars) - minimum_tail + 1)
        max_start_b = max(1, int(plan.source_b.bars) - minimum_tail + 1)
        self.start_a = self._spin(
            1,
            max_start_a,
            max(1, int(plan.start_bar_a) + 1),
            "",
        )
        self.start_b = self._spin(
            1,
            max_start_b,
            max(1, int(plan.start_bar_b) + 1),
            "",
        )
        self.lead_level = self._spin(30, 140, int(lead_gain_percent), "%")
        self.backing_level = self._spin(10, 140, int(backing_gain_percent), "%")
        self.keep_signature = QCheckBox("Keep the protected opening before the mash when one was detected")
        self.keep_signature.setChecked(bool(preserve_signature_intro))

        form.addRow("Target pulse", self.target_bpm)
        form.addRow(f"{plan.source_a.title} — source start bar", self.start_a)
        form.addRow(f"{plan.source_b.title} — source start bar", self.start_b)
        form.addRow("Lead melody level", self.lead_level)
        form.addRow("Accompaniment / backing level", self.backing_level)
        form.addRow("Protected opening", self.keep_signature)
        root.addLayout(form)

        note = QLabel(
            "Start bar chooses which part of each source is used. Level changes are written into the newly generated MIDI, "
            "so replaying the adjusted Take does not depend on the master volume slider."
        )
        note.setWordWrap(True)
        note.setObjectName("DetailsPanel")
        root.addWidget(note)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        save = buttons.button(QDialogButtonBox.StandardButton.Save)
        save.setText("Create Adjusted Take")
        save.setObjectName("PrimaryButton")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    @staticmethod
    def _spin(minimum: int, maximum: int, value: int, suffix: str) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(int(minimum), max(int(minimum), int(maximum)))
        spin.setValue(max(int(minimum), min(int(maximum), int(value))))
        if suffix:
            spin.setSuffix(suffix)
        return spin

    def values(self) -> GuidedAdjustmentValues:
        return GuidedAdjustmentValues(
            target_bpm=int(self.target_bpm.value()),
            start_bar_a=max(0, int(self.start_a.value()) - 1),
            start_bar_b=max(0, int(self.start_b.value()) - 1),
            lead_gain_percent=int(self.lead_level.value()),
            backing_gain_percent=int(self.backing_level.value()),
            preserve_signature_intro=bool(self.keep_signature.isChecked()),
        )
