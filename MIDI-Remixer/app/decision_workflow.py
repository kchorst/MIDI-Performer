from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import os
import signal
import statistics
import tempfile
import traceback
from typing import Any

from PySide6.QtCore import QLockFile, Qt
from PySide6.QtGui import QAction, QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QFrame, QGridLayout,
    QGroupBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QListWidget, QListWidgetItem,
    QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QSlider,
    QSpinBox, QSplitter, QStackedWidget, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from app.guided_remix_wizard import GuidedRemixWizard
from app.assisted_studio_wizard import AssistedStudioWizard
from app.mode_launch_mixin import ModeLaunchMixin
from app.button_policy import set_action_enabled
from app.deferred_actions import schedule_owned
from app.controllers.onboarding import OnboardingReadiness
from app.controllers.workflow_state import WorkflowStateController
from core.guided_beginner import score_band
from core.listening_review import ListeningReview, issue_for, listening_issue_labels
from app.studio_window import MidiRemixerStudio, PAIR_PREFIX, MULTI_PREFIX
from app.theme import load_stylesheet
from app.ui_components import (
    HoverTableWidget, SortableItem,
    decode_qbytearray as _decode_qbytearray, encode_qbytearray as _encode_qbytearray,
)
from core.dependency_setup import find_soundfont
from core.multi_source import SourceSpec
from core.operation_diagnostics import concise_exception, traceback_location, write_diagnostic
from core.remix_profile import NOTE_NAMES, assess_pair
from core.remix_guidance import (
    GUIDANCE_MODES, compatibility_explanation, conflict_decision, frame_decision,
    guided_recipe_keys, recommend_frame, recipe_advice, source_readiness,
)
from core.remix_version import APP_NAME, APP_VERSION_DISPLAY, TAGLINE
from core.runtime_cleanup import runtime_supervisor


from app.decision_pages_mixin import DecisionWorkflowPagesMixin

STEP_TITLES = (
    "Choose Guidance",
    "Choose Starting Path",
    "Choose Songs",
    "Review Songs",
    "Find Related Songs",
    "Inspect Structure",
    "Assign Musical Roles",
    "Choose Musical Frame",
    "Review Musical Conflicts",
    "Choose Arrangements",
    "Generate & Compare Takes",
    "Instrumentation",
    "Mix, Perform & Export",
)

STEP_GUIDANCE = 0
STEP_START_METHOD = 1
STEP_SELECT_SOURCES = 2
STEP_REVIEW_SOURCES = 3
STEP_DISCOVERY = 4
STEP_STRUCTURE = 5
STEP_ROLES = 6
STEP_FRAME = 7
STEP_CONFLICTS = 8
STEP_ARRANGEMENTS = 9
STEP_TAKES = 10
STEP_INSTRUMENTATION = 11
STEP_FINISH = 12

START_MODES: dict[str, tuple[str, str]] = {
    "one": (
        "Start with one MIDI",
        "Choose a temporary discovery focus, hear it raw, then let MIDI Remixer expose several kinds of related material.",
    ),
    "several": (
        "Start with several MIDIs",
        "Choose two or more files you already have in mind. No song is permanently dominant; the discovery focus can change later.",
    ),
    "explore": (
        "Explore relationships in the library",
        "Browse categories, harmonic families, rhythm, and structure relationships before choosing songs for the mashup.",
    ),
    "role": (
        "Start with a musical role",
        "Begin by looking for a song that can provide melody, backing, bass, hooks, drums, transitions, or an ending.",
    ),
    "characteristics": (
        "Start with musical characteristics",
        "Search by tempo, meter, key, progression family, arrangement type, or orchestration readiness.",
    ),
}


class DecisionWorkflowStudio(DecisionWorkflowPagesMixin, ModeLaunchMixin, MidiRemixerStudio):
    """Decision-by-decision MIDI Remixer interface.

    The proven analysis, playback, generation, library, project, and export
    methods remain in MidiRemixerStudio. This class presents them as a guided
    musician/DJ path: one visible decision per screen, Back/Next navigation,
    and no permanent user-facing anchor requirement.
    """

    def __init__(self) -> None:
        self.start_mode: str | None = None
        self.guidance_mode = "guided"
        self.max_step_reached = STEP_GUIDANCE
        self._navigating_steps = False
        self._structure_spins: list[QSpinBox] = []
        self._guidance_applied_steps: set[int] = set()
        self._guidance_alternative = 0
        self._guided_wizard: GuidedRemixWizard | None = None
        self._fusion_wizard: AssistedStudioWizard | None = None
        self._assisted_wizard: AssistedStudioWizard | None = None
        self._mode_choice_confirmed = False
        self.workflow_state: WorkflowStateController | None = None
        self._mode_button_labels = {
            "guided": "1 · Guided Remix",
            "fusion": "2 · Version Fusion",
            "assisted": "3 · Assisted Studio",
            "workbench": "4 · Custom / Advanced",
        }
        super().__init__()
        self.workflow_state = WorkflowStateController(self)
        # The onboarding prompt has two deliberate choices. "Selected songs
        # only" remains available later from the Library, not during first run.
        self._show_selected_deep_policy = False
        self.setWindowTitle(f"{APP_NAME} {APP_VERSION_DISPLAY} — Decision Workflow")
        self._refresh_onboarding_controls()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(8, 5, 8, 7)
        outer.setSpacing(5)

        # Compact header: plain status text, not a row of button-like pills.
        header = QHBoxLayout()
        header.setSpacing(10)
        self.title_label = QLabel(APP_NAME)
        self.title_label.setObjectName("AppTitle")
        self.library_status = QLabel("Library: not established")
        self.library_status.setObjectName("HeaderStatus")
        self.mode_status = QLabel("Mode: Guided Remix")
        self.mode_status.setObjectName("HeaderStatus")
        self.soundfont_status = QLabel("Instrument Library: none")
        self.soundfont_status.setObjectName("HeaderStatus")
        self.soundfont_status.setMaximumWidth(430)
        self.audio_status = QLabel("FluidSynth not found")
        self.audio_status.setObjectName("HeaderWarning")
        # These actions remain available from Settings; on-screen setup actions
        # appear only when the corresponding dependency is missing.
        self.load_soundfont_button = QPushButton("Choose Instrument Library…")
        self.load_soundfont_button.clicked.connect(self.choose_soundfont)
        self.load_soundfont_button.setVisible(False)
        self.audio_setup_button = QPushButton("Set Up FluidSynth…")
        self.audio_setup_button.clicked.connect(self.show_audio_setup)
        self.audio_setup_button.setVisible(False)
        header.addWidget(self.title_label)
        header.addSpacing(8)
        header.addWidget(self.library_status)
        header.addWidget(QLabel("·"))
        header.addWidget(self.mode_status)
        header.addStretch(1)
        header.addWidget(self.soundfont_status)
        header.addWidget(QLabel("·"))
        header.addWidget(self.audio_status)
        outer.addLayout(header)
        # Build the shared Library-status controls now, but place them inside the
        # first-run page so the visible order is audio setup → authoritative
        # Library connection/status → mode selection.
        self._build_library_scan_panel()

        # Persistent source-set control is available on every decision screen.
        source_bar = QFrame()
        source_bar.setObjectName("ActionFooter")
        source_layout = QHBoxLayout(source_bar)
        source_layout.setContentsMargins(7, 4, 7, 4)
        source_layout.addWidget(QLabel("Songs in This Mashup"))
        self.global_source_combo = QComboBox()
        self.global_source_combo.setMinimumWidth(260)
        source_layout.addWidget(self.global_source_combo, 1)
        play_source = QPushButton("Play Selected Song")
        play_source.setObjectName("PlayButton")
        play_source.clicked.connect(self._play_global_source_raw)
        add_source = QPushButton("Add Song…")
        add_source.setObjectName("UtilityButton")
        add_source.clicked.connect(lambda _checked=False: self.open_library_browser(purpose="sources"))
        remove_source = QPushButton("Remove Song")
        remove_source.setObjectName("DangerButton")
        remove_source.clicked.connect(self._remove_global_source)
        review_source = QPushButton("Review Songs")
        review_source.setObjectName("UtilityButton")
        review_source.clicked.connect(lambda: self.navigate_to(STEP_REVIEW_SOURCES, force=True))
        source_layout.addWidget(play_source)
        source_layout.addWidget(add_source)
        source_layout.addWidget(remove_source)
        source_layout.addWidget(review_source)
        self.global_play_source_button = play_source
        self.global_remove_source_button = remove_source
        self.source_bar = source_bar
        outer.addWidget(source_bar)

        body = QSplitter(Qt.Orientation.Horizontal)
        body.setObjectName("WorkflowSplitter")
        body.setChildrenCollapsible(False)
        body.setHandleWidth(5)

        # Visible path keeps users oriented and lets them return to completed decisions.
        left = QFrame()
        left.setObjectName("WorkflowRail")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(7, 8, 7, 8)
        path_title = QLabel("Remix decisions")
        path_title.setObjectName("CardTitle")
        left_layout.addWidget(path_title)
        self.step_list = QListWidget()
        self.step_list.setObjectName("StepList")
        self.step_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        for number, title in enumerate(STEP_TITLES, 1):
            self.step_list.addItem(QListWidgetItem(f"{number}. {title}"))
        self.step_list.currentRowChanged.connect(self._step_clicked)
        left_layout.addWidget(self.step_list, 1)
        body.addWidget(left)
        self.workflow_rail = left

        self.tabs = QStackedWidget()  # Kept as ``tabs`` for the reused engine methods.
        self.tabs.addWidget(self._build_start_page())
        self.tabs.addWidget(self._build_start_method_page())
        self.tabs.addWidget(self._build_select_page())
        self.tabs.addWidget(self._build_review_page())
        self.tabs.addWidget(self._build_discovery_page())
        self.tabs.addWidget(self._build_structure_page())
        self.tabs.addWidget(self._build_roles_page())
        self.tabs.addWidget(self._build_frame_page())
        self.tabs.addWidget(self._build_conflicts_page())
        self.tabs.addWidget(self._build_arrangements_page())
        self.tabs.addWidget(self._build_takes_page())
        self.tabs.addWidget(self._build_instrumentation_page())
        self.tabs.addWidget(self._build_finish_page())
        body.addWidget(self.tabs)
        body.setStretchFactor(0, 0)
        body.setStretchFactor(1, 1)
        body.setSizes([180, 1000])
        self.workflow_splitter = body
        outer.addWidget(body, 1)

        # One navigation system everywhere.
        nav = QFrame()
        nav.setObjectName("ActionFooter")
        nav_layout = QHBoxLayout(nav)
        nav_layout.setContentsMargins(8, 5, 8, 5)
        self.back_button = QPushButton("← Back")
        self.back_button.setObjectName("BackButton")
        self.back_button.setToolTip("Return to the previous remix decision without discarding completed work.")
        self.back_button.clicked.connect(self.go_back)
        self.nav_hint = QLabel("Choose how much guidance MIDI Remixer should provide.")
        self.nav_hint.setObjectName("Subtitle")
        self.nav_hint.setWordWrap(True)
        self.next_button = QPushButton("Next →")
        self.next_button.setObjectName("PrimaryButton")
        self.next_button.clicked.connect(self.go_next)
        # QStackedWidget emits currentChanged while the first page is inserted.
        # Connect only after navigation controls exist so startup can never call
        # _update_navigation against a half-constructed window.
        self.tabs.currentChanged.connect(self._page_changed)
        nav_layout.addWidget(self.back_button)
        nav_layout.addWidget(self.nav_hint, 1)
        nav_layout.addWidget(self.next_button)
        outer.addWidget(nav)

        # Global transport: exact same colors and behavior on every screen.
        transport = QHBoxLayout()
        self.transport_play = QPushButton("Play")
        self.transport_play.setObjectName("PlayButton")
        self.transport_play.clicked.connect(self.toggle_play_pause)
        self.transport_stop = QPushButton("Stop")
        self.transport_stop.setObjectName("StopButton")
        self.transport_stop.clicked.connect(self.stop_playback)
        self.playback_label = QLabel("Ready")
        self.playback_label.setObjectName("StatusPill")
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setMaximumWidth(150)
        self.volume_slider.valueChanged.connect(self._volume_changed)
        self.volume_value = QLabel("100%")
        self.transport_volume_label = QLabel("Volume")
        transport.addWidget(self.transport_play)
        transport.addWidget(self.transport_stop)
        transport.addWidget(self.playback_label, 1)
        transport.addWidget(self.transport_volume_label)
        transport.addWidget(self.volume_slider)
        transport.addWidget(self.volume_value)
        self._global_transport_widgets = (
            self.transport_play, self.transport_stop, self.playback_label,
            self.transport_volume_label, self.volume_slider, self.volume_value,
        )
        outer.addLayout(transport)

        self.setCentralWidget(central)

    def _set_global_transport_visible(self, visible: bool) -> None:
        for widget in getattr(self, "_global_transport_widgets", ()):
            widget.setVisible(bool(visible))

    def _build_actions(self) -> None:
        """Add workflow navigation to the shared application menus."""
        super()._build_actions()
        file_action = next((action for action in self.menuBar().actions() if action.text() == "File"), None)
        file_menu = file_action.menu() if file_action is not None else None
        if file_menu is None:
            return
        self.change_working_mode_action = QAction("Change Working Mode…", self)
        self.change_working_mode_action.setToolTip(
            "Return to Guided, Version Fusion, Assisted, or Custom/Advanced selection without deleting songs, Trials, or Takes."
        )
        self.change_working_mode_action.triggered.connect(self.change_working_mode)
        insertion_point = next((action for action in file_menu.actions() if action.isSeparator()), None)
        if insertion_point is None:
            file_menu.addAction(self.change_working_mode_action)
        else:
            file_menu.insertAction(insertion_point, self.change_working_mode_action)

    def change_working_mode(self, _checked: bool = False, *, source_wizard=None) -> None:
        """Return to mode choice while preserving the session and generated work.

        Mode switching is navigation, not rejection. The library catalogue, selected
        source files, saved ideas, Trials, Takes, and project history remain intact.
        Only the current wizard overlay and its in-flight discovery operation stop.
        """
        self.stop_playback()
        active_wizards = []
        for wizard in (source_wizard, getattr(self, "_guided_wizard", None), getattr(self, "_fusion_wizard", None), getattr(self, "_assisted_wizard", None)):
            if wizard is not None and wizard not in active_wizards:
                active_wizards.append(wizard)
        for wizard in active_wizards:
            shutdown = getattr(wizard, "_shutdown_discovery_operations", None)
            if callable(shutdown):
                shutdown()
            if getattr(wizard, "isVisible", lambda: False)():
                wizard.reject()

        self._mode_choice_confirmed = False
        self.settings_store.set("onboarding_mode_chosen", False)
        self.mode_status.setText("Mode: choose below")
        self.navigate_to(STEP_GUIDANCE, force=True)
        self._refresh_onboarding_controls()
        self._update_navigation()
        self.raise_()
        self.setFocus()
        self.statusBar().showMessage(
            "Choose a different working mode. Songs, saved ideas, Trials, and Takes were preserved.",
            5000,
        )


    # --------------------------------------------------------------- styling
    def _apply_style(self) -> None:
        super()._apply_style()
        self.setStyleSheet(self.styleSheet() + load_stylesheet("decision.qss"))

    # --------------------------------------------------------- settings/layout
    def _restore_settings(self) -> None:
        stored_guidance = str(self.settings_store.get("guidance_mode", "guided"))
        self.guidance_mode = stored_guidance if stored_guidance in GUIDANCE_MODES else "guided"
        self._mode_choice_confirmed = bool(self.settings_store.get("onboarding_mode_chosen", False))
        self.session_workspace.set_guidance_mode(self.guidance_mode)
        self.project.guidance_mode = self.guidance_mode
        self.mode_status.setText(
            f"Mode: {GUIDANCE_MODES[self.guidance_mode][0]}"
            if self._mode_choice_confirmed else "Mode: choose after setup"
        )
        for key, button in self.guidance_mode_buttons.items():
            selected = self._mode_choice_confirmed and key == self.guidance_mode
            button.setChecked(selected)
            button.setText(self._mode_button_labels[key])
            card = self.guidance_mode_cards.get(key)
            if card is not None:
                card.setProperty("selected", selected)
            badge = self.guidance_mode_selected_labels.get(key)
            if badge is not None:
                badge.setVisible(selected)
        self.target_bpm.setValue(int(self.settings_store.get("remix_default_bpm", 110)))
        stored_mode = self.settings_store.get("remix_target_mode")
        if self.target_mode.findData(stored_mode) >= 0:
            self.target_mode.setCurrentIndex(self.target_mode.findData(stored_mode))
        self.mode_handling.setCurrentText(str(self.settings_store.get("remix_mode_handling", "Preserve source mode")))
        old_length = str(self.settings_store.get("remix_multisource_length_mode", "Current focus full length"))
        if old_length == "Anchor full length":
            old_length = "Current focus full length"
        if self.length_mode.findText(old_length) >= 0:
            self.length_mode.setCurrentText(old_length)
        self.bars_spin.setValue(int(self.settings_store.get("remix_default_bars", 64)))
        self.section_bars.setValue(int(self.settings_store.get("remix_default_section_bars", 8)))
        self.density.setCurrentText(str(self.settings_store.get("remix_default_density", "Balanced")))
        self.variations.setValue(int(self.settings_store.get("remix_variations_per_strategy", 1)))
        self.variation_strength.setCurrentText(str(self.settings_store.get("remix_variation_strength", "Moderate")))
        self.random_seed.setValue(int(self.settings_store.get("remix_random_seed", 0)))
        self.auto_balance.setChecked(bool(self.settings_store.get("remix_auto_balance", True)))
        self.conflict_dynamic.setChecked(self.auto_balance.isChecked())
        self.volume_slider.setValue(int(self.settings_store.get("master_volume", 100)))
        self.project.output_directory = str(self.session_workspace.root)
        self.output_label.setText(f"Session workspace: {self.session_workspace.root}")
        soundfont = find_soundfont(self.root, self.settings_store.get("last_soundfont_path"), self.settings_store.get("last_soundfont_directory"))
        if soundfont:
            self._apply_soundfont_path(soundfont)
        else:
            self._set_soundfont_state(None)
        self._auto_detect_fluidsynth(False)
        self._set_transport_state("stopped")
        self._populate_recipe_table()
        if bool(self.settings_store.get("ui_restore_layout", True)):
            geometry = _decode_qbytearray(self.settings_store.get("ui_window_geometry"))
            state = _decode_qbytearray(self.settings_store.get("ui_window_state"))
            workflow = _decode_qbytearray(self.settings_store.get("ui_workflow_splitter_state"))
            analysis = _decode_qbytearray(self.settings_store.get("ui_analysis_splitter_state"))
            takes = _decode_qbytearray(self.settings_store.get("ui_takes_splitter_state"))
            if not geometry.isEmpty():
                self.restoreGeometry(geometry)
            if not state.isEmpty():
                self.restoreState(state)
            if not workflow.isEmpty():
                self.workflow_splitter.restoreState(workflow)
            if not analysis.isEmpty():
                self.analysis_splitter.restoreState(analysis)
            if not takes.isEmpty():
                self.takes_splitter.restoreState(takes)
        self._ensure_window_on_screen()
        self._length_mode_changed(self.length_mode.currentText())
        self._update_generate_button()
        self._refresh_source_set()
        self.navigate_to(STEP_GUIDANCE, force=True)

    def _save_ui_state(self) -> None:
        self.settings_store.update_many({
            "ui_window_geometry": _encode_qbytearray(self.saveGeometry()),
            "ui_window_state": _encode_qbytearray(self.saveState()),
            "ui_workflow_splitter_state": _encode_qbytearray(self.workflow_splitter.saveState()),
            "ui_analysis_splitter_state": _encode_qbytearray(self.analysis_splitter.saveState()),
            "ui_takes_splitter_state": _encode_qbytearray(self.takes_splitter.saveState()),
            "ui_active_decision_step": self.tabs.currentIndex(),
            "master_volume": self.volume_slider.value(),
            "remix_target_mode": self.target_mode.currentData(),
            "remix_mode_handling": self.mode_handling.currentText(),
            "remix_multisource_length_mode": self.length_mode.currentText(),
            "remix_default_bars": self.bars_spin.value(),
            "remix_default_section_bars": self.section_bars.value(),
            "remix_default_density": self.density.currentText(),
            "remix_variations_per_strategy": self.variations.value(),
            "remix_variation_strength": self.variation_strength.currentText(),
            "remix_random_seed": self.random_seed.value(),
            "remix_auto_balance": self.auto_balance.isChecked(),
            "guidance_mode": self.guidance_mode,
            "onboarding_mode_chosen": self._mode_choice_confirmed,
        })

    def _onboarding_readiness(self) -> OnboardingReadiness:
        return OnboardingReadiness(
            fluidsynth_ready=bool(self.resolved_fluidsynth_path()),
            soundfont_ready=bool(self.resolved_soundfont_path()),
            library_loading=bool(getattr(self, "_library_startup_loading", False)),
            library_refreshing=bool(self._classification_in_progress),
            library_ready=bool(self.profiles and self._library_quick_scan_completed),
            library_setup_pending=bool(self._library_policy_decision_pending),
            mode_confirmed=bool(self._mode_choice_confirmed),
        )

    def _onboarding_block_reason(self, *, require_mode: bool = True) -> str | None:
        # One pure state object owns the onboarding gate vocabulary so UI, tests,
        # and first-run/returning-user behavior cannot drift into the retired
        # Remixer-owned scanning model.
        return self._onboarding_readiness().block_reason(require_mode=require_mode)

    def _refresh_onboarding_controls(self) -> None:
        if not hasattr(self, "guidance_mode_buttons"):
            return
        fluidsynth_path = self.resolved_fluidsynth_path()
        soundfont_path = self.resolved_soundfont_path()
        fluidsynth_ready = bool(fluidsynth_path)
        soundfont_ready = bool(soundfont_path)
        audio_ready = fluidsynth_ready and soundfont_ready

        self.start_fluidsynth_status.setText(
            f"1. ✓ FluidSynth: {Path(fluidsynth_path).name}" if fluidsynth_path else "1. FluidSynth: not found"
        )
        self.start_soundfont_status.setText(
            f"2. ✓ SoundFont: {Path(soundfont_path).name}" if soundfont_path else "2. SoundFont: not selected"
        )
        if not fluidsynth_ready:
            self.start_audio_message.setText("Start by locating or installing FluidSynth.")
        elif not soundfont_ready:
            self.start_audio_message.setText("FluidSynth is ready. Next, choose a SoundFont.")
        elif getattr(self, "_library_startup_loading", False):
            self.start_audio_message.setText("Audio is ready. Loading the saved MIDI library — no rescan is running.")
        elif self._classification_in_progress:
            self.start_audio_message.setText("Audio is ready. Refreshing committed MIDI Library state.")
        elif not self.profiles:
            self.start_audio_message.setText("Audio is ready. Open MIDI Library Manager to create or recover your MIDI Library.")
        elif self._library_policy_decision_pending:
            self.start_audio_message.setText("Library setup is incomplete. Finish analysis setup in MIDI Library Manager.")
        else:
            self.start_audio_message.setText("Audio and the authoritative MIDI Library are ready.")

        # SoundFont selection and MIDI catalogue setup are independent. The
        # mode gate still requires all prerequisites, but these setup actions
        # must never unlock one another accidentally.
        self.start_soundfont_button.setEnabled(not soundfont_ready)
        self.start_soundfont_button.setToolTip(
            "Choose an .sf2 or .sf3 instrument library." if not soundfont_ready else str(soundfont_path)
        )
        configured_paths = self._normalised_library_paths()
        configured_library = bool(configured_paths)
        setup_complete = audio_ready and configured_library
        ready_for_mode = self._onboarding_block_reason(require_mode=False) is None
        analysis_snapshot = {} if getattr(self, "_library_startup_loading", False) else (self.deep_analysis_progress() if setup_complete else {})
        analysis_remaining = int(analysis_snapshot.get("remaining", 0) or 0)
        analysis_policy = str(analysis_snapshot.get("policy", "later"))
        analysis_is_active = analysis_remaining > 0 and analysis_policy == "background"
        # A completed setup is not a permanent first-page section. Once the fast
        # catalogue and policy decision are complete, the application header and
        # File/Settings menus retain library/audio access. Keep only genuinely
        # active detailed-analysis progress above the mode cards.
        self.audio_ready_panel.setVisible(not setup_complete)
        show_compact_library = setup_complete
        self.library_ready_strip.setVisible(show_compact_library)
        self.compact_browse_library_button.setEnabled(not self._classification_in_progress)
        if ready_for_mode and not analysis_is_active:
            self._set_library_scan_panel_visible(False)
        if self.profiles:
            distinct = int(getattr(self, "_library_cached_distinct_count", 0) or 0)
            if distinct > 0:
                self.library_ready_summary.setText(
                    f"MIDI library: {len(self.profiles)} files · about {distinct} distinct songs"
                )
            else:
                self.library_ready_summary.setText(f"MIDI library: {len(self.profiles)} files")
        elif configured_paths:
            if getattr(self, "_library_startup_loading", False):
                self.library_ready_summary.setText(f"Loading saved MIDI library: {Path(configured_paths[0]).name} — no rescan")
            else:
                self.library_ready_summary.setText(f"Library source: {Path(configured_paths[0]).name}")

        if isinstance(self.start_page_heading, QLabel) and isinstance(self.start_page_subtitle, QLabel):
            if not setup_complete:
                self.start_page_heading.setText("Set up audio and connect your MIDI Library, then choose a working mode")
                self.start_page_subtitle.setText(
                    "Set up FluidSynth and a SoundFont, then open MIDI Library Manager to create or recover the authoritative catalogue. "
                    "MIDI Remixer reads committed Library state; it does not scan or manage the catalogue."
                )
            elif getattr(self, "_library_startup_loading", False):
                self.start_page_heading.setText("Loading your saved MIDI library")
                self.start_page_subtitle.setText("MIDI Remixer is reopening the persistent catalogue in the background. Your MIDI files are not being rescanned.")
            elif self._classification_in_progress:
                self.start_page_heading.setText("Refreshing committed MIDI Library state")
                self.start_page_subtitle.setText(
                    "MIDI Remixer is refreshing its read-only view of the authoritative Library. Scanning and analysis remain owned by MIDI Library Manager/Engine."
                )
            elif self._library_policy_decision_pending:
                self.start_page_heading.setText("Finish Library setup in MIDI Library Manager")
                self.start_page_subtitle.setText(
                    "The Library is not yet ready for remix admission. Complete any pending analysis or eligibility work in MIDI Library Manager, then return here."
                )
            else:
                self.start_page_heading.setText("Choose how you want to remix")
                self.start_page_subtitle.setText(
                    "Choose the amount of help you want. Audio and library setup are complete and no longer need space on this screen."
                )

        self.start_library_button.setEnabled(not self._classification_in_progress)
        folder_button_text = "Open MIDI Library Manager…"
        self.start_library_button.setText(folder_button_text)
        self.compact_browse_library_button.setText("Open Full MIDI Library…")
        self.start_library_button.setObjectName("UtilityButton" if configured_library else "PrimaryButton")
        self.start_library_button.style().unpolish(self.start_library_button)
        self.start_library_button.style().polish(self.start_library_button)
        self.start_library_button.setToolTip(
            "Open MIDI Library Manager to create, recover, scan, analyze, or maintain the authoritative catalogue. "
            "MIDI Remixer never performs those Library-management operations itself."
        )

        self.guidance_group.setEnabled(ready_for_mode)
        self.mode_choice_title.setText("Choose a working mode" if ready_for_mode else "2. Choose how you want to remix")
        for key, button in self.guidance_mode_buttons.items():
            button.setEnabled(ready_for_mode)
            selected = self._mode_choice_confirmed and key == self.guidance_mode
            button.blockSignals(True)
            button.setChecked(selected)
            button.setText(self._mode_button_labels[key])
            button.blockSignals(False)
            card = self.guidance_mode_cards.get(key)
            if card is not None:
                card.setProperty("selected", selected)
                card.style().unpolish(card)
                card.style().polish(card)
            badge = self.guidance_mode_selected_labels.get(key)
            if badge is not None:
                badge.setVisible(selected)

        block = self._onboarding_block_reason(require_mode=False)
        self.mode_unlock_status.setVisible(bool(block))
        if block:
            self.mode_unlock_status.setText(block)

        if not self._mode_choice_confirmed:
            self.mode_status.setText("Mode: choose below" if ready_for_mode else "Mode: choose after setup")
        if hasattr(self, "_update_navigation"):
            self._update_navigation()

    def _update_deep_analysis_panel(self, current_path: str = "", state: str = "") -> None:
        super()._update_deep_analysis_panel(current_path, state)
        # The base panel may briefly show its completion state. On the mode-choice
        # screen, completed setup is intentionally compacted immediately; only
        # active background work should occupy this area.
        self._refresh_onboarding_controls()

    def _on_library_onboarding_state_changed(self) -> None:
        if hasattr(self, "selection_table"):
            self._refresh_decision_summaries()
        self._refresh_onboarding_controls()

    def _refresh_audio_readiness_ui(self) -> None:
        super()._refresh_audio_readiness_ui()
        self._refresh_onboarding_controls()

    def reset_layout(self) -> None:
        self.resize(1180, 780)
        self.workflow_splitter.setSizes([180, 1000])
        self.analysis_splitter.setSizes([390, 230])
        self.takes_splitter.setSizes([720, 410])
        self.fit_window_to_screen()

    def clear_active_take_for_new_branch(self) -> None:
        """Invalidate only the active Take when a wizard changes branches.

        Earlier Trials and Takes remain available in project history. This
        prevents the transport or Workbench from silently using the previous
        mash while a new song combination is displayed.
        """

        if self.workflow_state is not None:
            self.workflow_state.clear_active_take()

    def select_exact_take(self, take) -> object:
        if self.workflow_state is None:
            raise RuntimeError("Workflow state controller is not available.")
        return self.workflow_state.select_take(take)

    def open_take_in_workbench(self, take, *, destination: int = STEP_INSTRUMENTATION) -> object:
        if self.workflow_state is None:
            raise RuntimeError("Workflow state controller is not available.")
        return self.workflow_state.open_take_in_workbench(take, destination=destination)

    # -------------------------------------------------------------- navigation
    def choose_guidance_mode(self, mode: str) -> None:
        if mode not in GUIDANCE_MODES:
            return
        block = self._onboarding_block_reason(require_mode=False)
        if block:
            self.statusBar().showMessage(block, 5000)
            self._refresh_onboarding_controls()
            return
        self.guidance_mode = mode
        if mode != "guided":
            # Guided adjustment levels are attached to the generated branch, not
            # global mixer defaults. Avoid silently carrying them into Assisted
            # Studio or a later manual Workbench generation.
            self.pair_lead_gain_percent = 100
            self.pair_backing_gain_percent = 100
            self.pair_backing_mode = "all-except-melody"
        self._mode_choice_confirmed = True
        self.settings_store.update_many({"guidance_mode": mode, "onboarding_mode_chosen": True})
        self.session_workspace.set_guidance_mode(mode)
        self.project.guidance_mode = mode
        label = GUIDANCE_MODES[mode][0]
        self.mode_status.setText(f"Mode: {label}")
        for key, button in self.guidance_mode_buttons.items():
            selected = key == mode
            button.blockSignals(True)
            button.setChecked(selected)
            button.setText(self._mode_button_labels[key])
            button.blockSignals(False)
            card = self.guidance_mode_cards.get(key)
            if card is not None:
                card.setProperty("selected", selected)
                card.style().unpolish(card)
                card.style().polish(card)
            badge = self.guidance_mode_selected_labels.get(key)
            if badge is not None:
                badge.setVisible(selected)
        self._guidance_applied_steps.clear()
        self._apply_guidance_for_step(self.tabs.currentIndex(), force=True)
        self._apply_mode_readability()
        self.statusBar().showMessage(f"{label} selected. Existing songs, Trials, and Takes were preserved.", 3500)
        self._refresh_onboarding_controls()
        self._update_navigation()

    def launch_guided_wizard(self) -> None:
        # Guided Remix is embedded over the central workspace instead of opened
        # as a second top-level window. There is one audio state, one project
        # state, and no wizard that can disappear behind the workbench.
        try:
            if self._guided_wizard is not None and self._guided_wizard.isVisible():
                self._guided_wizard.raise_()
                self._guided_wizard.setFocus()
                return
            wizard = GuidedRemixWizard(self)
            self._guided_wizard = wizard

            def finished(_result: int) -> None:
                self._guided_wizard = None
                self._refresh_decision_summaries()
                self._update_navigation()

            wizard.finished.connect(finished)
            wizard.show()
            wizard.raise_()
            wizard.setFocus()
        except Exception as exc:
            self._guided_wizard = None
            log_path = write_diagnostic(self.paths.logs / "guided-remix-error.log", "Open Guided Remix", exc)
            QMessageBox.critical(
                self,
                "Guided Remix could not open",
                "MIDI Remixer kept the main project and source MIDI files safe.\n\n"
                f"{type(exc).__name__}: {exc}\n\n"
                f"Details were written to:\n{log_path}",
            )

    def launch_anchor_discovery(self) -> None:
        anchor_path = ""
        combo = getattr(self, "discovery_focus_combo", None)
        if combo is not None:
            anchor_path = str(combo.currentData() or "")
        if not anchor_path and self.source_specs:
            anchor_path = self.source_specs[0].path
        self.launch_assisted_wizard(initial_anchor_path=anchor_path or None)

    def _change_current_decision_manually(self) -> None:
        # Guided users move into shared control; Fusion/Assisted users expose the full Workbench.
        new_mode = "assisted" if self.guidance_mode == "guided" else "workbench"
        if self.guidance_mode == "workbench":
            self.statusBar().showMessage("Creative Workbench already exposes all available controls.")
            return
        self.choose_guidance_mode(new_mode)

    def _try_another_recommendation(self) -> None:
        self._guidance_alternative = (self._guidance_alternative + 1) % 3
        step = self.tabs.currentIndex()
        self._apply_guidance_for_step(step, force=True)
        self.statusBar().showMessage(f"Alternative recommendation {self._guidance_alternative + 1} applied. Back/Undo and earlier Trials remain available.")

    def _set_decision_text(self, key: str, text: str) -> None:
        widget = getattr(self, f"{key}_decision_text", None)
        if isinstance(widget, QPlainTextEdit):
            widget.setPlainText(text)

    def _apply_guidance_for_step(self, step: int, *, force: bool = False) -> None:
        if step in self._guidance_applied_steps and not force:
            return
        profiles = self._effective_profiles() if self.source_specs else []
        if step in {STEP_STRUCTURE, STEP_ROLES, STEP_FRAME, STEP_CONFLICTS, STEP_ARRANGEMENTS, STEP_TAKES} and len(profiles) < 2:
            key = {
                STEP_STRUCTURE: "structure", STEP_ROLES: "roles", STEP_FRAME: "frame",
                STEP_CONFLICTS: "conflicts", STEP_ARRANGEMENTS: "arrangements", STEP_TAKES: "takes",
            }.get(step)
            if key:
                self._set_decision_text(key, "Choose at least two songs. MIDI Remixer will then fill this decision and explain why.")
            return

        if step == STEP_STRUCTURE:
            summaries = []
            for index, profile in enumerate(profiles, 1):
                ready = source_readiness(profile)
                summaries.append(f"{index}. {profile.title}: {ready.status} ({ready.confidence}% confidence). {ready.summary}")
            self._set_decision_text(
                "structure",
                f"MIDI Remixer's decision\n\nWhat MIDI Remixer chose\n• Begin each song at bar {1 + self._guidance_alternative * 4} for this recommendation.\n"
                "• Preserve each song's full structure until the shared form is chosen.\n\nWhy\n• Early trimming can hide useful intros, trios, bridges, or endings.\n\nSong readiness\n"
                + "\n".join(f"• {item}" for item in summaries),
            )
            if self.guidance_mode == "guided" or force:
                proposed_start = self._guidance_alternative * 4
                self.source_specs = [
                    replace(spec, start_bar=min(proposed_start, max(0, int(profile.bars) - 4)))
                    for spec, profile in zip(self.source_specs, profiles)
                ]
                self._refresh_structure_table()

        elif step == STEP_ROLES:
            choices: list[str] = []
            if self.guidance_mode in {"guided", "assisted"} or force:
                updated: list[SourceSpec] = []
                lead_index = self._guidance_alternative % max(1, len(self.source_specs))
                foundation_index = (lead_index + 1) % max(1, len(self.source_specs))
                for index, spec in enumerate(self.source_specs):
                    profile = self.profiles.get(spec.path)
                    if not profile:
                        updated.append(spec)
                        continue
                    readiness = source_readiness(profile)
                    treatment = spec.melody_treatment
                    if profile.arrangement_type in {"piano reduction", "keyboard reduction", "single-track performance"}:
                        treatment = "top"
                    role = spec.role
                    if role in {"auto", "anchor", "melody", "foundation"}:
                        role = "melody" if index == lead_index else "foundation" if index == foundation_index else "section"
                    updated.append(replace(spec, role=role, melody_treatment=treatment))
                    choices.append(f"{profile.title}: {role}; {'top-voice extraction' if treatment == 'top' else 'complete melodic part'}; {readiness.status}.")
                self.source_specs = updated
                self._refresh_source_set()
            self._set_decision_text(
                "roles",
                "MIDI Remixer's decision\n\nWhat MIDI Remixer chose\n" + "\n".join(f"• {item}" for item in choices)
                + "\n\nWhy\n• A clear lead and foundation produce an understandable first Trial."
                + "\n• Piano reductions use top-voice extraction before solo-instrument assignment."
                + "\n\nYou may change any role or track mapping; the engine will recalculate recipe availability.",
            )

        elif step == STEP_FRAME:
            frame = recommend_frame(profiles, alternative=self._guidance_alternative)
            if self.guidance_mode in {"guided", "assisted"} or force:
                self.target_bpm.setValue(int(frame["target_bpm"]))
                tonic = frame.get("target_key_pc")
                self.target_key.setCurrentIndex(max(0, self.target_key.findData(tonic)))
                mode = frame.get("target_mode")
                self.target_mode.setCurrentIndex(max(0, self.target_mode.findData(mode)))
                self.length_mode.setCurrentText("Custom bars")
                self.bars_spin.setValue(int(frame["bars"]))
                self.section_bars.setValue(int(frame["section_bars"]))
                self.auto_balance.setChecked(True)
                self.conflict_dynamic.setChecked(True)
            self._set_decision_text("frame", frame_decision(profiles, frame).as_text())
            self._refresh_frame_summary()

        elif step == STEP_CONFLICTS:
            self._set_decision_text("conflicts", conflict_decision(profiles).as_text())
            if self.guidance_mode in {"guided", "assisted"} or force:
                if self._guidance_alternative == 0:
                    self.conflict_preserve.setChecked(True)
                    self.mode_handling.setCurrentText("Preserve source mode")
                    self.conflict_dynamic.setChecked(True)
                    self.auto_balance.setChecked(True)
                    treatment = "Conservative: preserve song notes and normalize broad dynamics."
                elif self._guidance_alternative == 1:
                    self.conflict_preserve.setChecked(False)
                    self.mode_handling.setCurrentText("Adapt diatonic notes to target mode")
                    self.conflict_dynamic.setChecked(True)
                    self.auto_balance.setChecked(True)
                    treatment = "Adaptive: move diatonic material toward the shared target mode and normalize dynamics."
                else:
                    self.conflict_preserve.setChecked(True)
                    self.mode_handling.setCurrentText("Preserve source mode")
                    self.conflict_dynamic.setChecked(False)
                    self.auto_balance.setChecked(False)
                    treatment = "Originalist: preserve song notes, modes, and broad dynamics for the final mixer."
                self.conflict_note.setText(treatment)

        elif step == STEP_ARRANGEMENTS:
            advice = recipe_advice(profiles)
            if self.guidance_mode == "guided" or force:
                recommended = list(guided_recipe_keys(profiles, int(self.settings_store.get("guided_initial_take_count", 3))))
                eligible_order = [item.key for item in advice if item.eligible]
                if self._guidance_alternative and eligible_order:
                    shift = self._guidance_alternative % len(eligible_order)
                    rotated = eligible_order[shift:] + eligible_order[:shift]
                    recommended = rotated[: max(1, len(recommended))]
                selected = set(recommended)
                for key, button in self.recipe_checks.items():
                    raw = key[len(PAIR_PREFIX):] if key.startswith(PAIR_PREFIX) else key[len(MULTI_PREFIX):]
                    button.setChecked(button.isEnabled() and raw in selected)
                self.variations.setValue(1)
            elif self.guidance_mode == "assisted" and not any(button.isChecked() for button in self.recipe_checks.values()):
                self.select_recommended_recipes()
            lines = ["MIDI Remixer's decision", "", "Recommended arrangement paths"]
            for item in advice:
                marker = "✓" if item.eligible and any(
                    button.isChecked() for key, button in self.recipe_checks.items()
                    if key.endswith(item.key)
                ) else "—" if item.eligible else "×"
                lines.append(f"{marker} {item.label} — {item.recommendation}. {item.reason}")
            lines.extend(("", "Why", "• Recommendations are arrangement-specific. A song can be excellent for section exchange but unsuitable for direct melody/backing separation.", "• Guided Remix chooses different compositions; Version Fusion combines useful alternate arrangements of one composition; Assisted exposes broader alternatives; Custom / Advanced leaves all choices manual."))
            self._set_decision_text("arrangements", "\n".join(lines))

        elif step == STEP_TAKES:
            if self.selected_take:
                text = (
                    f"Selected: {self.selected_take.trial_name or self.selected_take.trial_id} / {self.selected_take.name}\n"
                    f"Applied target: {self.selected_take.target_key} at {self.selected_take.target_bpm} felt BPM; MIDI quarter={self.selected_take.encoded_bpm:g}.\n"
                    "The Take is key/tempo matched non-destructively. Compare it with the original songs, then choose 'This Doesn't Sound Right' to revise tempo, harmony, balance, structure, or instrumentation."
                )
            elif self.takes:
                text = "Single-click a Take. MIDI Remixer will show the exact applied target key, pulse, Trial, recipe, and decisions. Double-click to hear it."
            else:
                text = "No Takes yet. The next generation creates a Trial containing several Takes so alternate recommendations do not destroy earlier work."
            self._set_decision_text("takes", text)

        self._guidance_applied_steps.add(step)

    def _play_global_source_raw(self) -> None:
        index = self.global_source_combo.currentIndex()
        if 0 <= index < len(self.source_specs):
            self._play_source_spec_raw(self.source_specs[index])

    def _remove_global_source(self) -> None:
        index = self.global_source_combo.currentIndex()
        self._remove_spec_at(index)

    def mark_listening_check_passed(self) -> None:
        """Record human listening acceptance for the exact selected Take."""
        take = self.selected_take
        if take is None:
            QMessageBox.information(self, APP_NAME, "Select and listen to a Take first.")
            return
        review = ListeningReview.create(
            take.take_id, trial_id=take.trial_id, take_path=take.path, verdict="passed",
            notes="User marked the listening check as passed."
        )
        self.session_workspace.record_listening_review(review)
        chosen_key = take_identity_key(take)
        self.takes = [
            replace(item, approved=True, status="approved") if take_identity_key(item) == chosen_key else item
            for item in self.takes
        ]
        self.selected_take = next((item for item in self.takes if take_identity_key(item) == chosen_key), take)
        self.project.set_takes(self.takes)
        self.project.listening_reviews = list(self.session_workspace.manifest.listening_reviews)
        self._session_dirty = True
        self._refresh_takes()
        self.select_exact_take(self.selected_take)
        self.statusBar().showMessage(f"Listening check passed for {take.name}. This is human acceptance, not an automatic quality score.", 6000)

    def show_take_feedback(self) -> None:
        if not self.selected_take:
            QMessageBox.information(self, APP_NAME, "Select a Take first.")
            return
        choices = listening_issue_labels(include_pairing=False)
        choice, ok = QInputDialog.getItem(self, "What does not sound right?", "Choose the closest problem:", choices, 0, False)
        if not ok:
            return
        take = self.selected_take
        self.session_workspace.record_listening_review(
            ListeningReview.create(
                take.take_id,
                trial_id=take.trial_id,
                take_path=take.path,
                verdict="needs-revision",
                issues=(issue_for(choice).code,),
            )
        )
        self.project.listening_reviews = list(self.session_workspace.manifest.listening_reviews)
        self._session_dirty = True
        issue_code = issue_for(choice).code
        destination = STEP_FRAME
        if issue_code == "harmony-clash":
            destination = STEP_CONFLICTS
        elif issue_code in {"crowded", "repetitive", "transition", "identity", "timing-alignment"}:
            destination = STEP_ARRANGEMENTS
        elif issue_code == "instrumentation":
            destination = STEP_INSTRUMENTATION
        elif issue_code in {"melody-missing", "source-roles"}:
            destination = STEP_ROLES
        self.navigate_to(destination, force=True)
        if issue_code == "timing-fast":
            self.target_bpm.setValue(max(30, self.target_bpm.value() - 10))
        elif issue_code == "timing-slow":
            self.target_bpm.setValue(min(300, self.target_bpm.value() + 10))
        elif issue_code in {"balance-backing-loud", "balance-lead-loud", "crowded"}:
            self.auto_balance.setChecked(True)
        self.statusBar().showMessage(f"Returned to the relevant decision for: {choice}. Generate a new Trial when ready; the current Trial remains available.")

    def choose_start_mode(self, mode: str) -> None:
        if mode not in START_MODES:
            return
        self.start_mode = mode
        label, description = START_MODES[mode]
        self.start_mode_summary.setText(f"<b>{label}</b><br>{description}")
        self.navigate_to(STEP_SELECT_SOURCES)

    def navigate_to(self, index: int, *, force: bool = False) -> None:
        index = max(0, min(len(STEP_TITLES) - 1, int(index)))
        if not force and index > self.max_step_reached + 1:
            return
        self.max_step_reached = max(self.max_step_reached, index)
        self._navigating_steps = True
        self.tabs.setCurrentIndex(index)
        self.step_list.setCurrentRow(index)
        self._navigating_steps = False
        self._refresh_step_list()
        self.workflow_rail.setVisible(index != STEP_GUIDANCE)
        self.source_bar.setVisible(bool(self.source_specs) and index != STEP_GUIDANCE)
        self._set_global_transport_visible(index != STEP_GUIDANCE)
        self._update_navigation()
        self._refresh_decision_summaries()
        self._apply_guidance_for_step(index)
        self._refresh_audio_readiness_ui()

    def _step_clicked(self, row: int) -> None:
        if self._navigating_steps or row < 0:
            return
        if row <= self.max_step_reached:
            self.navigate_to(row, force=True)
        else:
            self._navigating_steps = True
            self.step_list.setCurrentRow(self.tabs.currentIndex())
            self._navigating_steps = False

    def _page_changed(self, index: int) -> None:
        self._set_global_transport_visible(index != STEP_GUIDANCE)
        if not self._navigating_steps:
            self._refresh_step_list()
            self._update_navigation()
            self._refresh_decision_summaries()
            self._apply_mode_readability()

    def _refresh_step_list(self) -> None:
        current = self.tabs.currentIndex()
        for index in range(self.step_list.count()):
            item = self.step_list.item(index)
            flags = item.flags()
            if index <= self.max_step_reached:
                item.setFlags(flags | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            else:
                item.setFlags((flags | Qt.ItemFlag.ItemIsSelectable) & ~Qt.ItemFlag.ItemIsEnabled)
        if self.step_list.currentRow() != current:
            self._navigating_steps = True
            self.step_list.setCurrentRow(current)
            self._navigating_steps = False

    def go_back(self) -> None:
        self.navigate_to(self.tabs.currentIndex() - 1, force=True)

    def go_next(self) -> None:
        current = self.tabs.currentIndex()
        message = self._next_block_reason(current)
        if message:
            QMessageBox.information(self, "Next decision", message)
            return
        if current == STEP_GUIDANCE and self.guidance_mode == "guided":
            self.launch_guided_wizard()
            return
        if current == STEP_GUIDANCE and self.guidance_mode == "fusion":
            self.launch_fusion_wizard()
            return
        if current == STEP_GUIDANCE and self.guidance_mode == "assisted":
            self.launch_assisted_wizard()
            return
        if current == STEP_ARRANGEMENTS:
            self.generate_takes()
            return
        if current == STEP_FINISH:
            return
        self.navigate_to(current + 1)

    def _next_block_reason(self, step: int) -> str | None:
        if step == STEP_GUIDANCE:
            return self._onboarding_block_reason(require_mode=True)
        if step == STEP_START_METHOD and not self.start_mode:
            return "Choose one of the starting paths first."
        if step == STEP_SELECT_SOURCES and not self.source_specs:
            return "Choose at least one song or explore the MIDI Library before continuing."
        if step == STEP_REVIEW_SOURCES and not self.source_specs:
            return "Add at least one song."
        if step == STEP_DISCOVERY and len(self.source_specs) < 2:
            return "Add at least one related song before moving into arrangement decisions."
        if step in {STEP_STRUCTURE, STEP_ROLES, STEP_FRAME, STEP_CONFLICTS, STEP_ARRANGEMENTS} and len(self.source_specs) < 2:
            return "Choose at least two active songs."
        if step == STEP_ARRANGEMENTS and not self._selected_recipe_keys():
            return "Select at least one visible arrangement approach."
        if step == STEP_TAKES and not self.selected_take:
            return "Single-click a generated Take before moving to instrumentation."
        if step in {STEP_INSTRUMENTATION, STEP_FINISH} and not self.selected_take:
            return "Select a Take first."
        return None

    def _update_navigation(self) -> None:
        step = self.tabs.currentIndex()
        self.back_button.setVisible(step > 0)
        self.back_button.setEnabled(step > 0)
        self.next_button.setVisible(step < len(STEP_TITLES) - 1)
        if step == STEP_GUIDANCE and not self._mode_choice_confirmed:
            self.next_button.setText("Choose a Working Mode Above")
        elif step == STEP_GUIDANCE and self.guidance_mode == "guided":
            self.next_button.setText("Start Guided Remix →")
        elif step == STEP_GUIDANCE and self.guidance_mode == "fusion":
            self.next_button.setText("Start Version Fusion →")
        elif step == STEP_GUIDANCE and self.guidance_mode == "assisted":
            self.next_button.setText("Start Assisted Studio →")
        elif step == STEP_GUIDANCE and self.guidance_mode == "workbench":
            self.next_button.setText("Start Creative Workbench →")
        elif step == STEP_ARRANGEMENTS:
            self.next_button.setText(self.generate_btn.text())
        elif step == STEP_TAKES:
            self.next_button.setText("Continue to Instrumentation →")
        elif step == STEP_INSTRUMENTATION:
            self.next_button.setText("Continue to Mix & Export →")
        else:
            self.next_button.setText("Next →")
        reason = self._next_block_reason(step)
        if reason:
            self.nav_hint.setText(reason)
        else:
            hints = {
                STEP_GUIDANCE: "Choose how much work MIDI Remixer should do and how much control should be exposed.",
                STEP_START_METHOD: "Choose a starting path. No permanent main song is required.",
                STEP_SELECT_SOURCES: "Choose or explore songs; audition is available in the MIDI Library.",
                STEP_REVIEW_SOURCES: "Confirm the songs and optionally change the temporary discovery focus.",
                STEP_DISCOVERY: "Review musical relationships and add useful related songs.",
                STEP_STRUCTURE: "Inspect song form, length, meter, key changes, and starting positions.",
                STEP_ROLES: "Assign melody, foundation, hook, section, and voice-extraction roles.",
                STEP_FRAME: "Choose target tempo, key, mode, duration, phrase block, density, and broad dynamics handling.",
                STEP_CONFLICTS: "Review measurable conflicts and decide what should remain untouched.",
                STEP_ARRANGEMENTS: "Select one or more arrangement approaches and generate variations.",
                STEP_TAKES: "Hear and compare generated Takes; select one to develop.",
                STEP_INSTRUMENTATION: "Review instrumentation warnings and edit the chosen ensemble.",
                STEP_FINISH: "Balance, save, export, and render the selected Take.",
            }
            self.nav_hint.setText(hints.get(step, ""))
        self.next_button.setEnabled(reason is None)

    # ----------------------------------------------------------- source flow
    def open_library_browser(self, *, purpose: str = "sources", selected_callback=None) -> None:
        """Open the persistent Library without confusing browse/anchor/source intents.

        The base class supports three explicit purposes. Only source-selection may
        mutate the Creative Workbench source set or advance its navigation; a
        browse-only or anchor-only dialog must not silently alter the mash.
        """
        # Library cataloguing and MIDI analysis do not require FluidSynth or a SoundFont.
        # Playback remains unavailable until audio setup is complete.
        before = len(self.source_specs)
        super().open_library_browser(purpose=purpose, selected_callback=selected_callback)
        if purpose == "sources" and len(self.source_specs) > before:
            self.max_step_reached = max(self.max_step_reached, STEP_REVIEW_SOURCES)
            if self.tabs.currentIndex() <= STEP_SELECT_SOURCES:
                self.navigate_to(STEP_REVIEW_SOURCES)
        self._refresh_decision_summaries()

    def _choose_library_folder(self, replace: bool = False, *, progress_parent=None, status_callback=None) -> bool:
        # Library Engine scanning/classification is independent of playback. A
        # user may build/attach the 96K library before FluidSynth/SoundFont is
        # configured; audio readiness is checked only when audition/generation
        # actually needs it. Choosing another root also does not invalidate the
        # user's working-mode choice or existing session.
        started = super()._choose_library_folder(
            replace=replace, progress_parent=progress_parent, status_callback=status_callback
        )
        self._refresh_decision_summaries()
        return started

    def add_files(self, *, progress_parent=None, status_callback=None) -> bool:
        started = super().add_files(progress_parent=progress_parent, status_callback=status_callback)
        self._refresh_decision_summaries()
        return started

    def refresh_library(self) -> None:
        super().refresh_library()

    def _add_source_paths(self, paths: list[str], replace: bool = False) -> None:
        current_step = self.tabs.currentIndex() if hasattr(self, "tabs") else 0
        super()._add_source_paths(paths, replace=replace)
        self.session_workspace.update_sources(spec.path for spec in self.source_specs)
        self._session_dirty = True
        # Parent sends the old UI to screen 0. Restore a logical guided position.
        target = STEP_REVIEW_SOURCES if self.source_specs else max(STEP_SELECT_SOURCES, current_step)
        self.navigate_to(target, force=True)

    def _refresh_source_set(self) -> None:
        # Source order controls the temporary discovery focus; musical role is
        # independent. Older projects may still carry the internal "anchor" role.
        if self.source_specs and self.source_specs[0].role == "anchor":
            self.source_specs[0] = replace(self.source_specs[0], role="auto")
        super()._refresh_source_set()
        for row, spec in enumerate(self.source_specs):
            combo = self.source_table.cellWidget(row, 2)
            if isinstance(combo, QComboBox):
                combo.blockSignals(True)
                anchor_index = combo.findData("anchor")
                if anchor_index >= 0:
                    combo.removeItem(anchor_index)
                combo.setEnabled(True)
                current_role = spec.role if spec.role != "anchor" else "auto"
                target_index = combo.findData(current_role)
                if target_index >= 0:
                    combo.setCurrentIndex(target_index)
                combo.blockSignals(False)
        self._refresh_decision_summaries()

    def _source_role_changed(self, index: int, role: str) -> None:
        if 0 <= index < len(self.source_specs):
            self.source_specs[index] = replace(self.source_specs[index], role=role if role != "anchor" else "auto")
            self._populate_recipe_table()
            self._refresh_decision_summaries()

    def set_selected_anchor(self) -> None:
        self._move_focus_to(self.source_table.currentRow())

    def remove_selected_source(self) -> None:
        self._remove_spec_at(self.source_table.currentRow())

    def _refresh_decision_summaries(self) -> None:
        if not hasattr(self, "selection_table"):
            return
        if self._classification_in_progress:
            self.start_library_status.setText("3. Refreshing committed MIDI Library state…")
        elif self.profiles:
            count = len(self.profiles)
            if self._library_policy_decision_pending:
                self.start_library_status.setText(
                    f"3. Library contains {count} MIDI file{'s' if count != 1 else ''}, but setup is still incomplete in MIDI Library Manager."
                )
            else:
                self.start_library_status.setText(
                    f"3. ✓ MIDI Library connected: {count} committed file{'s' if count != 1 else ''}."
                )
        elif self._normalised_library_paths():
            self.start_library_status.setText("3. MIDI Library connected, but no usable MIDI files are currently available.")
        else:
            self.start_library_status.setText("3. MIDI Library: open MIDI Library Manager to create or recover it.")
        self.start_library_button.setText("Open MIDI Library Manager…")
        self.start_soundfont_status.setText(self.soundfont_status.text())
        self.global_source_combo.blockSignals(True)
        self.global_source_combo.clear()
        for row, spec in enumerate(self.source_specs):
            profile = self.profiles.get(spec.path)
            self.global_source_combo.addItem(f"{row + 1}. {profile.title if profile else Path(spec.path).stem}", spec.path)
        self.global_source_combo.blockSignals(False)
        self.global_play_source_button.setEnabled(bool(self.source_specs))
        self.global_remove_source_button.setEnabled(bool(self.source_specs))
        self.discovery_empty_panel.setVisible(not self.source_specs)
        self.selection_table.setVisible(bool(self.source_specs))
        self.selection_table.setRowCount(len(self.source_specs))
        self.review_table.setRowCount(len(self.source_specs))
        self.discovery_focus_combo.blockSignals(True)
        self.discovery_focus_combo.clear()
        for row, spec in enumerate(self.source_specs):
            profile = self.profiles.get(spec.path)
            if not profile:
                continue
            use = "Current discovery focus" if row == 0 else spec.role.title()
            selection_values = [profile.title, profile.composer, profile.style, f"{profile.felt_bpm:g}" if profile.felt_bpm else "—", use]
            for column, text in enumerate(selection_values):
                item = QTableWidgetItem(str(text))
                item.setData(Qt.ItemDataRole.UserRole, spec.path)
                self.selection_table.setItem(row, column, item)
            ready = source_readiness(profile)
            review_values = [str(row + 1), profile.title, profile.style, profile.key_display, f"{profile.felt_bpm:g}" if profile.felt_bpm else "—", profile.arrangement_type, f"{ready.status} · {ready.confidence}% · orchestration {profile.orchestration_readiness}%"]
            for column, text in enumerate(review_values):
                item = QTableWidgetItem(str(text))
                item.setData(Qt.ItemDataRole.UserRole, spec.path)
                self.review_table.setItem(row, column, item)
            self.discovery_focus_combo.addItem(f"{row + 1}. {profile.title}", spec.path)
        self.discovery_focus_combo.blockSignals(False)
        self.review_count.setText(f"{len(self.source_specs)} song{'s' if len(self.source_specs) != 1 else ''}")
        self._refresh_structure_table()
        self._refresh_conflicts_table()
        self._refresh_frame_summary()
        self._refresh_take_stage_summaries()
        self._refresh_onboarding_controls()
        self._update_navigation()

    def _play_selection_raw(self) -> None:
        row = self.selection_table.currentRow()
        if row < 0 and self.source_specs:
            row = 0
        if 0 <= row < len(self.source_specs):
            self._play_source_spec_raw(self.source_specs[row])

    def _remove_selection_row(self) -> None:
        row = self.selection_table.currentRow()
        self._remove_spec_at(row)

    def _play_review_raw(self) -> None:
        row = self.review_table.currentRow()
        if row < 0 and self.source_specs:
            row = 0
        if 0 <= row < len(self.source_specs):
            self._play_source_spec_raw(self.source_specs[row])

    def _remove_review_row(self) -> None:
        self._remove_spec_at(self.review_table.currentRow())

    def _remove_spec_at(self, row: int) -> None:
        if not (0 <= row < len(self.source_specs)):
            return
        removed = self.source_specs.pop(row)
        self.session_workspace.push_undo("restore_source", {"index": row, "source": removed.__dict__})
        self.session_workspace.update_sources(spec.path for spec in self.source_specs)
        self._session_dirty = True
        self._refresh_source_set()

    def _set_review_focus(self) -> None:
        row = self.review_table.currentRow()
        self._move_focus_to(row)

    def _focus_combo_changed(self, index: int) -> None:
        if index > 0:
            self._move_focus_to(index)

    def _move_focus_to(self, row: int) -> None:
        if not (0 <= row < len(self.source_specs)) or row == 0:
            return
        spec = self.source_specs.pop(row)
        self.source_specs.insert(0, replace(spec, role="auto" if spec.role == "anchor" else spec.role))
        self._refresh_source_set()

    def _add_selected_candidate(self) -> None:
        row = self.relationship_table.currentRow()
        cell = self.relationship_table.item(row, 0) if row >= 0 else None
        path = str(cell.data(Qt.ItemDataRole.UserRole)) if cell and cell.data(Qt.ItemDataRole.UserRole) else None
        if not path:
            QMessageBox.information(self, APP_NAME, "Select a related song first.")
            return
        if path in {spec.path for spec in self.source_specs}:
            self.statusBar().showMessage("That song is already in this mashup.")
            return
        self._add_source_paths([path], replace=False)
        self.navigate_to(STEP_REVIEW_SOURCES, force=True)

    # -------------------------------------------------------- structure/frame
    def _refresh_structure_table(self) -> None:
        self.structure_table.setRowCount(len(self.source_specs))
        self._structure_spins = []
        for row, spec in enumerate(self.source_specs):
            profile = self.profiles.get(spec.path)
            if not profile:
                continue
            form = profile.progression_family or "Unclassified"
            notes = "; ".join(profile.warnings[:2]) if profile.warnings else "No special structural warning"
            values = [profile.title, form, f"{profile.bars:.1f}", profile.meter, profile.key_display, str(len(profile.key_changes)), "", notes]
            for column, text in enumerate(values):
                if column == 6:
                    continue
                self.structure_table.setItem(row, column, QTableWidgetItem(text))
            spin = QSpinBox()
            spin.setRange(0, max(0, int(profile.bars) - 1))
            spin.setValue(spec.start_bar)
            spin.valueChanged.connect(lambda value, index=row: self._structure_start_changed(index, value))
            self.structure_table.setCellWidget(row, 6, spin)
            self._structure_spins.append(spin)

    def _structure_start_changed(self, index: int, value: int) -> None:
        if 0 <= index < len(self.source_specs):
            self.source_specs[index] = replace(self.source_specs[index], start_bar=int(value))
            # Keep the Roles screen synchronized.
            role_spin = self.source_table.cellWidget(index, 3)
            if isinstance(role_spin, QSpinBox) and role_spin.value() != int(value):
                role_spin.blockSignals(True)
                role_spin.setValue(int(value))
                role_spin.blockSignals(False)

    def _refresh_frame_summary(self, *_args) -> None:
        profiles = self._effective_profiles() if self.source_specs else []
        if not profiles:
            self.frame_summary.setPlainText("Choose songs first.")
            return
        lines = ["Original song frames:"]
        for index, profile in enumerate(profiles, 1):
            lines.append(f"{index}. {profile.title}: {profile.felt_bpm or '—'} felt BPM · {profile.meter} · {profile.analyzed_key_display} · about {profile.bars:.1f} bars")
        lines.extend([
            "",
            f"Proposed target: {self.target_bpm.value()} felt BPM · {self.target_key.currentText()} · {self.target_mode.currentText()}",
            f"Length: {self.length_mode.currentText()} ({self.bars_spin.value()} bars) · phrase block {self.section_bars.value()} bars · density {self.density.currentText()}",
            "Original MIDI files are not changed; transformations are applied only to generated Takes.",
        ])
        self.frame_summary.setPlainText("\n".join(lines))

    def _refresh_conflicts_table(self) -> None:
        rows = list(self._relationship_rows)
        # Keep only relationships between selected source-set members on the conflict screen.
        selected = {spec.path for spec in self.source_specs}
        rows = [item for item in rows if item.source_b in selected]
        self.conflict_table.setRowCount(len(rows))
        for row, item in enumerate(rows):
            values = [
                f"{item.title_a} ↔ {item.title_b}",
                f"{score_band(item.harmonic_score)[0]} · {item.harmonic_score}%",
                f"{score_band(item.melody_a_over_b_score)[0]} · {item.melody_a_over_b_score}%",
                f"{score_band(item.melody_b_over_a_score)[0]} · {item.melody_b_over_a_score}%",
                f"{score_band(item.key_score)[0]} · {item.key_score}%",
                f"{score_band(item.tempo_score)[0]} · {item.tempo_score}%",
                f"{score_band(item.meter_score)[0]} · {item.meter_score}%",
                item.adaptation_required,
            ]
            for column, text in enumerate(values):
                cell = QTableWidgetItem(text)
                cell.setToolTip("\n".join(item.observations))
                self.conflict_table.setItem(row, column, cell)

    def _refresh_relationships(self) -> None:
        super()._refresh_relationships()
        self._refresh_conflicts_table()

    def _relationship_selection_changed(self) -> None:
        super()._relationship_selection_changed()
        text = self.relationship_detail.toPlainText()
        row = self.relationship_table.currentRow()
        cell = self.relationship_table.item(row, 0) if row >= 0 else None
        path = str(cell.data(Qt.ItemDataRole.UserRole)) if cell and cell.data(Qt.ItemDataRole.UserRole) else ""
        assessment = next((item for item in self._relationship_rows if item.source_b == path), None)
        explanation = ("\n\nWhat the compatibility values mean\n" + compatibility_explanation(assessment)) if assessment else ""
        self.relationship_detail.setPlainText(
            text.replace("anchor melody", "focus melody").replace("candidate melody over anchor", "related melody over focus").replace("candidate", "related song") + explanation
        )
        self._update_navigation()

    def _apply_mode_readability(self) -> None:
        """Keep Guided mode readable even if the user reaches the full workflow."""
        guided = getattr(self, "guidance_mode", "guided") == "guided"
        for key in ("arrangements", "takes"):
            group = getattr(self, f"{key}_decision_group", None)
            if group is not None:
                group.setVisible(not guided)

        if hasattr(self, "recipe_table"):
            self.recipe_table.setColumnHidden(3, guided)
            for row in range(self.recipe_table.rowCount()):
                button = self.recipe_table.cellWidget(row, 0)
                show = not guided or (isinstance(button, QPushButton) and button.isChecked())
                self.recipe_table.setRowHidden(row, not show)
                if show:
                    self.recipe_table.setRowHeight(row, 72 if guided else 58)

        for name in (
            "variations_label", "variations", "variation_strength_label", "variation_strength",
            "random_seed_label", "random_seed", "select_all_recipes_button", "clear_recipes_button",
            "open_session_button",
        ):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.setVisible(not guided)
        if hasattr(self, "recommend_recipes_button"):
            self.recommend_recipes_button.setVisible(not guided)

        if hasattr(self, "takes_table"):
            for column in (1, 4, 5, 6, 7, 8):
                self.takes_table.setColumnHidden(column, guided)
            self.takes_table.setSelectionMode(
                QAbstractItemView.SelectionMode.SingleSelection if guided
                else QAbstractItemView.SelectionMode.ExtendedSelection
            )
        for name in ("delete_take_button", "delete_trial_button", "undo_take_button"):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.setVisible(not guided)
        if hasattr(self, "approve_take_button"):
            self.approve_take_button.setText("Choose This Take" if guided else "Approve / Unapprove")
        if hasattr(self, "create_trial_button"):
            self.create_trial_button.setText("Try Different Versions" if guided else "Create Another Trial")

    def _populate_recipe_table(self) -> None:
        super()._populate_recipe_table()
        self._apply_mode_readability()

    def _refresh_takes(self) -> None:
        super()._refresh_takes()
        self._apply_mode_readability()

    def _update_generate_button(self, *_args) -> None:
        super()._update_generate_button(*_args)
        if hasattr(self, "next_button"):
            self._update_navigation()

    # -------------------------------------------------------------- workflow
    def _update_workflow_state(self) -> None:
        has_sources = bool(self.source_specs)
        has_pair = len(self.source_specs) >= 2
        ready = self.audio_ready()
        self.play_anchor_button.setEnabled(has_sources and ready)
        self.play_related_button.setEnabled(self.relationship_table.currentRow() >= 0 and ready)
        self.discovery_empty_panel.setVisible(not has_sources)
        self.source_frame.setVisible(has_sources)
        self.remove_source_button.setEnabled(has_sources and self.source_table.currentRow() >= 0)
        self.set_anchor_button.setEnabled(len(self.source_specs) > 1 and self.source_table.currentRow() > 0)
        self.transport_play.setEnabled(ready and (has_sources or bool(self.selected_take)))
        self.analysis_empty_panel.setVisible(not has_sources or self.relationship_table.rowCount() == 0)
        self.analysis_splitter.setVisible(has_sources and self.relationship_table.rowCount() > 0)
        if hasattr(self, "section_blueprint_handoff"):
            decisions = tuple(getattr(self, "assisted_section_decisions", ()) or ())
            self.section_blueprint_handoff.setVisible(bool(decisions))
            self.section_blueprint_handoff.setText(
                "ASSISTED SECTION PLAN PRESERVED\n" + "\n".join(f"• {item}" for item in decisions)
                if decisions else ""
            )
        if has_sources and self.source_specs[0].path in self.profiles:
            self.analysis_anchor_label.setText(f"Current discovery focus: {self.profiles[self.source_specs[0].path].title}")
        else:
            self.analysis_anchor_label.setText("Choose a song to use as a temporary discovery focus.")
        self._update_navigation()

    def continue_to_mash_builder(self) -> None:
        # In the decision workflow, Discovery is followed by Source Review and
        # Structure/Role decisions—not a jump over those decisions.
        self._add_selected_candidate()
        if len(self.source_specs) >= 2:
            self.navigate_to(STEP_STRUCTURE)

    def _update_length_from_sources(self) -> None:
        self._length_mode_changed(self.length_mode.currentText())

    def _length_mode_changed(self, mode: str) -> None:
        # Support old project/settings wording while exposing neutral UI wording.
        self.bars_spin.setEnabled(mode == "Custom bars")
        profiles = self._effective_profiles() if self.source_specs else []
        if not profiles:
            return
        if mode in {"Current focus full length", "Anchor full length"}:
            bars = int(profiles[0].bars)
        elif mode in {"Shortest selected source", "Shortest selected song"}:
            bars = int(min(profile.bars for profile in profiles))
        elif mode == "Shared 64-bar form":
            bars = 64
        else:
            self._refresh_frame_summary()
            return
        self.bars_spin.setValue(max(4, bars))
        self._refresh_frame_summary()

    def generate_takes(self) -> None:
        previous_count = len(self.takes)
        super().generate_takes()
        if len(self.takes) > previous_count:
            self.max_step_reached = max(self.max_step_reached, STEP_TAKES)
            self.navigate_to(STEP_TAKES, force=True)

    def generate_takes_embedded(self) -> None:
        """Generate for an embedded beginner wizard without exposing the dense Takes table."""
        previous_count = len(self.takes)
        MidiRemixerStudio.generate_takes(self)
        if len(self.takes) > previous_count:
            self.max_step_reached = max(self.max_step_reached, STEP_TAKES)
            self._refresh_takes()
            self._update_navigation()

    def _take_selection_changed(self) -> None:
        super()._take_selection_changed()
        if self.guidance_mode == "guided" and self.selected_take:
            take = self.selected_take
            label = self._recipe_labels().get(take.recipe or take.kind, (take.recipe or take.kind).replace("-", " ").title())
            self.take_detail.setPlainText(
                f"{take.name}\n\n"
                f"Arrangement: {label}\n"
                f"Target: {take.target_key} · {take.target_bpm} BPM · {take.bars} bars\n\n"
                "Play this Take and compare it with either original song. Choose it when it is the version you want to continue with. "
                "The original MIDI files remain unchanged."
            )
        self._refresh_take_stage_summaries()
        self._update_navigation()

    def _refresh_take_stage_summaries(self) -> None:
        if not hasattr(self, "instrument_take_label"):
            return
        if not self.selected_take:
            self.instrument_take_label.setText("Select a Take on Generate & Compare Takes.")
            self.instrument_warning_text.setPlainText("Instrumentation analysis will appear after a Take is selected.")
            self.finish_take_label.setText("No Take selected.")
            return
        take = self.selected_take
        source_paths = take.source_paths or tuple(path for path in (take.source_a, take.source_b) if path)
        profiles = [self.profiles[path] for path in source_paths if path in self.profiles]
        warnings: list[str] = []
        for profile in profiles:
            warnings.append(f"{profile.title} — {profile.arrangement_type}; orchestration readiness {profile.orchestration_readiness}%")
            warnings.extend(f"  • {warning}" for warning in profile.warnings[:4])
        self.instrument_take_label.setText(f"Selected: {take.name} · {take.target_bpm} felt BPM · {take.target_key} · {take.bars} bars")
        self.instrument_warning_text.setPlainText("\n".join(warnings) if warnings else "No song instrumentation warnings were reported.")
        self.finish_take_label.setText(f"Ready to finish: {take.name}\n{take.description}")

    def _update_take_actions(self) -> None:
        super()._update_take_actions()
        selected = self.selected_take is not None
        self._update_navigation()
        if hasattr(self, "listening_pass_button"):
            set_action_enabled(self.listening_pass_button, selected, "Select and play a Take before recording a listening check.")
        if hasattr(self, "take_feedback_button"):
            set_action_enabled(self.take_feedback_button, selected, "Select a Take before describing what does not sound right.")
        if hasattr(self, "finish_take_label") and not selected:
            self.finish_take_label.setText("No Take selected.")

    # ------------------------------------------------------------- transport
    def toggle_play_pause(self) -> None:
        if self.playback_engine.is_playing() or self.playback_engine.is_paused():
            self.pause_or_resume_current()
            return
        if self.tabs.currentIndex() >= STEP_TAKES and self.selected_take:
            self.play_selected_take()
        else:
            self.play_selected_source_raw()

    def stop_playback(self) -> None:
        # Shared transport controller keeps the main window and child dialogs in sync.
        super().stop_playback()
        self.playback_label.setText(self.selected_take.name if self.selected_take and self.tabs.currentIndex() >= STEP_TAKES else "Stopped")

    # --------------------------------------------------------------- soundfont
    def _set_soundfont_state(self, path: str | None) -> None:
        super()._set_soundfont_state(path)
        if hasattr(self, "start_soundfont_status"):
            self.start_soundfont_status.setText(self.soundfont_status.text())
        # Setup actions live in the first-screen readiness panel and Settings.
        self.load_soundfont_button.setVisible(False)
        self._refresh_audio_readiness_ui()

    def _update_library_status(self) -> None:
        super()._update_library_status()
        if not hasattr(self, "start_library_status"):
            return
        configured = self._normalised_library_paths()
        total = len(self.profiles)
        ready = max(0, min(int(getattr(self, "_library_cached_ready_count", 0) or 0), total))
        pending = max(0, total - ready)
        distinct = int(getattr(self, "_library_cached_distinct_count", 0) or 0)
        header_button = getattr(self, "header_library_button", None)
        if self._classification_in_progress:
            self.start_library_status.setText(
                "3. Refreshing committed MIDI Library state…"
            )
        elif self.profiles:
            policy_note = (
                " Choose the deeper-analysis option below."
                if self._library_policy_decision_pending else ""
            )
            distinct_note = (
                f" · about {distinct} distinct song{'s' if distinct != 1 else ''}" if distinct > 0 else ""
            )
            self.start_library_status.setText(
                f"3. MIDI Library connected: {total} file{'s' if total != 1 else ''} · "
                f"{ready} deeply analyzed · {pending} pending{distinct_note}."
                + policy_note
            )
        elif configured:
            self.start_library_status.setText(
                "3. MIDI Library connected, but no usable MIDI files are available yet."
            )
        else:
            self.start_library_status.setText("3. MIDI Library: not established")

        folder_label = "Open MIDI Library Manager…"
        self.start_library_button.setText(folder_label)
        if header_button is not None:
            header_button.setText(folder_label)
        self._refresh_onboarding_controls()

    # -------------------------------------------------------------- projects
    def _after_project_loaded(self, _path: str, *, recovered: bool = False) -> None:
        if self.source_specs:
            self.max_step_reached = STEP_TAKES if self.takes else STEP_REVIEW_SOURCES
            self.navigate_to(STEP_TAKES if self.takes else STEP_REVIEW_SOURCES, force=True)
        else:
            self.max_step_reached = STEP_START_METHOD
            self.navigate_to(STEP_START_METHOD, force=True)
        if recovered:
            self._session_dirty = True
            self.statusBar().showMessage("Recovered session opened. Save, continue, or reject it when finished.")


def main() -> int:
    import sys

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("MIDI Remixer")
    app.setQuitOnLastWindowClosed(True)

    # Prevent accidental duplicate Python/FluidSynth stacks from repeated clicks.
    qa_lock = os.environ.get("MIDI_REMIXER_QA_INSTANCE_LOCK", "").strip()
    lock_path = Path(qa_lock) if qa_lock else Path(tempfile.gettempdir()) / "midi-remixer-single-instance.lock"
    instance_lock = QLockFile(str(lock_path))
    instance_lock.setStaleLockTime(30_000)
    if not instance_lock.tryLock(150):
        if instance_lock.removeStaleLockFile() and instance_lock.tryLock(150):
            pass
        else:
            QMessageBox.information(
                None,
                "MIDI Remixer is already running",
                "Only one MIDI Remixer instance can run at a time. Close the existing window before launching another copy.",
            )
            return 0

    window = DecisionWorkflowStudio()
    app.aboutToQuit.connect(window.shutdown_runtime)

    previous_hook = sys.excepthook
    handling_exception = False

    def cleanup_exception_hook(exc_type, exc_value, exc_traceback) -> None:
        """Fail closed after an unexpected Qt callback exception.

        Recoverable operation errors are handled at their action boundary. An
        exception that reaches sys.excepthook means UI/model state may be only
        partially mutated, so continuing is less safe than a controlled shutdown.
        """
        nonlocal handling_exception
        if exc_type in (KeyboardInterrupt, SystemExit):
            previous_hook(exc_type, exc_value, exc_traceback)
            app.exit(1 if exc_type is not SystemExit else 0)
            return
        if handling_exception:
            previous_hook(exc_type, exc_value, exc_traceback)
            app.exit(1)
            return
        handling_exception = True
        try:
            error = exc_value if isinstance(exc_value, BaseException) else RuntimeError(str(exc_value))
            write_diagnostic(window.paths.logs / "ui-errors.log", "Fatal uncaught Qt callback", error)
            error_text = concise_exception(error)
            location = traceback_location(error)
            previous_hook(exc_type, exc_value, exc_traceback)
            try:
                QMessageBox.critical(
                    None,
                    "MIDI Remixer must close",
                    "An unexpected internal error occurred. To protect session and playback state, "
                    "MIDI Remixer will perform a controlled shutdown instead of continuing in an unknown state.\n\n"
                    + error_text
                    + (f"\nLocation: {location}" if location else "")
                    + "\n\nDetails were written to the per-user ui-errors.log. Reopen the app and recover the autosaved session if offered.",
                )
            except RuntimeError as dialog_exc:
                # The failing callback may already involve a deleted Qt wrapper.
                write_diagnostic(window.paths.logs / "ui-errors.log", "Fatal exception dialog", dialog_exc)
            try:
                window.shutdown_runtime()
            finally:
                app.exit(1)
        finally:
            handling_exception = False

    sys.excepthook = cleanup_exception_hook

    def request_quit(_signum, _frame) -> None:
        runtime_supervisor.request_shutdown()
        app.quit()

    for signal_name in ("SIGINT", "SIGTERM"):
        value = getattr(signal, signal_name, None)
        if value is not None:
            try:
                signal.signal(value, request_quit)
            except (ValueError, OSError) as exc:
                write_diagnostic(window.paths.logs / "ui-errors.log", f"Register {signal_name} handler", exc)

    window.show()
    qa_autoclose = os.environ.get("MIDI_REMIXER_QA_AUTOCLOSE_MS", "").strip()
    if qa_autoclose:
        try:
            delay_ms = max(1, min(30_000, int(qa_autoclose)))
        except ValueError:
            delay_ms = 500
        schedule_owned(window, "qa-autoclose", delay_ms, window.close)
    try:
        return app.exec()
    finally:
        window.shutdown_runtime()
        instance_lock.unlock()
