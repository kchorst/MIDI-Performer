from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING
import traceback

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QComboBox, QDialog, QFrame, QGridLayout,
    QGroupBox, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QRadioButton, QScrollArea, QSizePolicy, QSlider, QSpinBox,
    QStackedWidget, QVBoxLayout, QWidget,
)
PAIR_PREFIX = "pair:"
MULTI_PREFIX = "multi:"

ENGINE_PLAN_PRESETS = (
    {
        "key": "balanced",
        "title": "Balanced Plan",
        "best_for": "Best first comparison",
        "description": "Keeps the shared pulse and each song's character, then generates the safest broad mix of arrangements.",
        "energy": "Balanced target pulse",
        "harmony": "Preserve each song's character",
    },
    {
        "key": "melody",
        "title": "Melody Spotlight",
        "best_for": "Best when one hook should lead",
        "description": "Prioritizes melody-over-backing and melody-relay ideas, with automatic repair only where a clash is severe.",
        "energy": "Balanced target pulse",
        "harmony": "Adapt severe clashes",
    },
    {
        "key": "sections",
        "title": "Section Journey",
        "best_for": "Best when simultaneous layering is risky",
        "description": "Uses cleaner section exchanges and transformations so each source can keep more of its own harmony.",
        "energy": "More relaxed",
        "harmony": "Prefer section-safe arrangements",
    },
    {
        "key": "collage",
        "title": "Build & Finale",
        "best_for": "Best for a rising multi-source arrangement",
        "description": "Uses a more energetic frame and prioritizes layered, progressive, and finale-style recipe families.",
        "energy": "More energetic",
        "harmony": "Prefer section-safe arrangements",
    },
)
from core.operation_diagnostics import write_diagnostic
from core.assisted_studio import (
    AssistedSetPlan, assisted_library_summary, assisted_score_label,
)
from core.multi_source import SourceSpec
from core.remix_profile import key_name
from core.remix_engine import take_identity_key
from app.controllers.workflow_state import WorkflowBranchKey
from app.deferred_actions import schedule_owned
from app.theme import load_stylesheet
from app.button_policy import install_button_policy, set_action_enabled

from app.assisted_arrangement_mixin import AssistedWizardArrangementMixin
from app.assisted_discovery_mixin import AssistedWizardDiscoveryMixin
from app.assisted_pages_mixin import AssistedWizardPagesMixin
from app.assisted_plan_mixin import AssistedWizardPlanMixin
from app.assisted_refinement_mixin import AssistedWizardRefinementMixin

if TYPE_CHECKING:
    from app.decision_workflow import DecisionWorkflowStudio
    from core.remix_engine import RemixTake


class AssistedStudioWizard(AssistedWizardRefinementMixin, AssistedWizardArrangementMixin, AssistedWizardDiscoveryMixin, AssistedWizardPagesMixin, AssistedWizardPlanMixin, QDialog):
    """Engine-led middle workflow.

    Assisted Studio searches for useful two- and multi-source sets, explains the
    measurable reasons, fills the technical frame, then lets the DJ choose the
    broad artistic direction. Detailed track mapping remains available through
    More Control / Creative Workbench.
    """
    def __init__(self, studio: "DecisionWorkflowStudio", *, initial_anchor_path: str | None = None, fusion_only: bool = False) -> None:
        # Keep Assisted Studio in the same application workspace so mode,
        # audio, transport, and project state cannot appear to disagree across
        # two independent windows.
        super().__init__(studio.centralWidget())
        self.setWindowFlags(Qt.WindowType.Widget)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setModal(False)
        self.studio = studio
        self.fusion_only = bool(fusion_only)
        self.workflow_title = "Version Fusion" if self.fusion_only else "Assisted Studio"
        self._fusion_family_members_cache: dict[str, tuple[str, ...]] | None = None
        self._fusion_family_cache_revision = -1
        self._fusion_family_offset = 0
        if self.parentWidget() is not None:
            self.parentWidget().installEventFilter(self)
        self.recommendations: tuple[AssistedSetPlan, ...] = ()
        self.selected_plan: AssistedSetPlan | None = None
        self.generated_takes: list["RemixTake"] = []
        self.selected_take: "RemixTake | None" = None
        self._active_plan_key: WorkflowBranchKey | None = None
        self._generated_branch_key: WorkflowBranchKey | None = None
        self._suspend_branch_invalidation = False
        self.current_page = 0
        self.direction_key = "balanced"
        self.engine_plan_key = "balanced"
        self.energy_key = "balanced"
        self.harmony_key = "preserve"
        self._last_plan_snapshot: dict[str, object] | None = None
        self.section_blueprint = None
        self.section_focus_combos: list[QComboBox] = []
        self.setWindowTitle("Version Fusion — Combine Alternate Arrangements" if self.fusion_only else "Assisted Studio — Recommended Combinations")
        self.setModal(False)
        self.resize(1000, 680)
        self.setMinimumSize(720, 500)
        self._build_ui()
        self._initialize_discovery(initial_anchor_path)
        self._apply_style()
        install_button_policy(self)
        self.transport_timer = QTimer(self)
        self.transport_timer.setInterval(180)
        self.transport_timer.timeout.connect(self._sync_transport)
        self.transport_timer.start()
        self.finished.connect(lambda _result: self.transport_timer.stop())
        self._fit_to_screen()
        self._refresh_readiness()
        self._show_page(0)

    # ---------------------------------------------------------------- UI

    def _report_error(self, context: str, exc: Exception) -> None:
        log_path = write_diagnostic(
            self.studio.paths.logs / ("version-fusion-error.log" if self.fusion_only else "assisted-studio-error.log"),
            context,
            exc,
        )
        QMessageBox.critical(
            self,
            f"{self.workflow_title} could not complete that step",
            f"{context} failed, but MIDI Remixer kept the project open.\n\n"
            f"{type(exc).__name__}: {exc}\n\nDetails: {log_path}",
        )

    # ---------------------------------------------------------- preparation
    def _refresh_readiness(self) -> None:
        summary = self.studio.assisted_library_summary_cached()
        self._populate_anchor_choices()
        self.library_status.setText(f"MIDI library: {summary['total']} classified files")
        soundfont = self.studio.resolved_soundfont_path()
        sf_name = Path(soundfont).name if soundfont and Path(soundfont).exists() else "none"
        self.instrument_status.setText(f"Instrument Library: {sf_name}")
        self.soundfont.setText(f"Instrument Library: {sf_name}")
        fs_path = self.studio.player.find_executable()
        audio_ready = bool(fs_path and Path(fs_path).exists())
        soundfont_ready = bool(soundfont and Path(soundfont).exists())
        self.audio_status.setText(f"Audio engine: {Path(fs_path).name if audio_ready else 'FluidSynth not found'}")
        if self.fusion_only:
            family_count = len(self._fusion_family_members())
            self.source_status.setText(
                f"Version families in saved catalogue: {family_count:,} · Deep analysis determines which are materially different"
            )
        else:
            family_count = 0
            self.source_status.setText(
                f"Studio-eligible: {summary['eligible']} · ready: {summary['studio_ready']} · adaptable: {summary['adaptable']} · whole-section: {summary['whole_section']} · hidden: {summary['hidden']}"
            )
        ready = audio_ready and soundfont_ready
        self.select_soundfont_button.setVisible(not soundfont_ready)
        self.setup_audio_button.setVisible(not audio_ready)
        self.use_library.setEnabled((family_count >= 1 if self.fusion_only else summary["eligible"] >= 2) and ready)
        self.choose_folder_button.setEnabled(True)
        if not ready:
            self.source_status.setText(self.studio.audio_readiness_message())
        self._refresh_deep_analysis_controls()

    def _refresh_deep_analysis_controls(self) -> None:
        if not hasattr(self, "deep_analysis_controls"):
            return
        snapshot = self.studio.deep_analysis_progress()
        total = int(snapshot.get("total", 0) or 0)
        finished = int(snapshot.get("finished", 0) or 0)
        remaining = int(snapshot.get("remaining", 0) or 0)
        self.deep_analysis_controls.setVisible(total > 0)
        if total > 0:
            self.deep_analysis_status.setText(
                "Library detailed analysis complete" if remaining <= 0 else f"Library detailed analysis: {finished}/{total}"
            )

    def _set_scanning(self, active: bool, message: str) -> None:
        self.scan_frame.setVisible(active or bool(message))
        self.scan_status.setText(message)
        self.scan_progress.setRange(0, 0 if active else 1)
        if not active:
            self.scan_progress.setValue(1)

        # During a live search, collapse setup/configuration sections so the
        # progress bar and Cancel Search control remain visible on common
        # 1366x768 and scaled Windows laptop displays. Restore them afterward.
        for section_name in ("readiness_group", "search_actions_group", "discovery_options_group"):
            section = getattr(self, section_name, None)
            if section is not None:
                section.setVisible(not active)

        self.use_library.setEnabled(not active and self.studio.audio_ready())
        self.next.setEnabled(not active and self.studio.audio_ready())
        self.choose_folder_button.setEnabled(not active)
        if hasattr(self, "anchor_combo"):
            anchor_mode = bool(getattr(self, "anchor_mode_anchor", None) and self.anchor_mode_anchor.isChecked())
            self.anchor_combo.setEnabled(not active and anchor_mode)
            self.preview_bars_combo.setEnabled(not active)
            if hasattr(self, "anchor_picker_button"):
                self.anchor_picker_button.setEnabled(not active and anchor_mode)
            if hasattr(self, "anchor_mode_anchor"):
                self.anchor_mode_anchor.setEnabled(not active)
            if hasattr(self, "anchor_mode_any"):
                self.anchor_mode_any.setEnabled(not active)
        if hasattr(self, "cancel_search_button"):
            self.cancel_search_button.setVisible(active)
        if hasattr(self, "more_ideas_button"):
            self.more_ideas_button.setEnabled(not active)
        if hasattr(self, "ideas_cancel_search_button"):
            self.ideas_cancel_search_button.setVisible(active and self.current_page == 1)
            self.ideas_cancel_search_button.setEnabled(active)
        QApplication.processEvents()
        if active and hasattr(self, "prepare_scroll"):
            self.prepare_scroll.verticalScrollBar().setValue(0)
            self.prepare_scroll.ensureWidgetVisible(self.scan_frame, 0, 0)

    def _choose_folder(self) -> None:
        self._invalidate_discovery_cache()
        self.studio._open_library_manager()
        self._refresh_readiness()

    def _add_files(self) -> None:
        self._invalidate_discovery_cache()
        self.studio._open_library_manager()
        self._refresh_readiness()

    def _choose_soundfont(self) -> None:
        self.studio.choose_soundfont()
        self._refresh_readiness()

    def _setup_audio(self) -> None:
        self.studio.show_audio_setup()
        self._refresh_readiness()

    # Recommendation discovery and short previews live in AssistedWizardDiscoveryMixin.

    def _plan_identity(self, plan: AssistedSetPlan) -> WorkflowBranchKey:
        return WorkflowBranchKey.create(
            (profile.path for profile in plan.profiles),
            target_bpm=plan.target_bpm,
            target_key_pc=plan.target_key_pc,
            target_mode=plan.target_mode,
            bars=plan.bars,
            section_bars=plan.section_bars,
            recipe_keys=plan.recipe_keys,
            options=(*plan.source_roles, plan.category, plan.headline),
        )

    def _current_branch_key(self) -> WorkflowBranchKey | None:
        plan = self.selected_plan
        if plan is None:
            return None
        bpm, target_pc, target_mode, bars, section_bars, mode_handling, auto_balance = self._resolved_frame()
        return WorkflowBranchKey.create(
            (profile.path for profile in plan.profiles),
            target_bpm=bpm,
            target_key_pc=target_pc,
            target_mode=target_mode,
            bars=bars,
            section_bars=section_bars,
            recipe_keys=self._selected_recipes(),
            options=(
                self.direction_key, self.energy_combo.currentText(), self.harmony_combo.currentText(),
                self.signature_combo.currentText(), self.length_combo.currentText(),
                self.variation_spin.value(), mode_handling, auto_balance,
            ),
        )

    def _invalidate_active_branch(self) -> None:
        self.studio.clear_active_take_for_new_branch()
        self.selected_take = None
        self.generated_takes = []
        self._generated_branch_key = None
        if hasattr(self, "result_summary"):
            self.result_summary.setText(
                "The song set or plan changed. Generate a new Assisted Trial and choose a Take; earlier Trials remain available in project history."
            )
        if hasattr(self, "results_workbench_button"):
            self.results_workbench_button.setText("Choose a Take to Continue")
            set_action_enabled(self.results_workbench_button, False, "Choose a Take before continuing to Creative Workbench.")

    def _generation_input_changed(self, *_args) -> None:
        if self._suspend_branch_invalidation:
            return
        if self.generated_takes or self.selected_take is not None:
            self._invalidate_active_branch()

    def _choose_plan(self, plan: AssistedSetPlan) -> None:
        self.studio.record_mash_feedback(plan.profiles, "choose")
        plan_key = self._plan_identity(plan)
        if self._active_plan_key is not None and plan_key != self._active_plan_key:
            self._invalidate_active_branch()
        self._active_plan_key = plan_key
        self.selected_plan = plan
        self.shape_title.setText(plan.headline)
        role_lines = "\n".join(f"• {profile.title}: {role}" for profile, role in zip(plan.profiles, plan.source_roles))
        self.shape_summary.setText(
            f"{plan.summary}\n\nEngine's initial role suggestion:\n{role_lines}"
            + "\n\nProposed contributions:\n" + "\n".join(f"• {item}" for item in plan.contribution_plan)
            + "\n\nSignature handling:\n" + "\n".join(f"• {item}" for item in plan.signature_plan)
            + "\n\nAvailable recipe families: " + ", ".join(key.replace("-", " ") for key in plan.recipe_keys)
        )
        self.engine_plan_key = "balanced"
        self._choose_direction("balanced")
        self._refresh_arrangement_intelligence()
        self._reset_section_blueprint(invalidate=False)
        self._show_page(2)

    def _choose_direction(self, key: str) -> None:
        changed = key != self.direction_key
        self.direction_key = key
        if changed and not self._suspend_branch_invalidation and (self.generated_takes or self.selected_take is not None):
            self._invalidate_active_branch()
        for name, button in self.direction_buttons.items():
            button.blockSignals(True)
            button.setChecked(name == key)
            button.blockSignals(False)

    # --------------------------------------------------------- plan/generate
    @staticmethod

    def _alternate_plan(self) -> None:
        order = tuple(preset["key"] for preset in ENGINE_PLAN_PRESETS)
        current = self.engine_plan_key if self.engine_plan_key in order else self.direction_key
        self._apply_engine_plan_preset(order[(order.index(current) + 1) % len(order)])

    def _prepare_parent(self) -> tuple[str, ...]:
        plan = self.selected_plan
        if not plan:
            return ()
        mode_name = "fusion" if self.fusion_only else "assisted"
        self.studio.guidance_mode = mode_name
        self.studio.pair_backing_mode = "all-except-melody"
        # Guided Take adjustments are branch-specific. Assisted Studio must not
        # inherit hidden lead/backing gain values from a previous Guided Trial.
        self.studio.pair_lead_gain_percent = 100
        self.studio.pair_backing_gain_percent = 100
        signature_choice = self.signature_combo.currentText()
        self.studio.preserve_signature_assets = not signature_choice.startswith("Do not")
        self.studio.signature_source = "auto"
        self.studio.signature_placement = "intro-and-outro" if "outro" in signature_choice.casefold() else ("none" if signature_choice.startswith("Do not") else "intro-alone")
        self.studio.settings_store.set("guidance_mode", mode_name)
        self.studio.session_workspace.set_guidance_mode(mode_name)
        self.studio.project.guidance_mode = mode_name
        self._apply_section_plan_to_studio()
        self.studio._add_source_paths([profile.path for profile in plan.profiles], replace=True)
        role_map = {
            "lead melody": "melody",
            "foundation": "foundation",
            "section / texture": "section",
            "supporting section": "section",
        }
        specs: list[SourceSpec] = []
        for profile, role_label in zip(plan.profiles, plan.source_roles):
            # Never reduce a song to its instantaneous top note by default.
            # Piano reductions remain intact and are used as whole sections unless
            # the musician explicitly chooses voice extraction in Creative Workbench.
            specs.append(SourceSpec(path=profile.path, role=role_map.get(role_label, "auto"), melody_treatment="full"))
        self.studio.source_specs = specs
        self.studio._refresh_source_set()
        bpm, target_pc, target_mode, bars, section_bars, mode_handling, auto_balance = self._resolved_frame()
        self.studio.target_bpm.setValue(bpm)
        target_index = self.studio.target_key.findData(target_pc)
        if target_index >= 0:
            self.studio.target_key.setCurrentIndex(target_index)
        mode_index = self.studio.target_mode.findData(target_mode)
        if mode_index >= 0:
            self.studio.target_mode.setCurrentIndex(mode_index)
        self.studio.length_mode.setCurrentText("Custom bars")
        self.studio.bars_spin.setValue(bars)
        self.studio.section_bars.setValue(section_bars)
        self.studio.mode_handling.setCurrentText(mode_handling)
        self.studio.auto_balance.setChecked(auto_balance)
        self.studio.variations.setValue(self.variation_spin.value())
        self.studio._populate_recipe_table()
        recipes = self._selected_recipes()
        for button in self.studio.recipe_checks.values():
            button.setChecked(False)
        prefix = PAIR_PREFIX if plan.source_count == 2 else MULTI_PREFIX
        for recipe in recipes:
            button = self.studio.recipe_checks.get(f"{prefix}{recipe}")
            if button and button.isEnabled():
                button.setChecked(True)
        return recipes

    def _generate(self) -> None:
        try:
            # The next Trial belongs to the currently displayed branch. Clear
            # the active Take/playback reference while preserving older Trials.
            self.studio.clear_active_take_for_new_branch()
            self.selected_take = None
            self.generated_takes = []
            recipes = self._prepare_parent()
            if not recipes:
                QMessageBox.information(self, self.workflow_title, "No eligible arrangement remains for the current broad direction. Choose another direction or group of songs.")
                return
            before = {take_identity_key(take) for take in self.studio.takes}
            self.studio.generate_takes()
            self.generated_takes = [take for take in self.studio.takes if take_identity_key(take) not in before]
            self._generated_branch_key = self._current_branch_key()
            self._populate_results()
            self._show_page(4)
        except Exception as exc:
            self._report_error("Trial generation", exc)

    def _populate_results(self) -> None:
        self._clear_layout(self.results_layout)
        if not self.generated_takes:
            self.result_summary.setText("No Takes were generated. Earlier work is unchanged; go Back and choose a section-safe direction or another recommended song combination.")
            return
        trials = {take.trial_id for take in self.generated_takes}
        chosen = self.selected_take.name if self.selected_take else "none yet"
        revision_count = sum(1 for item in self.generated_takes if item.parent_take_id)
        self.result_summary.setText(
            f"Available: {len(self.generated_takes)} Take(s) in {len(trials)} Trial(s), including {revision_count} section revision(s). "
            f"A is the parent Take; B/C change only the named section. Listening does not change the choice. Chosen Take: {chosen}."
        )
        set_action_enabled(
            self.results_workbench_button,
            self.selected_take is not None,
            "Choose a Take before continuing to Creative Workbench.",
        )
        for button, reason in (
            (self.results_performance_button, "Choose a Take before applying Performance Polish."),
            (self.results_export_button, "Choose a Take before exporting."),
        ):
            set_action_enabled(button, self.selected_take is not None, reason)
        self.results_workbench_button.setText(
            "Continue with Chosen Take →" if self.selected_take is not None else "Choose a Take to Continue"
        )
        baseline_take = self.generated_takes[0]
        for index, take in enumerate(self.generated_takes):
            chosen_take = bool(self.selected_take and take_identity_key(self.selected_take) == take_identity_key(take))
            card = QGroupBox(f"{take.trial_name or take.trial_id} · {take.name}{' · CHOSEN' if chosen_take else ''}")
            layout = QVBoxLayout(card)
            decision_lines = list(take.decision_summary or ())
            visible_lines = decision_lines[:4]
            slot = take.comparison_slot if take.parent_take_id else ("A" if index == 0 else f"A{index + 1}")
            lineage = ""
            if take.parent_take_id:
                lineage = (
                    f" · Parent {take.parent_take_id} · Changed: {', '.join(take.changed_sections) or 'selected section'}"
                    f" · Reason: {take.refinement_reason or 'selective refinement'}"
                )
            revision_kind = "performance revision" if take.refinement_reason == "Performance polish" else "section revision"
            comparison_label = f"{slot} · {revision_kind if take.parent_take_id else 'baseline'}"
            info = QLabel(
                f"{comparison_label}{lineage} · Recipe: {take.recipe or take.kind} · Target: {take.target_key} · {take.target_bpm} felt BPM · {take.bars} bars\n"
                + ("\n".join(visible_lines) if visible_lines else "No protected signature opening was added to this Take.")
            )
            info.setWordWrap(True)
            details = QLabel("\n".join(decision_lines[4:]))
            details.setObjectName("DetailsPanel")
            details.setWordWrap(True)
            details.setVisible(False)
            buttons = QHBoxLayout()
            play = QPushButton("Play Take")
            play.setObjectName("PlayButton")
            play.clicked.connect(lambda _checked=False, t=take: self._play_take(t))
            approve = QPushButton("Chosen ✓" if chosen_take else "Choose This Take")
            approve.setObjectName("PrimaryButton")
            approve.clicked.connect(lambda _checked=False, t=take: self._choose_take(t))
            delete = QPushButton("Delete Take")
            delete.setObjectName("DangerButton")
            delete.clicked.connect(lambda _checked=False, t=take: self._delete_take(t))
            buttons.addWidget(play)
            if take.parent_take_id:
                changed = QPushButton("Play Changed Section")
                changed.setObjectName("PlayButton")
                changed.clicked.connect(lambda _checked=False, t=take: self._play_changed_section(t))
                buttons.addWidget(changed)
                parent_take = self._parent_take_for(take)
                if parent_take is not None:
                    play_parent = QPushButton("Play Parent Section")
                    play_parent.setObjectName("UtilityButton")
                    play_parent.clicked.connect(lambda _checked=False, t=parent_take, r=take: self._play_parent_section(t, r))
                    buttons.addWidget(play_parent)
            elif index > 0:
                play_a = QPushButton("Play A Baseline")
                play_a.setObjectName("UtilityButton")
                play_a.setToolTip(f"Play the first Take for direct A/B listening: {baseline_take.name}")
                play_a.clicked.connect(lambda _checked=False, t=baseline_take: self._play_take(t))
                buttons.addWidget(play_a)
            refine = QPushButton("Refine One Section…")
            refine.setObjectName("UtilityButton")
            refine.clicked.connect(lambda _checked=False, t=take: self._refine_take_section(t))
            buttons.addWidget(refine)
            buttons.addWidget(approve)
            if len(decision_lines) > 4:
                show_details = QPushButton("Show Details ▾")
                show_details.setObjectName("UtilityButton")
                show_details.clicked.connect(
                    lambda _checked=False, b=show_details, d=details: self._toggle_plan_notes(b, d)
                )
                buttons.addWidget(show_details)
            buttons.addWidget(delete)
            buttons.addStretch(1)
            layout.addWidget(info)
            layout.addWidget(details)
            layout.addLayout(buttons)
            self.results_layout.addWidget(card, index // 2, index % 2)
        final_row = (len(self.generated_takes) + 1) // 2
        self.results_layout.setRowStretch(final_row, 1)

    # ------------------------------------------------------------ playback
    def _play_profile(self, profile) -> None:
        self.studio._play_path(profile.path, f"Song: {profile.title}", f"assisted-{Path(profile.path).stem}")

    def _play_take(self, take: "RemixTake") -> None:
        # Auditioning is intentionally separate from choosing. Otherwise the
        # last item heard can silently replace the Take intended for handoff.
        self.studio._play_path(take.path, take.name, "assisted::" + "::".join(take_identity_key(take)))

    def _choose_take(self, take: "RemixTake") -> None:
        branch_ids = {take_identity_key(item) for item in self.generated_takes}
        chosen_key = take_identity_key(take)
        updated_takes: list["RemixTake"] = []
        chosen = None
        for item in self.studio.takes:
            item_key = take_identity_key(item)
            if item_key in branch_ids:
                item = replace(
                    item,
                    approved=item_key == chosen_key,
                    status="approved" if item_key == chosen_key else "candidate",
                )
            updated_takes.append(item)
            if take_identity_key(item) == chosen_key:
                chosen = item
        self.studio.takes = updated_takes
        updated_by_key = {take_identity_key(item): item for item in updated_takes}
        self.generated_takes = [updated_by_key[take_identity_key(old)] for old in self.generated_takes]
        self.studio.project.set_takes(self.studio.takes)
        if chosen is not None:
            self.studio.stop_playback()
            self.selected_take = chosen
            self.studio.select_exact_take(chosen)
        self._populate_results()
        self._show_page(4)

    def _performance_polish_selected(self) -> None:
        take = self.selected_take
        if take is None:
            QMessageBox.information(self, "Performance Polish", "Choose a Take before applying Performance Polish.")
            return
        self.studio.select_exact_take(take)
        before = take_identity_key(take)
        self.studio.open_performance_polish()
        created = self.studio.selected_take
        if created is None or take_identity_key(created) == before:
            return
        if all(take_identity_key(item) != take_identity_key(created) for item in self.generated_takes):
            self.generated_takes.append(created)
        self.selected_take = created
        self._populate_results()
        self._show_page(4)

    def _delete_take(self, take: "RemixTake") -> None:
        chosen_id = take_identity_key(self.selected_take) if self.selected_take is not None else None
        delete_key = take_identity_key(take)
        self.studio.selected_take = take
        self.studio.takes_table.clearSelection()
        # Reuse the session-safe delete/Undo path by selecting the matching row.
        self.studio._refresh_takes()
        for row in range(self.studio.takes_table.rowCount()):
            item = self.studio.takes_table.item(row, 0)
            raw_key = item.data(Qt.ItemDataRole.UserRole) if item else None
            row_key = tuple(str(value) for value in raw_key) if isinstance(raw_key, (tuple, list)) and len(raw_key) == 3 else None
            if row_key == delete_key:
                self.studio.takes_table.selectRow(row)
                break
        self.studio.delete_selected_takes()
        self.generated_takes = [item for item in self.generated_takes if take_identity_key(item) != delete_key]
        if chosen_id == delete_key:
            self.selected_take = None
            self.studio.clear_active_take_for_new_branch()
        elif chosen_id is not None:
            chosen = next((item for item in self.studio.takes if take_identity_key(item) == chosen_id), None)
            self.selected_take = chosen
            if chosen is not None:
                self.studio.select_exact_take(chosen)
        self._populate_results()
        self._show_page(4)

    def _transport_play_or_resume(self) -> None:
        engine = self.studio.playback_engine
        if engine.is_playing():
            self.studio.pause_or_resume_current()
            return
        if engine.is_paused():
            self.studio.pause_or_resume_current()
            return
        path = self.studio.current_playback_path
        if path and Path(path).exists():
            self.studio._play_path(path, self.studio.current_playback_label or Path(path).stem, self.studio.current_playback_key or f"assisted-replay-{Path(path).stem}")

    def _sync_transport(self) -> None:
        engine = self.studio.playback_engine
        playing = engine.is_playing()
        paused = engine.is_paused()
        has_current = bool(self.studio.current_playback_path and Path(self.studio.current_playback_path).exists())
        self.transport_play.setEnabled(playing or paused or has_current)
        self.transport_play.setText("Pause" if playing else ("Resume" if paused else "Play"))
        self.transport_stop.setEnabled(playing or paused)
        self.transport_stop.setProperty("transportState", "active" if playing or paused else "idle")
        for button in (self.transport_play, self.transport_stop):
            button.style().unpolish(button)
            button.style().polish(button)
        if playing:
            self.transport_status.setText(f"Playing: {self.studio.current_playback_label or 'MIDI'}")
        elif paused:
            self.transport_status.setText(f"Paused: {self.studio.current_playback_label or 'MIDI'}")
        else:
            self.transport_status.setText("Ready" if not has_current else f"Ready to replay: {self.studio.current_playback_label or Path(self.studio.current_playback_path).stem}")
        volume = self.studio.volume_slider.value()
        if self.transport_volume.value() != volume:
            self.transport_volume.blockSignals(True)
            self.transport_volume.setValue(volume)
            self.transport_volume.blockSignals(False)
            self.transport_volume_value.setText(f"{volume}%")

    def _volume_changed(self, value: int) -> None:
        self.transport_volume_value.setText(f"{value}%")
        if self.studio.volume_slider.value() != value:
            self.studio.volume_slider.setValue(value)

    # ----------------------------------------------------------- navigation
    def _show_page(self, index: int) -> None:
        # Re-resolve the authoritative SoundFont each time the embedded studio
        # changes pages. This keeps the persistent header in sync even when the
        # instrument library was chosen from the main Settings menu.
        self._refresh_readiness()
        self.current_page = max(0, min(4, index))
        self.pages.setCurrentIndex(self.current_page)
        labels = ("Prepare", "Discover & Audition", "Shape", "Review Plan", "Listen")
        self.progress.setText(f"Step {self.current_page + 1} of 5 · {labels[self.current_page]}")
        self.back.setText("← Change Working Mode" if self.current_page == 0 else "← Back")
        set_action_enabled(
            self.back,
            True,
            "Return to working-mode selection without deleting songs, saved ideas, Trials, or Takes."
            if self.current_page == 0 else
            "Return one Assisted Studio step without discarding earlier work.",
        )
        if self.current_page == 0:
            if self.fusion_only:
                family_count = len(self._fusion_family_members())
                profile = self.studio.profiles.get(self._selected_anchor_path()) if self._selected_anchor_path() else None
                self.next.setText(f"Find Fusion Versions for {profile.title} →" if profile is not None else "Find Version Fusion Candidates →")
                if hasattr(self, "use_library"):
                    self.use_library.setText(f"Find Fusion Versions for {profile.title}" if profile is not None else "Find Strong Fusion Candidates")
                set_action_enabled(self.next, family_count >= 1, "Version Fusion needs at least one same-song family with two catalogued files.")
                self.hint.setText(
                    "Choose a song from the saved Library, or search strong version families. Exact copies and tempo-only exports are hidden; instrumentally different versions remain eligible."
                )
            else:
                reusable = bool(
                    getattr(self, "_cached_discovery_result", None) is not None
                    and getattr(self, "_cached_discovery_key", None) == self._discovery_cache_key()
                )
                self.next.setText("Return to Mash Ideas →" if reusable else "Discover Mash Ideas →")
                if hasattr(self, "use_library"):
                    self.use_library.setText("More Different Ideas" if reusable else "Discover Mash Ideas")
                ready = self.studio.assisted_library_summary_cached()["eligible"] >= 2
                set_action_enabled(self.next, ready, "At least two usable songs are required before recommendations can be created.")
                self.hint.setText(
                    "Your previous mash ideas are still available; Next returns to them without searching again."
                    if reusable else
                    "Choose an anchor song, then discover distinct cross-song ideas and separate arrangement variants."
                )
        elif self.current_page == 1:
            self.next.setText("Choose an idea above")
            set_action_enabled(self.next, False, "Choose one recommended song set above.")
            self.hint.setText(
                "Preview the alternate arrangements, then choose one materially different pair to fuse."
                if self.fusion_only else
                "Preview 8 or 16 bars, save promising ideas, compare them, then choose one to shape."
            )
        elif self.current_page == 2:
            self.next.setText("Review Engine Plan →")
            set_action_enabled(self.next, self.selected_plan is not None, "Choose a recommended song set first.")
            self.hint.setText("Choose only the broad artistic direction; the engine fills the technical preparation.")
        elif self.current_page == 3:
            self.next.setText("Generate Assisted Trial →")
            ready = self.selected_plan is not None and bool(self._selected_recipes())
            set_action_enabled(self.next, ready, "Choose a plan with at least one eligible recipe.")
            self.hint.setText("Review exact key, tempo, role, dynamics, and recipe decisions before generation.")
        else:
            self.next.setText("Continue with Chosen Take →")
            current_ids = {take_identity_key(take) for take in self.generated_takes}
            ready = self.selected_take is not None and take_identity_key(self.selected_take) in current_ids
            set_action_enabled(self.next, ready, "Listen, then choose one Take from the current Assisted Trial.")
            self.hint.setText("Listening does not change the choice. Choose the exact Take you want before continuing.")

    def _go_back(self) -> None:
        if self.current_page == 0:
            self.studio.change_working_mode(source_wizard=self)
            return
        if self.current_page >= 4:
            self.studio.stop_playback()
        self._show_page(self.current_page - 1)

    def _go_next(self) -> None:
        if self.current_page == 0:
            self._find_recommendations()
        elif self.current_page == 2:
            self._review_plan()
        elif self.current_page == 3:
            self._generate()
        elif self.current_page == 4:
            if self.selected_take is None:
                QMessageBox.information(self, self.workflow_title, "Choose a Take before continuing to Creative Workbench.")
                return
            try:
                self.studio.open_take_in_workbench(self.selected_take, destination=10)
            except Exception as exc:
                self._report_error("Chosen Take handoff", exc)
                return
            self.accept()

    def _show_measurement_help(self) -> None:
        QMessageBox.information(
            self,
            f"How {self.workflow_title} uses measurements",
            "Preparation estimates the amount of key, tempo, meter, role, length, and harmonic work.\n\n"
            "Harmony estimates bar-level functional overlap after proposed key alignment.\n\n"
            "Directional fit separately tests one song's melody over the other song's backing.\n\n"
            "Tempo includes direct, half-time, and double-time relationships. Meter estimates bar and phrase-grid alignment.\n\n"
            "Orchestration estimates how clearly melody, foundation, bass, drums, and other roles are separated.\n\n"
            "Strong / Good / Workable / Exploratory describe preparation confidence—not artistic quality.",
        )

    def _more_control(self, *, keep_results: bool = False, use_plan: bool = True) -> None:
        if keep_results:
            if self.selected_take is None:
                QMessageBox.information(
                    self, self.workflow_title,
                    "Choose the exact Take you want before continuing to Creative Workbench.",
                )
                return
            try:
                self.studio.open_take_in_workbench(self.selected_take, destination=10)
            except Exception as exc:
                self._report_error("Chosen Take handoff", exc)
                return
            self.accept()
            return

        try:
            if use_plan and self.selected_plan is not None:
                self._prepare_parent()
                destination = 7 if self.current_page >= 3 else 6
            else:
                destination = 2
            self.studio.choose_guidance_mode("workbench")
            self.studio.navigate_to(destination, force=True)
        except Exception as exc:
            self._report_error("Creative Workbench handoff", exc)
            return
        self.accept()
