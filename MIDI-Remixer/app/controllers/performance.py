from __future__ import annotations

"""Create registered performance-polish revisions from exact generated Takes."""

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from app.performance_polish_dialog import PerformancePolishDialog
from core.operation_diagnostics import operation_failure, write_diagnostic
from core.performance_polish import PerformancePolishSettings, polish_midi
from core.remix_engine import RemixTake, take_identity_key
from core.take_quality import inspect_generated_take

if TYPE_CHECKING:
    from app.studio_window import MidiRemixerStudio


class PerformanceController:
    def __init__(self, studio: "MidiRemixerStudio") -> None:
        self.studio = studio

    def open_dialog(self) -> None:
        studio = self.studio
        host = QApplication.activeModalWidget() or studio
        if not studio.selected_take:
            QMessageBox.information(host, "MIDI Remixer", "Single-click a Take first, then apply Performance Polish.")
            return
        parent_take = studio.selected_take
        dialog = PerformancePolishDialog(parent_take.name, host)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            created = self.create_polished_take(parent_take, dialog.settings())
        except (OSError, ValueError, RuntimeError, TypeError, EOFError) as exc:
            write_diagnostic(
                studio.paths.logs / "ui-errors.log",
                "Performance Polish",
                exc,
                path=parent_take.path,
                take_id=parent_take.take_id,
            )
            QMessageBox.critical(
                host,
                "Performance Polish failed",
                operation_failure(
                    "Performance Polish",
                    exc,
                    path=parent_take.path,
                    corrective_action="Confirm the selected Take still exists, then try a tighter preset.",
                ),
            )
            return
        finally:
            QApplication.restoreOverrideCursor()

        studio.select_exact_take(created)
        studio._play_path(
            created.path,
            created.name,
            "performance::" + "::".join(take_identity_key(created)),
        )
        QMessageBox.information(
            host,
            "Performance Take created",
            "A new non-destructive performance revision was created and selected. "
            "Use the parent Take for A/B comparison.",
        )

    def create_polished_take(self, parent: RemixTake, settings: PerformancePolishSettings) -> RemixTake:
        studio = self.studio
        exact_parent = next(
            (item for item in studio.takes if take_identity_key(item) == take_identity_key(parent)),
            None,
        )
        if exact_parent is None or not Path(exact_parent.path).is_file():
            raise FileNotFoundError("The selected parent Take is no longer available in this session.")

        item = settings.normalized()
        trial_number = len(studio.session_workspace.trials()) + 1
        trial_name = f"Performance Polish Trial {trial_number}"
        decisions = [
            f"Performance polish parent: {exact_parent.trial_name or exact_parent.trial_id} / {exact_parent.take_id}",
            *item.summary_lines(),
        ]
        source_paths = list(exact_parent.source_paths or tuple(
            path for path in (exact_parent.source_a, exact_parent.source_b) if path
        ))
        trial = studio.session_workspace.create_trial(
            trial_name,
            studio.guidance_mode,
            decisions=decisions,
            source_paths=source_paths,
            settings={
                "parent_take_id": exact_parent.take_id,
                "parent_trial_id": exact_parent.trial_id,
                "performance_polish": item.to_dict(),
            },
        )
        output_dir = studio.session_workspace.trial_directory(trial.trial_id) / "performance-polish"
        output_dir.mkdir(parents=True, exist_ok=True)
        slug = "-".join(part for part in item.profile.casefold().replace("_", "-").split() if part) or "natural"
        target = output_dir / f"{Path(exact_parent.path).stem}-performance-{slug}.mid"

        try:
            output = polish_midi(exact_parent.path, target, item)
            quality = inspect_generated_take(output)
            if not quality.passed:
                raise ValueError("Performance output failed the structural gate: " + "; ".join(quality.errors))
        except (OSError, ValueError, RuntimeError, TypeError, EOFError):
            studio.session_workspace.delete_trial(trial.trial_id)
            raise

        take_id = f"take-{studio._next_take_number():03d}"
        lineage_root = exact_parent.lineage_root_take_id or exact_parent.take_id
        summary = tuple(dict.fromkeys((
            *exact_parent.decision_summary,
            *item.summary_lines(),
            f"Structural quality gate passed: {quality.note_on_count} note-ons across {quality.audible_track_count} audible tracks.",
            *(f"Listening prompt: {warning}" for warning in quality.warnings),
        )))
        created = replace(
            exact_parent,
            take_id=take_id,
            name=f"{exact_parent.name} — {item.profile} performance",
            description=f"{item.profile} performance polish applied as a non-destructive full-Take revision.",
            path=str(output),
            trial_id=trial.trial_id,
            trial_name=trial_name,
            generation_run=trial_number,
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            status="candidate",
            approved=False,
            exported_paths=(),
            decision_summary=summary,
            parent_take_id=exact_parent.take_id,
            parent_trial_id=exact_parent.trial_id,
            parent_path=exact_parent.path,
            lineage_root_take_id=lineage_root,
            revision_generation=max(1, int(exact_parent.revision_generation or 0) + 1),
            changed_sections=("Full performance",),
            refinement_reason="Performance polish",
            refinement_settings=tuple(f"{key}={value}" for key, value in item.to_dict().items()),
            comparison_slot="B",
        )

        studio.current_trial_id = trial.trial_id
        studio.session_workspace.register_take(trial.trial_id, take_id, created.path)
        studio.takes.append(created)
        studio.project.guidance_mode = studio.guidance_mode
        studio.project.session_id = studio.session_workspace.session_id
        studio.project.trials = list(studio.session_workspace.manifest.trials)
        studio.project.active_trial_id = trial.trial_id
        studio.project.output_directory = str(studio.session_workspace.root)
        studio.project.set_takes(studio.takes)
        studio._session_dirty = True
        studio._session_saved = False
        studio._refresh_takes()
        studio._update_workflow_state()
        return created
