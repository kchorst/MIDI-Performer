from __future__ import annotations

"""Assisted Studio section-refinement workflow."""

from PySide6.QtWidgets import QMessageBox

from app.selective_refinement_dialog import SelectiveRefinementDialog
from core.remix_engine import RemixTake, take_identity_key


class AssistedWizardRefinementMixin:
    def _refine_take_section(self, take: RemixTake) -> None:
        dialog = SelectiveRefinementDialog(take, mode="assisted", parent=self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        created, failures = self.studio.refinement_controller.create_revisions(take, dialog.requests())
        if not created:
            QMessageBox.warning(
                self,
                "No safe section revision was created",
                "The parent Take remains available.\n\n"
                + ("\n".join(failures) if failures else "No refinement output passed preflight."),
            )
            return
        existing = {take_identity_key(item) for item in self.generated_takes}
        self.generated_takes.extend(item for item in created if take_identity_key(item) not in existing)
        self._populate_results()
        self._show_page(4)
        self.studio.refinement_controller.play_changed_section(created[0])
        message = (
            f"Created {len(created)} section revision(s). A remains unchanged; "
            "use Play Changed Section for focused B/C comparison."
        )
        if failures:
            message += "\n\nSkipped:\n" + "\n".join(f"• {item}" for item in failures)
        QMessageBox.information(self, "Section revisions created", message)

    def _play_changed_section(self, take: RemixTake) -> None:
        self.studio.refinement_controller.play_changed_section(take)

    def _play_parent_section(self, parent: RemixTake, reference: RemixTake) -> None:
        self.studio.refinement_controller.play_take_at_reference_section(parent, reference)

    def _parent_take_for(self, take: RemixTake) -> RemixTake | None:
        if not take.parent_take_id:
            return None
        return next(
            (
                item for item in self.studio.takes
                if item.take_id == take.parent_take_id
                and (not take.parent_trial_id or item.trial_id == take.parent_trial_id)
            ),
            None,
        )
