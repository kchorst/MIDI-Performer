from __future__ import annotations

"""Launch ownership for Assisted and Version Fusion workflow overlays.

Version Fusion deliberately reuses the mature Assisted arrangement/shaping shell,
but it is a first-class product mode with its own persistent mode identity and
same-composition discovery policy.
"""

from PySide6.QtWidgets import QMessageBox

from app.assisted_studio_wizard import AssistedStudioWizard
from core.assisted_studio import AssistedSetPlan, version_fusion_plan_for_pair
from core.operation_diagnostics import write_diagnostic
from core.song_identity import fusion_difference_kind


class ModeLaunchMixin:
    def launch_fusion_wizard(
        self, initial_anchor_path: str | None = None, *, initial_plan: AssistedSetPlan | None = None
    ) -> None:
        self._launch_assisted_family_wizard(initial_anchor_path, fusion_only=True, initial_plan=initial_plan)

    def launch_assisted_wizard(self, initial_anchor_path: str | None = None) -> None:
        self._launch_assisted_family_wizard(initial_anchor_path, fusion_only=False, initial_plan=None)

    def route_pair_to_fusion(self, first, second) -> str:
        """Evaluate a visible Mash pair using Version Fusion's stricter rules.

        Returns ``fusion`` when a useful same-song Fusion opens, ``neither``
        when a redundant same-song pair is persistently retired, or ``mash``
        when automatic analysis does not establish a same-composition relationship; an explicit user Fusion request may still override identity uncertainty without rewriting Library truth.
        """
        if not self.require_authoritative_family_identity(self, "Version Fusion"):
            return "waiting"
        kind = fusion_difference_kind(first, second)
        if kind == "unrelated":
            # The user explicitly clicked Try as Version Fusion. Automatic identity
            # is allowed to be uncertain, but it must not overrule direct human
            # knowledge that two oddly named files are versions of one song. The
            # override does not rewrite Library family state and still rejects exact
            # or materially indistinguishable copies.
            plan = version_fusion_plan_for_pair(first, second, identity_override=True)
            if plan is not None:
                self.launch_fusion_wizard(initial_anchor_path=first.path, initial_plan=plan)
                return "fusion"
            QMessageBox.information(
                self, "Version Fusion Needs More Difference",
                "Automatic analysis could not confirm that these files are versions of the same composition, and the "
                "files do not contain enough detected arrangement or instrumentation difference to build a useful "
                "Version Fusion. The pair has not been suppressed; you may keep it as a Mash idea or ignore it.",
            )
            return "mash"
        plan = version_fusion_plan_for_pair(first, second)
        if plan is None:
            readable = {
                "exact": "an exact musical duplicate",
                "tempo_only": "the same arrangement with only a tempo/export change",
                "patch_only": "the same underlying arrangement with only a trivial patch change",
                "near_duplicate": "too musically similar to contribute a useful alternate arrangement",
            }.get(kind, "not materially different enough for Version Fusion")
            self.mark_pair_neither(first, second, reason=kind)
            QMessageBox.information(
                self, "Neither Mash nor Useful Fusion",
                f"MIDI Remixer classified these two files as {readable}.\n\n"
                "They will no longer be suggested as a normal Mash pair or as a Version Fusion pair. "
                "You can restore the decision later from File → Manage Suppressed Mash / Fusion Ideas…",
            )
            return "neither"
        self.launch_fusion_wizard(initial_anchor_path=first.path, initial_plan=plan)
        return "fusion"

    def _launch_assisted_family_wizard(
        self, initial_anchor_path: str | None, *, fusion_only: bool, initial_plan: AssistedSetPlan | None = None
    ) -> None:
        attribute = "_fusion_wizard" if fusion_only else "_assisted_wizard"
        existing = getattr(self, attribute, None)
        if existing is not None and existing.isVisible():
            existing.raise_()
            existing.setFocus()
            return
        title = "Version Fusion" if fusion_only else "Assisted Studio"
        log_name = "version-fusion-error.log" if fusion_only else "assisted-studio-error.log"
        try:
            wizard = AssistedStudioWizard(self, initial_anchor_path=initial_anchor_path, fusion_only=fusion_only)
            setattr(self, attribute, wizard)
            if fusion_only and initial_plan is not None:
                wizard.load_preselected_fusion_plan(initial_plan)

            def finished(_result: int) -> None:
                setattr(self, attribute, None)
                self._refresh_decision_summaries()
                self._update_navigation()

            wizard.finished.connect(finished)
            wizard.show()
            wizard.raise_()
            wizard.setFocus()
        except Exception as exc:
            setattr(self, attribute, None)
            log_path = write_diagnostic(self.paths.logs / log_name, f"Open {title}", exc)
            QMessageBox.critical(
                self, f"{title} could not open",
                "MIDI Remixer kept the main project open.\n\n"
                f"{type(exc).__name__}: {exc}\n\nDetails were written to:\n{log_path}",
            )
        else:
            self._refresh_decision_summaries()
            self._update_navigation()
