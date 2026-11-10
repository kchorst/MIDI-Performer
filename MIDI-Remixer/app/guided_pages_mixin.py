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
    guided_library_summary, guided_near_matches, guided_pair_recommendations,
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

if TYPE_CHECKING:
    from app.decision_workflow import DecisionWorkflowStudio
    from core.remix_engine import RemixTake




class GuidedWizardPagesMixin:
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(6)

        header = QHBoxLayout()
        title = QLabel("Guided Remix")
        title.setObjectName("SectionTitle")
        self.progress = QLabel("Step 1 of 5 · Prepare")
        self.progress.setObjectName("HeaderStatus")
        self.analysis_header = QLabel("")
        self.analysis_header.setObjectName("HeaderStatus")
        self.analysis_header.setVisible(False)
        self.audio_header = QLabel("Audio setup required")
        self.audio_header.setObjectName("HeaderWarning")
        header.addWidget(title)
        header.addSpacing(8)
        header.addWidget(self.progress)
        header.addStretch(1)
        header.addWidget(self.analysis_header)
        header.addSpacing(8)
        header.addWidget(self.audio_header)
        root.addLayout(header)

        # Persistent activity feedback lives outside the page stack. A rematch
        # launched from the recommendations page therefore cannot disappear
        # merely because the Prepare page is no longer visible.
        self.guided_activity_panel = QFrame()
        self.guided_activity_panel.setObjectName("LibraryScanPanel")
        activity_layout = QHBoxLayout(self.guided_activity_panel)
        activity_layout.setContentsMargins(10, 6, 10, 6)
        activity_layout.setSpacing(8)
        self.guided_activity_progress = QProgressBar()
        self.guided_activity_progress.setRange(0, 0)
        self.guided_activity_progress.setMaximumWidth(260)
        self.guided_activity_progress.setFormat("Opening prepared ideas…")
        self.guided_activity_label = QLabel("MIDI Remixer is opening prepared mash ideas…")
        self.guided_activity_label.setObjectName("LibraryScanDetail")
        self.guided_activity_label.setWordWrap(True)
        self.guided_activity_cancel = QPushButton("Cancel Search")
        self.guided_activity_cancel.setObjectName("StopButton")
        self.guided_activity_cancel.clicked.connect(self._cancel_guided_discovery)
        activity_layout.addWidget(self.guided_activity_progress)
        activity_layout.addWidget(self.guided_activity_label, 1)
        activity_layout.addWidget(self.guided_activity_cancel)
        self.guided_activity_panel.setVisible(False)
        root.addWidget(self.guided_activity_panel)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._scroll_page(self._build_prepare_page()))
        self.pages.addWidget(self._scroll_page(self._build_recommend_page()))
        self.pages.addWidget(self._scroll_page(self._build_plan_page()))
        self.pages.addWidget(self._scroll_page(self._build_results_page()))
        self.pages.addWidget(self._scroll_page(self._build_finish_page()))
        root.addWidget(self.pages, 1)

        self.transport_bar = self._build_transport_bar()
        root.addWidget(self.transport_bar)

        footer = QFrame()
        footer.setObjectName("ActionFooter")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(8, 5, 8, 5)
        self.back = QPushButton("← Change Working Mode")
        self.back.setObjectName("BackButton")
        self.back.setToolTip("Return to working-mode selection without deleting songs, Trials, or Takes.")
        self.back.clicked.connect(self._go_back)
        self.hint = QLabel("Finish audio setup first. Then MIDI Remixer can open prepared ideas between distinct songs with usable musical parts.")
        self.hint.setWordWrap(True)
        self.hint.setObjectName("Subtitle")
        self.next = QPushButton("Find dependable mash ideas →")
        self.next.setObjectName("PrimaryButton")
        self.next.clicked.connect(self._go_next)
        footer_layout.addWidget(self.back)
        footer_layout.addWidget(self.hint, 1)
        footer_layout.addWidget(self.next)
        root.addWidget(footer)

    def _apply_wizard_style(self) -> None:
        self.setStyleSheet(self.studio.styleSheet() + load_stylesheet("guided.qss"))

    def _fit_to_screen(self) -> None:
        parent = self.parentWidget()
        if parent is not None:
            self.setGeometry(parent.rect())
            return
        screen = self.screen() or QApplication.primaryScreen()
        if not screen:
            return
        available = screen.availableGeometry()
        width = min(1080, max(720, available.width() - 28))
        height = min(720, max(500, available.height() - 36))
        self.resize(width, height)
        self.move(available.center() - self.rect().center())

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self._fit_to_screen()

    def eventFilter(self, watched, event) -> bool:  # type: ignore[override]
        if watched is self.parentWidget() and event.type() == QEvent.Type.Resize:
            self._fit_to_screen()
        return super().eventFilter(watched, event)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        cancel_owned(self)
        self.transport_timer.stop()
        self._detach_background_callbacks()
        super().closeEvent(event)

    def _detach_background_callbacks(self) -> None:
        with suppress(RuntimeError, TypeError):
            self.studio.library_scan_bridge.deepState.disconnect(self._deep_scan_status_update)

    def _scroll_page(self, page: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setWidget(page)
        return scroll

    def _build_transport_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("ActionFooter")
        row = QHBoxLayout(bar)
        row.setContentsMargins(8, 5, 8, 5)
        row.setSpacing(7)
        self.transport_play = QPushButton("Play")
        self.transport_play.setObjectName("PlayButton")
        self.transport_play.clicked.connect(self._transport_play_or_resume)
        self.transport_stop = QPushButton("Stop")
        self.transport_stop.setObjectName("StopButton")
        self.transport_stop.clicked.connect(self.studio.stop_playback)
        self.transport_status = QLabel("Select a song or Trial to preview")
        self.transport_status.setObjectName("Subtitle")
        self.transport_status.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.transport_volume = QSlider(Qt.Orientation.Horizontal)
        self.transport_volume.setRange(0, 100)
        self.transport_volume.setValue(self.studio.volume_slider.value())
        self.transport_volume.setMaximumWidth(150)
        self.transport_volume.valueChanged.connect(self._wizard_volume_changed)
        self.transport_volume_value = QLabel(f"{self.transport_volume.value()}%")
        row.addWidget(self.transport_play)
        row.addWidget(self.transport_stop)
        row.addWidget(self.transport_status, 1)
        row.addWidget(QLabel("Volume"))
        row.addWidget(self.transport_volume)
        row.addWidget(self.transport_volume_value)
        return bar

    def _wizard_volume_changed(self, value: int) -> None:
        self.transport_volume_value.setText(f"{value}%")
        if self.studio.volume_slider.value() != value:
            self.studio.volume_slider.setValue(value)

    def _transport_play_or_resume(self) -> None:
        engine = self.studio.playback_engine
        if engine.is_playing() or engine.is_paused():
            self.studio.pause_or_resume_current()
            return
        path = self.studio.current_playback_path
        if path and Path(path).exists():
            self.studio._play_path(
                path,
                self.studio.current_playback_label or Path(path).stem,
                self.studio.current_playback_key or f"guided-replay-{Path(path).stem}",
            )

    def _sync_transport(self) -> None:
        engine = self.studio.playback_engine
        playing = engine.is_playing()
        paused = engine.is_paused()
        has_current = bool(self.studio.current_playback_path and Path(self.studio.current_playback_path).exists())
        self.transport_play.setText("Pause" if playing else "Resume" if paused else "Play")
        self.transport_play.setProperty("transportState", "pause" if playing else "ready")
        self.transport_play.setEnabled(playing or paused or has_current)
        self.transport_stop.setEnabled(playing or paused)
        self.transport_stop.setProperty("transportState", "active" if playing or paused else "idle")
        for button in (self.transport_play, self.transport_stop):
            button.style().unpolish(button)
            button.style().polish(button)
        if playing:
            label = self.studio.current_playback_label or "MIDI"
            self.transport_status.setText(f"Playing: {label}")
        elif paused:
            label = self.studio.current_playback_label or "MIDI"
            self.transport_status.setText(f"Paused: {label}")
        elif has_current:
            self.transport_status.setText(f"Ready to replay: {self.studio.current_playback_label or Path(self.studio.current_playback_path).stem}")
        else:
            self.transport_status.setText("Select a song or Trial to preview")
        studio_volume = self.studio.volume_slider.value()
        if self.transport_volume.value() != studio_volume:
            self.transport_volume.blockSignals(True)
            self.transport_volume.setValue(studio_volume)
            self.transport_volume.blockSignals(False)
            self.transport_volume_value.setText(f"{studio_volume}%")

    def _page_header(self, title: str, subtitle: str) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(8)
        # Pack beginner content against the top. Without explicit alignment a
        # hidden setup card could leave a large visual gap in a tall window.
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        heading = QLabel(title)
        heading.setObjectName("SectionTitle")
        text = QLabel(subtitle)
        text.setObjectName("Subtitle")
        text.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(text)
        return page, layout

    def _build_prepare_page(self) -> QWidget:
        page, layout = self._page_header(
            "Get audio ready, then find a good first mash",
            "A first-time user should be able to hear every choice. FluidSynth and a SoundFont therefore come before library searching or song selection.",
        )
        self.prepare_heading = layout.itemAt(0).widget()
        self.prepare_subtitle = layout.itemAt(1).widget()
        self.prepare_heading.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.prepare_subtitle.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

        self.prepare_next_panel = QLabel(
            "Next: search the library, listen to the original songs, choose one mash idea, then compare a few generated Trials."
        )
        self.prepare_next_panel.setObjectName("PrepareNextPanel")
        self.prepare_next_panel.setWordWrap(True)
        self.prepare_next_panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        layout.addWidget(self.prepare_next_panel)

        self.guided_audio_panel = QFrame()
        self.guided_audio_panel.setObjectName("WizardAudioPanel")
        audio = QGridLayout(self.guided_audio_panel)
        audio.setContentsMargins(10, 8, 10, 8)
        audio.setHorizontalSpacing(10)
        audio.setVerticalSpacing(5)
        title = QLabel("Audio setup")
        title.setObjectName("CardTitle")
        self.audio_line = QLabel("Checking FluidSynth…")
        self.audio_line.setObjectName("WizardStatusLine")
        self.instrument_line = QLabel("Checking Instrument Library…")
        self.instrument_line.setObjectName("WizardStatusLine")
        self.guided_fluidsynth_button = QPushButton("Locate FluidSynth…")
        self.guided_fluidsynth_button.setObjectName("PrimaryButton")
        self.guided_fluidsynth_button.clicked.connect(self._setup_audio)
        self.guided_soundfont_button = QPushButton("Choose SoundFont…")
        self.guided_soundfont_button.setObjectName("PrimaryButton")
        self.guided_soundfont_button.clicked.connect(self._choose_soundfont)
        audio.addWidget(title, 0, 0, 1, 2)
        audio.addWidget(self.audio_line, 1, 0)
        audio.addWidget(self.guided_fluidsynth_button, 1, 1)
        audio.addWidget(self.instrument_line, 2, 0)
        audio.addWidget(self.guided_soundfont_button, 2, 1)
        audio.setColumnStretch(0, 1)
        layout.addWidget(self.guided_audio_panel)

        readiness = QGroupBox("Song library")
        grid = QGridLayout(readiness)
        grid.setContentsMargins(8, 6, 8, 6)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)
        self.library_line = QLabel("MIDI library: not established")
        self.library_line.setObjectName("WizardStatusLine")
        self.gate_line = QLabel("Songs ready for Guided Remix: not evaluated")
        self.gate_line.setObjectName("WizardStatusLine")
        grid.addWidget(self.library_line, 0, 0, 1, 2)
        grid.addWidget(self.gate_line, 1, 0, 1, 2)
        layout.addWidget(readiness)

        self.scan_frame = QFrame()
        self.scan_frame.setObjectName("InfoPanel")
        scan_layout = QVBoxLayout(self.scan_frame)
        scan_layout.setContentsMargins(8, 5, 8, 5)
        scan_row = QHBoxLayout()
        self.scan_progress = QProgressBar()
        self.scan_progress.setRange(0, 0)
        self.scan_progress.setMaximumWidth(190)
        self.scan_status = QLabel("Waiting")
        self.scan_status.setWordWrap(True)
        scan_row.addWidget(self.scan_progress)
        scan_row.addWidget(self.scan_status, 1)
        self.cancel_search_button = QPushButton("Cancel Search")
        self.cancel_search_button.setObjectName("UtilityButton")
        self.cancel_search_button.setVisible(False)
        self.cancel_search_button.clicked.connect(self._cancel_guided_discovery)
        scan_row.addWidget(self.cancel_search_button)
        scan_layout.addLayout(scan_row)
        # Contract 4: detailed-analysis policy and pause/resume controls belong
        # exclusively to MIDI Library Manager. Guided Remix observes committed
        # readiness and never exposes a second set of Library maintenance controls.
        self.scan_policy_row = QFrame()
        self.scan_policy_row.setVisible(False)
        self.deep_control_row = QFrame()
        self.deep_control_row.setVisible(False)
        self.scan_frame.setVisible(False)
        layout.addWidget(self.scan_frame)

        actions = QGroupBox("Find mash ideas")
        action_layout = QHBoxLayout(actions)
        action_layout.setContentsMargins(10, 8, 10, 8)
        action_layout.setSpacing(8)
        # Primary search is visually dominant but remains a normal-sized button,
        # not a full-width blue strip that can be mistaken for a section label.
        self.use_library = QPushButton("Find Mash Ideas in My Library")
        self.use_library.setObjectName("WizardPrimaryChoice")
        self.use_library.setToolTip("Open prepared, distinct-song mash ideas already committed by MIDI Library Engine.")
        self.use_library.clicked.connect(self._find_recommendations)
        self.choose_folder_button = QPushButton("Open MIDI Library Manager…")
        self.choose_folder_button.setObjectName("WizardUtilityButton")
        self.choose_folder_button.clicked.connect(self._choose_folder)
        action_layout.addWidget(self.use_library)
        action_layout.addSpacing(8)
        action_layout.addWidget(self.choose_folder_button)
        action_layout.addStretch(1)
        layout.addWidget(actions)

        note = QLabel(
            "Guided Remix uses one representative per song, keeps obvious alternate versions together, and prefers files with separate melody, backing, bass, drums, and supporting parts. Files needing role correction remain available in Assisted Studio and Creative Workbench."
        )
        note.setObjectName("InfoPanel")
        note.setWordWrap(True)
        layout.addWidget(note)
        return page

    def _toggle_details(self, button: QPushButton, widget: QWidget) -> None:
        visible = not widget.isVisible()
        widget.setVisible(visible)
        button.setText("Hide details ▴" if visible else "Why this works ▾")

    def _friendly_recipe_text(self, plan: GuidedPairPlan, recipe: str) -> tuple[str, str]:
        a = plan.source_a.title
        b = plan.source_b.title
        mapping = {
            "melody-a-accompaniment-b": (
                f"{a} melody over {b} instruments",
                f"Keep {a}'s lead melody while {b} supplies drums, bass, chords, and supporting parts.",
            ),
            "melody-b-accompaniment-a": (
                f"{b} melody over {a} instruments",
                f"Keep {b}'s lead melody while {a} supplies drums, bass, chords, and supporting parts.",
            ),
            "section-exchange": (
                "Songs take turns in complete sections",
                "Each song keeps its own melody and harmony, then hands the arrangement to the other song.",
            ),
            "transformation": (
                "Gradual handoff",
                "The arrangement slowly changes from one song to the other instead of switching all at once.",
            ),
            "conversation": (
                "Call and response",
                "Short phrases from the two songs answer each other while avoiding constant overlap.",
            ),
            "layered-blend": (
                "Controlled layered blend",
                "Compatible parts play together with register and balance protection.",
            ),
            "foundation-swap": (
                "Swap the musical foundation",
                "The melody and backing roles change partway through so both directions can be heard.",
            ),
        }
        return mapping.get(recipe, (recipe.replace("-", " ").title(), "A different arrangement of the same checked song pair."))

    def _plain_pair_summary(self, plan: GuidedPairPlan) -> str:
        if plan.category == "Creative section-switch":
            return "A controlled creative option: the songs trade complete passages instead of forcing their melodies and chords together."
        lead = plan.source_a if plan.lead_source == "a" else plan.source_b
        backing = plan.source_b if plan.backing_source == "b" else plan.source_a
        return f"Best direction: keep {lead.title}'s melody and use {backing.title}'s fuller instrumental backing."

    def _build_recommend_page(self) -> QWidget:
        page, layout = self._page_header(
            "Choose a mash idea",
            "MIDI Remixer found several different song combinations. Read one short explanation, audition either original song, then choose an idea. Technical measurements stay hidden unless you open them.",
        )
        self.recommendation_summary = QLabel("No search has been run yet.")
        self.recommendation_summary.setObjectName("InfoPanel")
        self.recommendation_summary.setWordWrap(True)
        layout.addWidget(self.recommendation_summary)

        self.recommendation_area = QScrollArea()
        self.recommendation_area.setWidgetResizable(True)
        self.recommendation_area.setFrameShape(QFrame.Shape.NoFrame)
        self.recommendation_widget = QWidget()
        self.recommendation_layout = QVBoxLayout(self.recommendation_widget)
        self.recommendation_layout.setContentsMargins(2, 2, 5, 2)
        self.recommendation_layout.setSpacing(9)
        self.recommendation_area.setWidget(self.recommendation_widget)
        layout.addWidget(self.recommendation_area, 1)

        bottom = QHBoxLayout()
        readiness = QPushButton("Why were some songs skipped?")
        readiness.setObjectName("UtilityButton")
        readiness.clicked.connect(self._show_readiness_report)
        more_ideas = QPushButton("More Different Mash Ideas")
        more_ideas.setObjectName("PrimaryButton")
        more_ideas.setToolTip("Open the next prepared batch while excluding song families already shown in this Guided session.")
        more_ideas.clicked.connect(self._find_recommendations)
        browse = QPushButton("Open Full MIDI Library…")
        browse.setObjectName("UtilityButton")
        browse.setToolTip("Open the authoritative MIDI Library Manager with all Library functions and playback controls.")
        browse.clicked.connect(lambda _checked=False: self.studio.open_library_browser(purpose="browse"))
        assisted = QPushButton("Open Assisted Studio")
        assisted.setObjectName("UtilityButton")
        assisted.clicked.connect(lambda: self._switch_mode("assisted"))
        bottom.addWidget(readiness)
        bottom.addWidget(more_ideas)
        bottom.addWidget(browse)
        bottom.addStretch(1)
        bottom.addWidget(assisted)
        layout.addLayout(bottom)
        return page

    def _build_plan_page(self) -> QWidget:
        page, layout = self._page_header(
            "Review what MIDI Remixer will create",
            "The app has already chosen the safer timing, key treatment, and starting points. It will create no more than three clearly different Takes, and your original MIDI files remain untouched.",
        )
        self.plan_title = QLabel("No mash idea selected")
        self.plan_title.setObjectName("EmptyTitle")
        layout.addWidget(self.plan_title)
        self.plan_overview = QLabel("Choose a mash idea first.")
        self.plan_overview.setObjectName("InfoPanel")
        self.plan_overview.setWordWrap(True)
        layout.addWidget(self.plan_overview)

        approaches = QGroupBox("Versions you will be able to compare")
        self.plan_choices_layout = QVBoxLayout(approaches)
        self.plan_choices_layout.setSpacing(7)
        layout.addWidget(approaches)

        self.plan_details_button = QPushButton("Why this works ▾")
        self.plan_details_button.setObjectName("UtilityButton")
        self.plan_text = QPlainTextEdit()
        self.plan_text.setReadOnly(True)
        self.plan_text.setMaximumHeight(210)
        self.plan_text.setVisible(False)
        self.plan_details_button.clicked.connect(lambda: self._toggle_details(self.plan_details_button, self.plan_text))
        layout.addWidget(self.plan_details_button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.plan_text)

        audition = QHBoxLayout()
        self.play_a = QPushButton("Play First Song")
        self.play_a.setObjectName("PlayButton")
        self.play_a.clicked.connect(lambda: self._play_plan_source(0))
        self.play_b = QPushButton("Play Second Song")
        self.play_b.setObjectName("PlayButton")
        self.play_b.clicked.connect(lambda: self._play_plan_source(1))
        self.play_signature = QPushButton("Preview Special Opening")
        self.play_signature.setObjectName("PlayButton")
        self.play_signature.clicked.connect(self._play_signature_preview)
        self.play_signature.setVisible(False)
        another = QPushButton("← Choose Another Idea")
        another.setObjectName("BackButton")
        another.clicked.connect(lambda: self._return_to_ideas())
        audition.addWidget(self.play_a)
        audition.addWidget(self.play_b)
        audition.addWidget(self.play_signature)
        audition.addStretch(1)
        audition.addWidget(another)
        layout.addLayout(audition)
        layout.addStretch(1)
        return page

    def _build_results_page(self) -> QWidget:
        page, layout = self._page_header(
            "Listen and choose your favorite Take",
            "Each card sounds different. Play them one at a time and choose the result you want to continue with. Technical settings are optional details.",
        )
        self.results_summary = QLabel("Takes have not been generated yet.")
        self.results_summary.setObjectName("InfoPanel")
        self.results_summary.setWordWrap(True)
        layout.addWidget(self.results_summary)
        self.results_area = QScrollArea()
        self.results_area.setWidgetResizable(True)
        self.results_widget = QWidget()
        self.results_layout = QVBoxLayout(self.results_widget)
        self.results_layout.setContentsMargins(2, 2, 5, 2)
        self.results_layout.setSpacing(9)
        self.results_area.setWidget(self.results_widget)
        layout.addWidget(self.results_area, 1)

        actions = QHBoxLayout()
        another = QPushButton("← Try Another Mash Idea")
        another.setObjectName("BackButton")
        another.clicked.connect(lambda: self._return_to_ideas())
        problem = QPushButton("Adjust the Chosen Take…")
        problem.setObjectName("UtilityButton")
        problem.clicked.connect(self._revise)
        assisted = QPushButton("More Control in Assisted Studio")
        assisted.setObjectName("UtilityButton")
        assisted.clicked.connect(lambda: self._switch_mode("assisted", keep_results=True))
        actions.addWidget(another)
        actions.addWidget(problem)
        actions.addStretch(1)
        actions.addWidget(assisted)
        layout.addLayout(actions)
        return page

    def _build_finish_page(self) -> QWidget:
        page, layout = self._page_header(
            "Finish your mash",
            "Your chosen Take is ready. Stay in the beginner workflow to listen, make an optional sound adjustment, and save MIDI or audio.",
        )
        self.finish_summary = QLabel("Choose a Take first.")
        self.finish_summary.setObjectName("InfoPanel")
        self.finish_summary.setWordWrap(True)
        layout.addWidget(self.finish_summary)

        self.finish_choices = QGroupBox("What would you like to do?")
        finish_layout = QVBoxLayout(self.finish_choices)
        finish_layout.setContentsMargins(10, 8, 10, 10)
        finish_layout.setSpacing(8)
        actions = QHBoxLayout()
        export_actions = QHBoxLayout()
        self.finish_play = QPushButton("▶ Play Chosen Take")
        self.finish_play.setObjectName("PlayButton")
        self.finish_play.clicked.connect(self._play_chosen_take)
        self.finish_mixer = QPushButton("Adjust Instruments & Balance…")
        self.finish_mixer.setObjectName("UtilityButton")
        self.finish_mixer.clicked.connect(self.studio.open_mixer)
        self.finish_performance = QPushButton("Performance Polish…")
        self.finish_performance.setObjectName("PrimaryButton")
        self.finish_performance.clicked.connect(self._performance_polish_chosen)
        self.finish_export_package = QPushButton("Export Selected…")
        self.finish_export_package.setObjectName("PrimaryButton")
        self.finish_export_package.clicked.connect(self.studio.export_selected)
        self.finish_export_midi = QPushButton("Save Remixed MIDI…")
        self.finish_export_midi.setObjectName("PrimaryButton")
        self.finish_export_midi.clicked.connect(self.studio.export_selected_midi)
        self.finish_render_wav = QPushButton("Render WAV…")
        self.finish_render_wav.clicked.connect(lambda: self.studio.render_selected_audio("wav"))
        self.finish_render_mp3 = QPushButton("Render MP3…")
        self.finish_render_mp3.clicked.connect(lambda: self.studio.render_selected_audio("mp3"))
        actions.addWidget(self.finish_play)
        actions.addWidget(self.finish_mixer)
        actions.addWidget(self.finish_performance)
        actions.addStretch(1)
        export_actions.addWidget(self.finish_export_package)
        export_actions.addWidget(self.finish_export_midi)
        export_actions.addWidget(self.finish_render_wav)
        export_actions.addWidget(self.finish_render_mp3)
        export_actions.addStretch(1)
        finish_layout.addLayout(actions)
        finish_layout.addLayout(export_actions)
        layout.addWidget(self.finish_choices)

        note = QLabel(
            "MIDI Remixer keeps the chosen Take and its Trial. Your original MIDI files are unchanged. "
            "Instrumentation editing and Performance Polish are optional; every finished version is a separate Take revision."
        )
        note.setObjectName("DetailsPanel")
        note.setWordWrap(True)
        layout.addWidget(note)

        advanced = QHBoxLayout()
        advanced_label = QLabel("Need every technical control?")
        advanced_label.setObjectName("Subtitle")
        advanced_button = QPushButton("Customize This Exact Take in Creative Workbench")
        advanced_button.setObjectName("UtilityButton")
        advanced_button.clicked.connect(self._open_advanced_instrumentation)
        advanced.addWidget(advanced_label)
        advanced.addStretch(1)
        advanced.addWidget(advanced_button)
        layout.addLayout(advanced)
        layout.addStretch(1)
        return page

    def _refresh_finish_page(self) -> None:
        take = self.selected_take or self.studio.selected_take
        enabled = take is not None
        for button in (
            self.finish_play, self.finish_mixer, self.finish_performance, self.finish_export_package,
            self.finish_export_midi, self.finish_render_wav, self.finish_render_mp3,
        ):
            set_action_enabled(button, enabled, "Choose a Take before using this action.")
        if take is None:
            self.finish_summary.setText("Choose a Take on the previous screen before finishing.")
            return
        self.studio.selected_take = take
        source_names: list[str] = []
        for path in take.source_paths or tuple(path for path in (take.source_a, take.source_b) if path):
            profile = self.studio.profiles.get(path)
            source_names.append(profile.title if profile else Path(path).stem)
        sources = " + ".join(source_names) if source_names else "your selected songs"
        self.finish_summary.setText(
            f"<b>{take.name}</b><br>"
            f"Mash: {sources}<br>"
            f"Target: {take.target_key} · {take.target_bpm} BPM · {take.bars} bars<br>"
            "The timing, key treatment, arrangement, and broad balance decisions are already applied to this generated Take."
        )
