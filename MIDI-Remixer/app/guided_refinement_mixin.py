from __future__ import annotations

"""Beginner-facing section fixes backed by the shared refinement engine."""

from PySide6.QtWidgets import QMessageBox

from app.selective_refinement_dialog import SelectiveRefinementDialog
from core.remix_engine import RemixTake, take_identity_key


class GuidedWizardRefinementMixin:
    def _guided_refine_section(self, take: RemixTake) -> None:
        dialog = SelectiveRefinementDialog(take, mode="guided", parent=self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        created, failures = self.studio.refinement_controller.create_revisions(take, dialog.requests())
        if not created:
            QMessageBox.warning(
                self,
                "No safe section fix was created",
                "The original Take remains available.\n\n"
                + ("\n".join(failures) if failures else "The revised section did not pass preflight."),
            )
            return
        existing = {take_identity_key(item) for item in self.generated_takes}
        self.generated_takes.extend(item for item in created if take_identity_key(item) not in existing)
        self._populate_results()
        self._show_page(3)
        self.studio.refinement_controller.play_changed_section(created[0])
        QMessageBox.information(
            self,
            "Section fix created",
            f"Created {len(created)} new Take. The earlier Take remains unchanged for comparison.",
        )

    def _guided_play_changed_section(self, take: RemixTake) -> None:
        self.studio.refinement_controller.play_changed_section(take)

    def _guided_play_parent_section(self, child: RemixTake) -> None:
        parent = next(
            (
                item for item in self.studio.takes
                if item.take_id == child.parent_take_id
                and (not child.parent_trial_id or item.trial_id == child.parent_trial_id)
            ),
            None,
        )
        if parent is not None:
            self.studio.refinement_controller.play_take_at_reference_section(parent, child)
