from __future__ import annotations

from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QApplication, QDialog, QFrame, QGroupBox, QHBoxLayout, QLabel, QMessageBox,
    QPlainTextEdit, QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget,
    QInputDialog, QProgressBar, QSlider, QGridLayout, QSizePolicy, QSplitter,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
)

from core.guided_beginner import (
    GuidedNearMatch, GuidedPairPlan, guided_library_diagnostics,
    guided_library_summary,
    guided_source_gate, score_band, score_help,
)
from core.listening_review import ListeningReview
from core.remix_guidance import GUIDANCE_MODES
from core.remix_engine import take_identity_key, write_signature_preview
from app.studio_window import PAIR_PREFIX
from app.guided_adjustment_dialog import GuidedTakeAdjustmentDialog
from app.controllers.workflow_state import WorkflowBranchKey
from app.theme import load_stylesheet
from app.button_policy import install_button_policy, set_action_enabled
from app.deferred_actions import cancel_owned, schedule_owned

from app.guided_pages_mixin import GuidedWizardPagesMixin
from app.guided_refinement_mixin import GuidedWizardRefinementMixin
from app.guided_discovery_mixin import GuidedWizardDiscoveryMixin
from app.guided_activity_mixin import GuidedWizardActivityMixin

if TYPE_CHECKING:
    from app.decision_workflow import DecisionWorkflowStudio
    from core.remix_engine import RemixTake


class GuidedRemixWizard(GuidedWizardActivityMixin, GuidedWizardRefinementMixin, GuidedWizardDiscoveryMixin, GuidedWizardPagesMixin, QDialog):
    """Strict first-mash wizard.

    This is deliberately not the Creative Workbench with fields prefilled. It
    hides risky source material, prevalidates pair/recipe combinations, makes
    the musical decisions, explains them in ordinary language, and produces a
    small set of auditionable Trials.
    """

    def __init__(self, studio: "DecisionWorkflowStudio") -> None:
        # Embed the beginner wizard inside the main application window. This
        # avoids two competing windows, two apparent application states, and a
        # wizard that can become hidden behind the workbench.
        super().__init__(studio.centralWidget())
        self.setWindowFlags(Qt.WindowType.Widget)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setModal(False)
        self.studio = studio
        if self.parentWidget() is not None:
            self.parentWidget().installEventFilter(self)
        self.recommendations: tuple[GuidedPairPlan, ...] = ()
        self.near_matches: tuple[GuidedNearMatch, ...] = ()
        self.recommendation_rows: list[GuidedPairPlan | GuidedNearMatch] = []
        self.selected_plan: GuidedPairPlan | None = None
        self.selected_recommendation_index = -1
        self.generated_takes: list["RemixTake"] = []
        self.selected_take: "RemixTake | None" = None
        self._active_branch_key: WorkflowBranchKey | None = None
        self._generated_branch_key: WorkflowBranchKey | None = None
        self.lead_gain_percent = 100
        self.backing_gain_percent = 100
        self.preserve_signature_intro = True
        self.current_page = 0
        self._guided_discovery_operation_name = f"guided-discovery-{id(self)}"
        self._guided_discovery_cancel_requested = False
        self._guided_reservoir_offset = 0
        # Session-wide composition history. "More Different Mash Ideas" must
        # rotate actual songs, not merely filenames or pair row IDs. Keep a
        # bounded set of prior source paths so the prepared-result reader can
        # apply the deep-profile same-composition guard even when legacy Engine
        # family IDs were too literal.
        self._guided_seen_profile_paths: list[str] = []
        # Lightweight normalized composition identities persist for the whole
        # Guided wizard session so repeated "More Different" requests do not
        # recycle a song merely because its previous deep profile aged out of
        # the bounded fuzzy-comparison window.
        self._guided_seen_family_keys: set[str] = set()
        self._guided_activity_started_at = 0.0
        self.setWindowTitle("Guided Remix — First Mash Wizard")
        self.resize(980, 660)
        self.setMinimumSize(720, 500)
        self._build_ui()
        self._apply_wizard_style()
        install_button_policy(self)
        self.guided_activity_timer = QTimer(self)
        self.guided_activity_timer.setInterval(750)
        self.guided_activity_timer.timeout.connect(self._refresh_guided_activity_heartbeat)
        self.transport_timer = QTimer(self)
        self.transport_timer.setInterval(180)
        self.transport_timer.timeout.connect(self._sync_transport)
        self.transport_timer.start()
        self.finished.connect(lambda _result: self.transport_timer.stop())
        self.studio.library_scan_bridge.deepState.connect(self._deep_scan_status_update)
        self.finished.connect(lambda _result: self._detach_background_callbacks())
        self.finished.connect(lambda _result: self.studio.operation_manager.cancel(self._guided_discovery_operation_name))
        self._sync_transport()
        self._fit_to_screen()
        self._refresh_readiness()
        self._show_page(0)


    def _play_chosen_take(self) -> None:
        take = self.selected_take or self.studio.selected_take
        if take is not None:
            self._play_take(take)

    def _performance_polish_chosen(self) -> None:
        take = self.selected_take or self.studio.selected_take
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
        self._refresh_finish_page()

    def _open_advanced_instrumentation(self) -> None:
        take = self.selected_take or self.studio.selected_take
        if take is None:
            QMessageBox.information(self, "Customize Take", "Choose a Take before opening Creative Workbench.")
            return
        try:
            self.studio.open_take_in_workbench(take, destination=11)
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Chosen Take could not be opened",
                "The Guided Remix and all earlier Trials remain safe. Choose the Take again and retry.\n\n"
                f"{type(exc).__name__}: {exc}",
            )
            return
        self.accept()

    def _refresh_readiness(self) -> None:
        summary = self.studio.guided_library_summary_cached()
        soundfont_path = self.studio.resolved_soundfont_path()
        fluidsynth_path = self.studio.resolved_fluidsynth_path()
        ready = bool(soundfont_path and fluidsynth_path)
        self.audio_header.setText("Audio ready" if ready else self.studio.audio_readiness_message())
        self.audio_header.setObjectName("HeaderStatus" if ready else "HeaderWarning")
        self.audio_header.style().unpolish(self.audio_header)
        self.audio_header.style().polish(self.audio_header)
        self.audio_line.setText(f"✓ FluidSynth: {Path(fluidsynth_path).name}" if fluidsynth_path else "✕ FluidSynth: not found")
        self.instrument_line.setText(f"✓ Instrument Library: {Path(soundfont_path).name}" if soundfont_path else "✕ Instrument Library: no SoundFont selected")
        self.guided_fluidsynth_button.setVisible(not bool(fluidsynth_path))
        self.guided_soundfont_button.setVisible(not bool(soundfont_path))
        self.guided_audio_panel.setProperty("ready", ready)
        self.guided_audio_panel.style().unpolish(self.guided_audio_panel)
        self.guided_audio_panel.style().polish(self.guided_audio_panel)
        # Once audio is ready, repeating the whole setup card wastes the first
        # beginner screen. Keep only the compact header status.
        self.guided_audio_panel.setVisible(not ready)
        self.guided_audio_panel.setMaximumHeight(0 if ready else 16777215)
        self.guided_audio_panel.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Ignored if ready else QSizePolicy.Policy.Preferred,
        )
        self.guided_audio_panel.updateGeometry()
        if isinstance(self.prepare_heading, QLabel) and isinstance(self.prepare_subtitle, QLabel):
            if ready:
                self.prepare_heading.setText("Find a good first mash")
                self.prepare_subtitle.setText(
                    "Your audio and authoritative MIDI Library are ready. Open prepared mash ideas now, or use MIDI Library Manager when you need different Library content."
                )
                self.prepare_next_panel.setText(
                    "Next: MIDI Remixer will open prepared ideas between distinct songs, explain the strongest combinations, and let you audition both originals before choosing."
                )
            else:
                self.prepare_heading.setText("Get audio ready, then find a good first mash")
                self.prepare_subtitle.setText(
                    "FluidSynth and a SoundFont must be ready before a beginner can audition songs and mash ideas."
                )
                self.prepare_next_panel.setText(
                    "Open MIDI Library Manager whenever the Library must be created, recovered, scanned, or changed. FluidSynth and a SoundFont are required only before auditioning and generating the mash."
                )
        self.library_line.setText(
            f"MIDI library: {summary['total']} files · about {summary['distinct_songs']} distinct songs · {summary['duplicate_variants']} alternate/duplicate versions grouped"
        )
        self.gate_line.setText(
            f"Songs ready for Guided Remix: {summary['guided_ready_songs']} · files needing review: {summary['hidden']}"
        )
        can_search = ready and summary["guided_ready_songs"] >= 2
        self.use_library.setEnabled(can_search)
        configured_library = bool(self.studio._normalised_library_paths())
        self.choose_folder_button.setEnabled(not self.studio._classification_in_progress)
        self.choose_folder_button.setText("Open MIDI Library Manager…")
        self.choose_folder_button.setObjectName("WizardUtilityButton" if configured_library else "WizardPrimaryChoice")
        self.choose_folder_button.style().unpolish(self.choose_folder_button)
        self.choose_folder_button.style().polish(self.choose_folder_button)
        if not ready:
            self.hint.setText(self.studio.audio_readiness_message())
        elif summary["guided_ready_songs"] < 2:
            self.hint.setText(
                "Guided Remix needs at least two Library-approved Mash Ready songs. "
                "Open MIDI Library Manager to add, analyze, or review Library files."
            )
        else:
            self.hint.setText("Audio and songs are ready. Open a dependable prepared mash idea.")
        self._refresh_deep_scan_progress()
        self._show_page(self.current_page)

    def _show_scan_policy_choice(self, summary_text: str) -> None:
        self._refresh_readiness()
        self.scan_policy_row.setVisible(False)
        self.deep_control_row.setVisible(False)
        self.scan_frame.setVisible(True)
        self.scan_progress.setVisible(False)
        self.scan_status.setText(
            summary_text + "\nDetailed-analysis policy is controlled by MIDI Library Manager. Remixer observes committed eligibility automatically."
        )

    def _apply_scan_policy(self, value: str) -> None:
        self.scan_policy_row.setVisible(False)
        self.deep_control_row.setVisible(False)
        self.studio._open_library_manager()

    def _deep_scan_status_update(self, path: str, state: str) -> None:
        if not self.isVisible():
            return
        # A completed deep profile can immediately make another song eligible
        # for Guided Remix, so refresh the visible ready count and button state.
        if state in {"complete", "failed"}:
            self._refresh_readiness()
        else:
            self._refresh_deep_scan_progress(path, state)

    def _refresh_deep_scan_progress(self, path: str = "", state: str = "") -> None:
        snapshot = self.studio.deep_analysis_progress()
        total = int(snapshot.get("total", 0) or 0)
        finished = int(snapshot.get("finished", 0) or 0)
        remaining = int(snapshot.get("remaining", 0) or 0)
        self.scan_policy_row.setVisible(False)
        self.deep_control_row.setVisible(False)
        if total <= 0:
            self.analysis_header.setVisible(False)
            return
        self.analysis_header.setText(
            "Library detailed analysis complete" if remaining <= 0 else f"Library detailed analysis: {finished}/{total}"
        )
        self.analysis_header.setVisible(True)

    def _choose_folder(self) -> None:
        self.scan_policy_row.setVisible(False)
        self.studio._open_library_manager()
        self._refresh_readiness()

    def _add_files(self) -> None:
        self.studio._open_library_manager()
        self._refresh_readiness()

    def _choose_soundfont(self) -> None:
        self.studio.choose_soundfont()
        self._refresh_readiness()

    def _setup_audio(self) -> None:
        self.studio.show_audio_setup()
        self._refresh_readiness()

    def _clear_layout(self, layout: QVBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
            child = item.layout()
            if child:
                self._clear_layout(child)  # type: ignore[arg-type]

    def _populate_recommendations(self) -> None:
        self._clear_layout(self.recommendation_layout)
        self.recommendation_rows = list(self.recommendations or self.near_matches)
        diagnostics = self.studio.guided_library_diagnostics_cached()
        if self.recommendations:
            creative = sum(1 for plan in self.recommendations if plan.category == "Creative section-switch")
            note = f" {creative} are clearly labeled creative section-switch experiments." if creative else ""
            self.recommendation_summary.setText(
                f"{diagnostics['total_files']} MIDI files represent about {diagnostics['distinct_songs']} distinct songs. "
                f"{diagnostics['duplicate_variants']} alternate or duplicate versions were grouped, and {len(self.recommendations)} different mash ideas are shown below.{note}"
            )
        else:
            exclusions = diagnostics.get("exclusions", {})
            top_reason = max(exclusions, key=exclusions.get) if exclusions else "The available songs need more preparation"
            self.recommendation_summary.setText(
                f"No beginner-ready pairing was found. The most common issue was: {top_reason}. "
                "The closest combinations are shown only so you can understand what needs review."
            )

        for index, item in enumerate(self.recommendation_rows):
            card = QFrame()
            card.setObjectName("MashIdeaCard")
            card.setProperty("creative", isinstance(item, GuidedPairPlan) and item.category == "Creative section-switch")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(12, 9, 12, 9)
            card_layout.setSpacing(6)

            title_row = QHBoxLayout()
            if isinstance(item, GuidedPairPlan):
                title = QLabel(f"Idea {index + 1} · {item.headline}")
                title.setObjectName("CardTitle")
                badge_text = "Creative option" if item.category == "Creative section-switch" else ("Best starting point" if index == 0 else item.category)
                badge = QLabel(badge_text)
                badge.setObjectName("IdeaBadge")
                title_row.addWidget(title, 1)
                title_row.addWidget(badge)
                summary = QLabel(self._plain_pair_summary(item))
                summary.setWordWrap(True)
                evidence_line = (
                    "\n• Reference evidence: " + ", ".join(item.assessment.reference_sources)
                    if item.assessment.reference_sources else ""
                )
                facts = QLabel(
                    f"• {item.source_a.meter} meter · target near {item.target_bpm} BPM\n"
                    f"• {len(item.recipe_keys)} arrangement version{'s' if len(item.recipe_keys) != 1 else ''} available\n"
                    "• Original MIDI files will not be changed"
                    + evidence_line
                )
            else:
                title = QLabel(f"Closest result · {item.source_a.title} + {item.source_b.title}")
                title.setObjectName("CardTitle")
                badge = QLabel("Needs review")
                badge.setObjectName("IdeaBadge")
                title_row.addWidget(title, 1)
                title_row.addWidget(badge)
                summary = QLabel(item.blockers[0] if item.blockers else "This combination needs manual preparation.")
                summary.setWordWrap(True)
                facts = QLabel("Open Assisted Studio to correct the uncertain timing, harmony, or musical roles.")
            facts.setWordWrap(True)
            facts.setObjectName("Subtitle")
            card_layout.addLayout(title_row)
            card_layout.addWidget(summary)
            card_layout.addWidget(facts)

            details = QLabel()
            details.setWordWrap(True)
            details.setObjectName("DetailsPanel")
            details.setVisible(False)
            if isinstance(item, GuidedPairPlan):
                details.setText(
                    "Why MIDI Remixer suggested it\n" + "\n".join(f"• {line}" for line in item.reasons)
                    + "\n\nWhat each song provides\n" + "\n".join(f"• {line}" for line in item.contribution_plan)
                    + "\n\nWhat will change\n" + "\n".join(f"• {line}" for line in item.transformations)
                )
            else:
                details.setText("Why it needs review\n" + "\n".join(f"• {line}" for line in item.blockers))

            buttons = QHBoxLayout()
            play_a = QPushButton("Play First Song")
            play_a.setObjectName("PlayButton")
            play_a.clicked.connect(lambda _checked=False, p=item.source_a: self._play_profile(p))
            play_b = QPushButton("Play Second Song")
            play_b.setObjectName("PlayButton")
            play_b.clicked.connect(lambda _checked=False, p=item.source_b: self._play_profile(p))
            why = QPushButton("Why this works ▾")
            why.setObjectName("UtilityButton")
            why.clicked.connect(lambda _checked=False, b=why, d=details: self._toggle_details(b, d))
            ignore = QPushButton("Ignore This Pair")
            ignore.setObjectName("UtilityButton")
            ignore.setToolTip("Hide this song relationship in future Guided and Assisted recommendations. Original MIDI files are not changed.")
            ignore.clicked.connect(lambda _checked=False, p=item: self._ignore_recommendation_pair(p))
            fusion = QPushButton("Try as Version Fusion")
            fusion.setObjectName("UtilityButton")
            fusion.setToolTip(
                "Check whether these two files are alternate arrangements of one song. Useful versions move into Version Fusion; redundant versions are suppressed from both workflows."
            )
            fusion.clicked.connect(lambda _checked=False, p=item: self._try_recommendation_as_fusion(p))
            use = QPushButton("Use This Idea" if isinstance(item, GuidedPairPlan) else "Review in Assisted Studio")
            use.setObjectName("PrimaryButton" if isinstance(item, GuidedPairPlan) else "UtilityButton")
            if isinstance(item, GuidedPairPlan):
                use.clicked.connect(lambda _checked=False, p=item: self._choose_plan(p))
            else:
                use.clicked.connect(lambda: self._switch_mode("assisted"))
            buttons.addWidget(play_a)
            buttons.addWidget(play_b)
            buttons.addWidget(why)
            buttons.addWidget(ignore)
            buttons.addWidget(fusion)
            buttons.addStretch(1)
            buttons.addWidget(use)
            card_layout.addLayout(buttons)
            card_layout.addWidget(details)
            self.recommendation_layout.addWidget(card)

        if not self.recommendation_rows:
            empty = QLabel("Add or analyze more complete MIDI songs, then search again.")
            empty.setObjectName("EmptyState")
            empty.setWordWrap(True)
            self.recommendation_layout.addWidget(empty)
        self.recommendation_layout.addStretch(1)

    def _try_recommendation_as_fusion(self, item: GuidedPairPlan | GuidedNearMatch) -> None:
        outcome = self.studio.route_pair_to_fusion(item.source_a, item.source_b)
        if outcome != "neither":
            return
        self.recommendations = tuple(
            plan for plan in self.recommendations
            if not self.studio.recommendation_pair_suppressed(plan.source_a, plan.source_b)
        )
        self.near_matches = tuple(
            plan for plan in self.near_matches
            if not self.studio.recommendation_pair_suppressed(plan.source_a, plan.source_b)
        )
        self.selected_recommendation_index = -1
        self._populate_recommendations()

    def _ignore_recommendation_pair(self, item: GuidedPairPlan | GuidedNearMatch) -> None:
        self.studio.ignore_mash_pair(item.source_a, item.source_b)
        self.recommendations = tuple(plan for plan in self.recommendations
                                     if not self.studio.recommendation_pair_suppressed(plan.source_a, plan.source_b))
        self.near_matches = tuple(plan for plan in self.near_matches
                                 if not self.studio.recommendation_pair_suppressed(plan.source_a, plan.source_b))
        self.selected_recommendation_index = -1
        self._populate_recommendations()

    def _selected_recommendation_item(self) -> GuidedPairPlan | GuidedNearMatch | None:
        if 0 <= self.selected_recommendation_index < len(self.recommendation_rows):
            return self.recommendation_rows[self.selected_recommendation_index]
        return None

    def _set_idea_buttons(self, item: GuidedPairPlan | GuidedNearMatch | None) -> None:
        return

    def _recommendation_selection_changed(self) -> None:
        return

    def _play_selected_idea_source(self, index: int) -> None:
        item = self._selected_recommendation_item()
        if item is not None:
            self._play_profile(item.source_a if index == 0 else item.source_b)

    def _audition_selected_idea(self) -> None:
        self._play_selected_idea_source(0)

    def _use_selected_idea(self) -> None:
        item = self._selected_recommendation_item()
        if isinstance(item, GuidedPairPlan):
            self._choose_plan(item)

    def _show_readiness_report(self) -> None:
        diagnostics = self.studio.guided_library_diagnostics_cached()
        lines = [
            f"MIDI files analyzed: {diagnostics['total_files']}",
            f"Distinct songs: {diagnostics['distinct_songs']}",
            f"Alternate/duplicate versions grouped: {diagnostics['duplicate_variants']}",
            f"Songs ready for Guided Remix: {diagnostics['guided_ready_songs']}",
            "",
            "Why other files need review:",
        ]
        exclusions = diagnostics.get("exclusions", {})
        if exclusions:
            lines.extend(f"• {label}: {count}" for label, count in sorted(exclusions.items(), key=lambda item: (-item[1], item[0])))
        else:
            lines.append("• Every analyzed song passed the initial readiness check.")
        lines.extend(("", "Song details (sample):"))
        for _path, title, level, summary in diagnostics.get("sample_rows", ())[:20]:
            lines.append(f"• {title}: {level} — {summary}")
        if diagnostics['total_files'] > 20:
            lines.append(f"• …and {diagnostics['total_files'] - 20} more files")
        QMessageBox.information(self, "Song readiness", "\n".join(lines))

    def _plan_branch_key(self, plan: GuidedPairPlan) -> WorkflowBranchKey:
        return WorkflowBranchKey.create(
            (plan.source_a.path, plan.source_b.path),
            target_bpm=plan.target_bpm,
            target_key_pc=plan.target_key_pc,
            target_mode=plan.target_mode,
            bars=plan.bars,
            section_bars=plan.section_bars,
            recipe_keys=plan.recipe_keys[:3],
            options=(
                plan.start_bar_a, plan.start_bar_b, plan.lead_source, plan.backing_source,
                self.lead_gain_percent, self.backing_gain_percent, self.preserve_signature_intro,
            ),
        )

    def _invalidate_active_branch(self) -> None:
        self.studio.clear_active_take_for_new_branch()
        self.selected_take = None
        self.generated_takes = []
        self._generated_branch_key = None
        if hasattr(self, "results_summary"):
            self.results_summary.setText(
                "This is a different mash idea. Create new Takes before continuing; earlier Trials remain available in project history."
            )
        if hasattr(self, "finish_summary"):
            self.finish_summary.setText("Choose a Take from the current mash idea before finishing.")

    def _choose_plan(self, plan: GuidedPairPlan) -> None:
        self.studio.record_mash_feedback((plan.source_a, plan.source_b), "choose")
        branch_key = self._plan_branch_key(plan)
        if self._active_branch_key is not None and branch_key != self._active_branch_key:
            self._invalidate_active_branch()
            self.lead_gain_percent = 100
            self.backing_gain_percent = 100
            self.preserve_signature_intro = True
            branch_key = self._plan_branch_key(plan)
        self._active_branch_key = branch_key
        self.selected_plan = plan
        self.plan_title.setText(plan.headline)
        if plan.category == "Creative section-switch":
            overview = (
                "This is a controlled creative option. The songs will take turns in complete passages, "
                "so each keeps its own melody and chords."
            )
        else:
            lead = plan.source_a if plan.lead_source == "a" else plan.source_b
            backing = plan.source_b if plan.backing_source == "b" else plan.source_a
            overview = (
                f"MIDI Remixer will lead with {lead.title}'s melody and use {backing.title}'s fuller backing. "
                f"It will align them near {plan.target_bpm} BPM and create {len(plan.recipe_keys)} different version{'s' if len(plan.recipe_keys) != 1 else ''}."
            )
        self.plan_overview.setText(overview + " The original MIDI files remain unchanged.")

        self._clear_layout(self.plan_choices_layout)
        for index, recipe in enumerate(plan.recipe_keys[:3], 1):
            title, description = self._friendly_recipe_text(plan, recipe)
            option = QFrame()
            option.setObjectName("PlanOptionCard")
            option_layout = QVBoxLayout(option)
            option_layout.setContentsMargins(9, 7, 9, 7)
            comparison = take.comparison_slot if take.parent_take_id else "A"
            heading = QLabel(f"{comparison} · Take {index} · {title}")
            heading.setObjectName("CardTitle")
            body = QLabel(description)
            body.setWordWrap(True)
            option_layout.addWidget(heading)
            option_layout.addWidget(body)
            self.plan_choices_layout.addWidget(option)

        key_name_text = self._target_key_text(plan)
        lines = [
            "TECHNICAL DETAILS",
            f"• Target key: {key_name_text}",
            f"• Target pulse: {plan.target_bpm} felt BPM",
            f"• Preview length: {plan.bars} bars in {plan.section_bars}-bar phrases",
            "• Dynamics: automatically balance the backing so the lead remains audible",
            "",
            "WHY",
            *(f"• {item}" for item in plan.reasons),
            "",
            "WHAT WILL CHANGE",
            *(f"• {item}" for item in plan.transformations),
        ]
        if plan.signature_source and plan.signature_asset:
            signature_profile = plan.source_a if plan.signature_source == "a" else plan.source_b
            lines.extend((
                "",
                "PROTECTED SIGNATURE MATERIAL",
                f"• Preserve {signature_profile.title}'s {plan.signature_asset.get('label', 'special opening')} before the companion song enters.",
                "• Percussion remains on General MIDI channel 10 and is never transposed.",
            ))
        self.plan_text.setPlainText("\n".join(lines))
        self.plan_text.setVisible(False)
        self.plan_details_button.setText("Why this works ▾")
        self.play_signature.setVisible(bool(plan.signature_source and plan.signature_asset))
        self._show_page(2)

    def _play_signature_preview(self) -> None:
        plan = self.selected_plan
        if not plan or not plan.signature_source or not plan.signature_asset:
            return
        profile = plan.source_a if plan.signature_source == "a" else plan.source_b
        preview_dir = Path(self.studio.session_workspace.root) / "previews"
        preview = preview_dir / f"{Path(profile.path).stem}-signature-opening.mid"
        try:
            write_signature_preview(profile, preview, target_bpm=plan.target_bpm)
            self.studio._play_path(str(preview), f"Protected opening: {profile.title}", f"signature-{Path(profile.path).stem}")
        except Exception as exc:
            QMessageBox.warning(self, "Signature preview", f"The protected opening could not be previewed.\n\n{type(exc).__name__}: {exc}")

    def _target_key_text(self, plan: GuidedPairPlan) -> str:
        if plan.target_key_pc is None:
            return f"{plan.source_a.key_display} (first song reference)"
        names = ("C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B")
        return f"{names[plan.target_key_pc % 12]} {plan.target_mode}"

    def _play_profile(self, profile) -> None:
        self.studio._play_path(profile.path, f"Song: {profile.title}", f"guided-{Path(profile.path).stem}")

    def _play_plan_source(self, index: int) -> None:
        if not self.selected_plan:
            return
        profile = self.selected_plan.source_a if index == 0 else self.selected_plan.source_b
        self._play_profile(profile)

    def _prepare_parent_for_plan(self, plan: GuidedPairPlan) -> None:
        self.studio._add_source_paths([plan.source_a.path, plan.source_b.path], replace=True)
        if len(self.studio.source_specs) >= 2:
            # Guided Remix keeps complete detected tracks. Top/bottom voice
            # extraction is an explicit manual option for merged keyboard parts,
            # never a silent default for multitrack songs.
            self.studio.source_specs[0] = replace(self.studio.source_specs[0], start_bar=plan.start_bar_a, melody_treatment="full")
            self.studio.source_specs[1] = replace(self.studio.source_specs[1], start_bar=plan.start_bar_b, melody_treatment="full")
            self.studio._refresh_source_set()
        self.studio.guidance_mode = "guided"
        self.studio.assisted_section_source_order = ()
        self.studio.assisted_section_labels = ()
        self.studio.assisted_section_decisions = ()
        self.studio.pair_backing_mode = "all-except-melody"
        self.studio.pair_lead_gain_percent = int(self.lead_gain_percent)
        self.studio.pair_backing_gain_percent = int(self.backing_gain_percent)
        self.studio.preserve_signature_assets = bool(self.preserve_signature_intro)
        self.studio.signature_source = (plan.signature_source or "none") if self.preserve_signature_intro else "none"
        self.studio.signature_placement = "intro-alone" if self.preserve_signature_intro else "none"
        self.studio.target_bpm.setValue(plan.target_bpm)
        target_index = self.studio.target_key.findData(plan.target_key_pc)
        if target_index >= 0:
            self.studio.target_key.setCurrentIndex(target_index)
        mode_index = self.studio.target_mode.findData(plan.target_mode)
        if mode_index >= 0:
            self.studio.target_mode.setCurrentIndex(mode_index)
        self.studio.length_mode.setCurrentText("Custom bars")
        self.studio.bars_spin.setValue(plan.bars)
        self.studio.section_bars.setValue(plan.section_bars)
        self.studio.auto_balance.setChecked(True)
        self.studio.mode_handling.setCurrentText("Preserve source mode")
        self.studio.variations.setValue(1)
        self.studio._populate_recipe_table()

    def _generate_trials(self) -> None:
        plan = self.selected_plan
        if not plan:
            return
        # A generation run creates a new active branch. Preserve prior Trials in
        # project history, but never allow their selected Take or playback path
        # to carry into this run.
        self.studio.clear_active_take_for_new_branch()
        self.selected_take = None
        self.generated_takes = []
        before_ids = {take_identity_key(take) for take in self.studio.takes}
        self._prepare_parent_for_plan(plan)
        for recipe in plan.recipe_keys[:3]:
            for button in self.studio.recipe_checks.values():
                button.setChecked(False)
            button = self.studio.recipe_checks.get(f"{PAIR_PREFIX}{recipe}")
            if not button or not button.isEnabled():
                continue
            button.setChecked(True)
            self.studio.generate_takes_embedded()
        self.generated_takes = [take for take in self.studio.takes if take_identity_key(take) not in before_ids]
        self._generated_branch_key = self._plan_branch_key(plan)
        self._populate_results()

    def _populate_results(self) -> None:
        self._clear_layout(self.results_layout)
        if not self.generated_takes:
            self.results_summary.setText(
                "MIDI Remixer could not create a usable Take from this idea. The original files are untouched. Try another mash idea or open Assisted Studio."
            )
            return
        chosen = self.selected_take.name if self.selected_take else "none yet"
        self.results_summary.setText(
            f"Created {len(self.generated_takes)} different Take{'s' if len(self.generated_takes) != 1 else ''}. "
            f"Play each one, then choose your favorite. Chosen Take: {chosen}."
        )
        for index, take in enumerate(self.generated_takes, 1):
            recipe = take.recipe or take.kind
            title, description = self._friendly_recipe_text(self.selected_plan, recipe) if self.selected_plan else (take.name, take.description)
            card = QFrame()
            card.setObjectName("TakeChoiceCard")
            card.setProperty("chosen", bool(self.selected_take and take_identity_key(self.selected_take) == take_identity_key(take)))
            layout = QVBoxLayout(card)
            layout.setContentsMargins(12, 9, 12, 9)
            layout.setSpacing(6)
            heading_row = QHBoxLayout()
            comparison = take.comparison_slot if take.parent_take_id else "A"
            heading = QLabel(f"{comparison} · Take {index} · {title}")
            heading.setObjectName("CardTitle")
            heading_row.addWidget(heading, 1)
            if self.selected_take and take_identity_key(self.selected_take) == take_identity_key(take):
                chosen_label = QLabel("Chosen ✓")
                chosen_label.setObjectName("IdeaBadge")
                heading_row.addWidget(chosen_label)
            body = QLabel(description)
            body.setWordWrap(True)
            revision_note = (
                f" · changed only {', '.join(take.changed_sections)}" if take.parent_take_id and take.changed_sections else ""
            )
            simple = QLabel(
                f"Target: {take.target_key} · {take.target_bpm} BPM · {take.bars} bars · originals unchanged{revision_note}"
            )
            simple.setObjectName("Subtitle")
            simple.setWordWrap(True)
            details = QLabel(
                "Technical details\n"
                f"• Recipe: {take.kind.replace('-', ' ').title()}\n"
                f"• Trial record: {take.trial_name or take.trial_id}\n"
                "• Key and tempo matching were applied only to this generated Take.\n"
                + ("\n".join(f"• {line}" for line in take.decision_summary) if take.decision_summary else "• No additional protected-opening decision was recorded.")
            )
            details.setObjectName("DetailsPanel")
            details.setWordWrap(True)
            details.setVisible(False)
            buttons = QHBoxLayout()
            play = QPushButton("▶ Play This Take")
            play.setObjectName("PlayButton")
            play.clicked.connect(lambda _checked=False, t=take: self._play_take(t))
            keep = QPushButton("Chosen ✓" if self.selected_take and take_identity_key(self.selected_take) == take_identity_key(take) else "Choose This Take")
            keep.setObjectName("PrimaryButton")
            keep.clicked.connect(lambda _checked=False, t=take, b=keep: self._approve_take(t, b))
            revise = QPushButton("Adjust…")
            revise.clicked.connect(lambda _checked=False, t=take: self._revise(t))
            why = QPushButton("Why this works ▾")
            why.setObjectName("UtilityButton")
            why.clicked.connect(lambda _checked=False, b=why, d=details: self._toggle_details(b, d))
            buttons.addWidget(play)
            if take.parent_take_id:
                changed = QPushButton("▶ Play Changed Section")
                changed.setObjectName("PlayButton")
                changed.clicked.connect(lambda _checked=False, t=take: self._guided_play_changed_section(t))
                buttons.addWidget(changed)
                parent_section = QPushButton("Play Parent Section")
                parent_section.setObjectName("UtilityButton")
                parent_section.clicked.connect(lambda _checked=False, t=take: self._guided_play_parent_section(t))
                buttons.addWidget(parent_section)
            buttons.addWidget(keep)
            fix_section = QPushButton("Fix a Section…")
            fix_section.setObjectName("UtilityButton")
            fix_section.clicked.connect(lambda _checked=False, t=take: self._guided_refine_section(t))
            buttons.addWidget(fix_section)
            buttons.addWidget(revise)
            buttons.addWidget(why)
            buttons.addStretch(1)
            layout.addLayout(heading_row)
            layout.addWidget(body)
            layout.addWidget(simple)
            layout.addLayout(buttons)
            layout.addWidget(details)
            self.results_layout.addWidget(card)
        self.results_layout.addStretch(1)

    def _play_take(self, take: "RemixTake") -> None:
        # Listening is not the same as choosing. The user must explicitly press
        # Choose This Take before Guided Remix enables the final Continue action.
        exact_key = take_identity_key(take)
        current = next((item for item in self.studio.takes if take_identity_key(item) == exact_key), None)
        if current is None or not Path(current.path).is_file():
            QMessageBox.warning(
                self,
                "Take is no longer available",
                "This Take could not be found in the current session. Create the Takes for this mash idea again; earlier Trials remain safe.",
            )
            return
        active_ids = {take_identity_key(item) for item in self.generated_takes}
        if take_identity_key(current) not in active_ids:
            QMessageBox.information(
                self,
                "Create Takes for this idea",
                "That Take belongs to an earlier mash idea. Create and play a Take from the combination currently shown.",
            )
            return
        played = self.studio.playback_controller.play_path(
            current.path,
            current.name,
            "guided::" + "::".join(take_identity_key(current)),
        )
        if not played:
            self.results_summary.setText(
                "Playback did not start. The Take remains saved; review the audio message and press Play This Take again."
            )

    def _approve_take(self, take: "RemixTake", button: QPushButton) -> None:
        updated_takes = []
        selected_updated = None
        generated_ids = {take_identity_key(item) for item in self.generated_takes}
        chosen_key = take_identity_key(take)
        for item in self.studio.takes:
            item_key = take_identity_key(item)
            if item_key in generated_ids:
                chosen = item_key == chosen_key
                updated = replace(item, approved=chosen, status="approved" if chosen else "candidate")
            else:
                updated = item
            updated_takes.append(updated)
            if take_identity_key(updated) == chosen_key:
                selected_updated = updated
        self.studio.takes = updated_takes
        updated_by_key = {take_identity_key(item): item for item in updated_takes}
        self.generated_takes = [updated_by_key[take_identity_key(old)] for old in self.generated_takes]
        self.studio.project.set_takes(self.studio.takes)
        if selected_updated is not None:
            self.studio.stop_playback()
            self.selected_take = selected_updated
            self.studio.select_exact_take(selected_updated)
        self._populate_results()
        self._show_page(3)

    def _revise(self, take: "RemixTake | None" = None) -> None:
        """Create a genuinely changed Take from explicit generation inputs.

        Earlier builds mapped broad feedback to flags such as auto-balance that
        were often already enabled. The UI then reported a revision even though
        the generated MIDI was identical. Guided adjustments now edit source
        start bars and role gains directly and regenerate the exact clicked
        recipe.
        """
        plan = self.selected_plan
        if plan is None:
            return
        reviewed_take = take or self.selected_take
        dialog = GuidedTakeAdjustmentDialog(
            plan,
            lead_gain_percent=self.lead_gain_percent,
            backing_gain_percent=self.backing_gain_percent,
            preserve_signature_intro=self.preserve_signature_intro,
            parent=self,
        )
        dialog.setStyleSheet(load_stylesheet("guided"))
        install_button_policy(dialog)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        changed = (
            values.target_bpm != plan.target_bpm
            or values.start_bar_a != plan.start_bar_a
            or values.start_bar_b != plan.start_bar_b
            or values.lead_gain_percent != self.lead_gain_percent
            or values.backing_gain_percent != self.backing_gain_percent
            or values.preserve_signature_intro != self.preserve_signature_intro
        )
        if not changed:
            QMessageBox.information(
                self,
                "No adjustment was made",
                "Change at least one start bar, level, pulse, or protected-opening setting before creating an adjusted Take.",
            )
            return

        issue_codes: list[str] = []
        if values.start_bar_a != plan.start_bar_a or values.start_bar_b != plan.start_bar_b:
            issue_codes.append("timing-alignment")
        if values.backing_gain_percent < self.backing_gain_percent:
            issue_codes.append("balance-backing-loud")
        if values.lead_gain_percent < self.lead_gain_percent:
            issue_codes.append("balance-lead-loud")
        if values.target_bpm < plan.target_bpm:
            issue_codes.append("timing-fast")
        elif values.target_bpm > plan.target_bpm:
            issue_codes.append("timing-slow")
        if not issue_codes:
            issue_codes.append("other-adjustment")

        if reviewed_take is not None:
            self.studio.session_workspace.record_listening_review(
                ListeningReview.create(
                    reviewed_take.take_id,
                    trial_id=reviewed_take.trial_id,
                    take_path=reviewed_take.path,
                    verdict="needs-revision",
                    issues=tuple(issue_codes),
                    branch_signature=self._active_branch_key.signature if self._active_branch_key else "",
                    notes=(
                        f"Lead {values.lead_gain_percent}%; backing {values.backing_gain_percent}%; "
                        f"start bars {values.start_bar_a + 1}/{values.start_bar_b + 1}; "
                        f"target {values.target_bpm} BPM; protected intro {values.preserve_signature_intro}."
                    ),
                )
            )
            self.studio.project.listening_reviews = list(self.studio.session_workspace.manifest.listening_reviews)
            self.studio._session_dirty = True

        recipe = reviewed_take.recipe if reviewed_take and reviewed_take.recipe in plan.recipe_keys else plan.recipe_keys[0]
        adjusted_plan = replace(
            plan,
            target_bpm=values.target_bpm,
            start_bar_a=values.start_bar_a,
            start_bar_b=values.start_bar_b,
            recipe_keys=(recipe,),
        )
        self.lead_gain_percent = values.lead_gain_percent
        self.backing_gain_percent = values.backing_gain_percent
        self.preserve_signature_intro = values.preserve_signature_intro
        self.selected_plan = adjusted_plan
        self._active_branch_key = self._plan_branch_key(adjusted_plan)

        # Keep the earlier Trial for A/B listening, but detach it from the active
        # branch so Play/Choose can never silently reuse its file.
        self.studio.clear_active_take_for_new_branch()
        self.selected_take = None
        before = {take_identity_key(item) for item in self.studio.takes}
        self._prepare_parent_for_plan(adjusted_plan)
        for button in self.studio.recipe_checks.values():
            button.setChecked(False)
        selected = self.studio.recipe_checks.get(f"{PAIR_PREFIX}{recipe}")
        if selected and selected.isEnabled():
            selected.setChecked(True)
            self.studio.generate_takes_embedded()
        new_takes = [item for item in self.studio.takes if take_identity_key(item) not in before]
        if not new_takes:
            QMessageBox.warning(
                self,
                "Adjusted Take could not be created",
                "The previous Take remains available. Try a smaller start-bar change or restore the protected opening, then retry.",
            )
            return
        self.generated_takes.extend(new_takes)
        self._generated_branch_key = self._plan_branch_key(adjusted_plan)
        self._populate_results()
        self.results_summary.setText(
            f"Created {len(new_takes)} adjusted Take(s): lead {self.lead_gain_percent}%, backing "
            f"{self.backing_gain_percent}%, source start bars {adjusted_plan.start_bar_a + 1} and "
            f"{adjusted_plan.start_bar_b + 1}, target {adjusted_plan.target_bpm} BPM. "
            "Earlier Takes remain below for direct comparison."
        )

    def _show_score_help(self) -> None:
        text = "\n\n".join((
            score_help("preparation", 70),
            score_help("harmony", 60),
            score_help("direction", 65),
            score_help("tempo", 75),
            score_help("meter", 85),
            score_help("orchestration", 70),
        ))
        QMessageBox.information(self, "How MIDI Remixer uses measurements", text)

    def _switch_mode(self, mode: str, *, keep_results: bool = False) -> None:
        self.studio.choose_guidance_mode(mode)
        if mode == "assisted":
            self.accept()
            schedule_owned(self, "launch-assisted", 0, self.studio.launch_assisted_wizard)
            return
        if keep_results and self.generated_takes:
            self.studio.navigate_to(10, force=True)
        else:
            self.studio.navigate_to(2, force=True)
        self.accept()

    def _show_page(self, index: int) -> None:
        self.current_page = max(0, min(4, index))
        self.pages.setCurrentIndex(self.current_page)
        # There is nothing to play on the preparation screen. Transport appears
        # only after songs or Trials are displayed.
        self.transport_bar.setVisible(self.current_page > 0)
        labels = ("Prepare", "Choose", "Review", "Listen", "Finish")
        self.progress.setText(f"Step {self.current_page + 1} of 5 · {labels[self.current_page]}")
        self.back.setText("← Change Working Mode" if self.current_page == 0 else "← Back")
        self.back.setEnabled(True)
        if self.current_page == 0:
            summary = self.studio.guided_library_summary_cached()
            self.next.setText("Find Mash Ideas →")
            ready = self.studio.audio_ready() and summary["guided_ready_songs"] >= 2
            reason = "Finish Audio Setup before opening mash ideas." if not self.studio.audio_ready() else "At least two distinct Guided-ready songs are required."
            set_action_enabled(self.next, ready, reason)
        elif self.current_page == 1:
            self.next.setText("Choose an idea above")
            self.next.setEnabled(False)
        elif self.current_page == 2:
            count = len(self.selected_plan.recipe_keys) if self.selected_plan else 0
            self.next.setText(f"Create {count} Take{'s' if count != 1 else ''} →")
            ready = self.selected_plan is not None and self.studio.audio_ready()
            set_action_enabled(self.next, ready, "Choose a mash idea and finish Audio Setup before generating Takes.")
        elif self.current_page == 3:
            self.next.setText("Continue to Finish →")
            current_ids = {take_identity_key(take) for take in self.generated_takes}
            ready = self.selected_take is not None and take_identity_key(self.selected_take) in current_ids
            set_action_enabled(self.next, ready, "Listen, then choose one Take from the current Trial.")
        else:
            self._refresh_finish_page()
            self.next.setText("Done — Return to Start")
            current_ids = {take_identity_key(take) for take in self.generated_takes}
            ready = self.selected_take is not None and take_identity_key(self.selected_take) in current_ids
            set_action_enabled(self.next, ready, "Choose a Take from the current mash before finishing.")

    def _return_to_ideas(self) -> None:
        """Return to recommendations with transport fully detached from the old branch."""
        self.studio.stop_playback()
        self._show_page(1)

    def _go_back(self) -> None:
        if self.current_page == 0:
            self.studio.change_working_mode(source_wizard=self)
            return
        # A previous Take must never continue controlling transport while the
        # user is choosing or generating a different combination.
        if self.current_page >= 3:
            self.studio.stop_playback()
        self._show_page(self.current_page - 1)

    def _go_next(self) -> None:
        if self.current_page == 0:
            self._find_recommendations()
        elif self.current_page == 2:
            self._generate_trials()
            self._show_page(3)
        elif self.current_page == 3 and self.selected_take is not None:
            # Older Guided builds would continue directly to instrumentation.
            # Stay inside the beginner workflow instead: advanced instrumentation is
            # an explicit optional action on the Finish screen, never an automatic jump.
            self._show_page(4)
        elif self.current_page == 4 and self.selected_take is not None:
            self.studio.navigate_to(0, force=True)
            self.accept()

