from __future__ import annotations

"""Shared selective-refinement orchestration for all three workflows."""

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Sequence

from core.remix_engine import RemixTake, take_identity_key
from core.selective_refinement import (
    RefinementRequest,
    RefinementResult,
    refine_take,
    section_start_percent,
)
from core.take_quality import inspect_generated_take

if TYPE_CHECKING:
    from app.studio_window import MidiRemixerStudio


class RefinementController:
    def __init__(self, studio: "MidiRemixerStudio") -> None:
        self.studio = studio

    def create_revisions(
        self,
        parent: RemixTake,
        requests: Sequence[RefinementRequest],
    ) -> tuple[list[RemixTake], list[str]]:
        studio = self.studio
        exact_parent = next(
            (item for item in studio.takes if take_identity_key(item) == take_identity_key(parent)),
            None,
        )
        if exact_parent is None or not Path(exact_parent.path).is_file():
            raise FileNotFoundError("The parent Take is no longer available in this session.")
        normalized = [request.normalized() for request in requests]
        if not normalized:
            raise ValueError("Choose at least one section refinement.")

        trial_number = len(studio.session_workspace.trials()) + 1
        trial_name = f"Refinement Trial {trial_number}"
        decisions = [
            f"Selective refinement parent: {exact_parent.trial_name or exact_parent.trial_id} / {exact_parent.take_id}",
            *(
                line
                for request in normalized
                for line in request.summary_lines()
            ),
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
                "requests": [request.to_dict() for request in normalized],
            },
        )
        studio.current_trial_id = trial.trial_id
        output = studio.session_workspace.trial_directory(trial.trial_id) / "selective-refinement"
        output.mkdir(parents=True, exist_ok=True)

        results: list[RefinementResult] = []
        failures: list[str] = []
        for request in normalized:
            try:
                result = refine_take(exact_parent, output, request)
                quality = inspect_generated_take(result.take.path)
                if not quality.passed:
                    failures.append(
                        f"{request.comparison_slot} {request.section_name}: structural gate: "
                        + "; ".join(quality.errors)
                    )
                    Path(result.take.path).unlink(missing_ok=True)
                    continue
                if not result.preflight.passed:
                    failures.append(
                        f"{request.comparison_slot} {request.section_name}: section preflight: "
                        + "; ".join(result.preflight.errors)
                    )
                    Path(result.take.path).unlink(missing_ok=True)
                    continue
                quality_lines = [
                    f"Structural quality gate passed: {quality.note_on_count} note-ons across {quality.audible_track_count} audible tracks.",
                    result.preflight.summary,
                ]
                quality_lines.extend(f"Listening prompt: {warning}" for warning in quality.warnings)
                quality_lines.extend(f"Section listening prompt: {warning}" for warning in result.preflight.warnings)
                results.append(
                    replace(
                        result,
                        take=replace(
                            result.take,
                            decision_summary=tuple(dict.fromkeys((*result.take.decision_summary, *quality_lines))),
                        ),
                    )
                )
            except Exception as exc:
                failures.append(
                    f"{request.comparison_slot} {request.section_name}: {type(exc).__name__}: {exc}"
                )

        if not results:
            studio.session_workspace.delete_trial(trial.trial_id)
            studio.current_trial_id = exact_parent.trial_id or None
            return [], failures

        next_number = studio._next_take_number()
        created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        created: list[RemixTake] = []
        for offset, result in enumerate(results):
            take_id = f"take-{next_number + offset:03d}"
            annotated = replace(
                result.take,
                take_id=take_id,
                trial_id=trial.trial_id,
                trial_name=trial_name,
                generation_run=trial_number,
                created_at=created_at,
            )
            created.append(annotated)
            studio.session_workspace.register_take(trial.trial_id, take_id, annotated.path)

        studio.takes.extend(created)
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
        studio.statusBar().showMessage(
            f"{trial_name}: created {len(created)} section revision(s); {len(failures)} skipped."
        )
        return created, failures

    @staticmethod
    def _section_percent(take: RemixTake) -> int:
        percent = 0
        settings = tuple(take.refinement_settings or ())
        for item in settings:
            if str(item).startswith("section_index="):
                try:
                    index = int(str(item).split("=", 1)[1])
                except ValueError:
                    continue
                count = 6
                for candidate in settings:
                    if str(candidate).startswith("section_count="):
                        try:
                            count = max(1, int(str(candidate).split("=", 1)[1]))
                        except ValueError:
                            count = 6
                percent = max(0, min(99, int(round(index * 100 / count))))
                break
        return percent

    def play_changed_section(self, take: RemixTake) -> bool:
        return self.studio._play_path(
            take.path,
            f"{take.name} — changed section",
            "refined-section::" + "::".join(take_identity_key(take)),
            start_percent=self._section_percent(take),
        )

    def play_take_at_reference_section(self, take: RemixTake, reference: RemixTake) -> bool:
        return self.studio._play_path(
            take.path,
            f"{take.name} — comparison section",
            "refined-parent-section::" + "::".join(take_identity_key(take)),
            start_percent=self._section_percent(reference),
        )

    def play_request_section(self, take: RemixTake, request: RefinementRequest) -> bool:
        return self.studio._play_path(
            take.path,
            f"{take.name} — {request.section_name}",
            "refinement-request::" + "::".join(take_identity_key(take)),
            start_percent=section_start_percent(request),
        )
