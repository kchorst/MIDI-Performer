from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
import json
import shutil
import subprocess
import sys
import statistics
import threading
import weakref
from typing import Any

from PySide6.QtCore import QByteArray, QFileSystemWatcher, Qt, QTimer
from PySide6.QtGui import QAction, QBrush, QCloseEvent, QColor, QCursor
from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QFrame,
    QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
    QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QScrollArea, QSlider, QSizePolicy, QSpinBox, QSplitter, QTableWidget,
    QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from app.controllers.audio_setup import AudioSetupController
from app.controllers.export import ExportController
from app.controllers.library import LibraryController
from app.controllers.playback import PlaybackController
from app.controllers.performance import PerformanceController
from app.controllers.refinement import RefinementController
from app.controllers.session import SessionController
from app.controllers.lifecycle import ShutdownCoordinator
from app.button_policy import install_button_policy, set_action_enabled
from app.deferred_actions import cancel_owned, schedule_owned
from app.operation_manager import OperationManager
from app.theme import load_stylesheet
from app.ui_components import (
    HoverTableWidget, LibraryScanBridge, SORT_ROLE, SortableItem,
    decode_qbytearray as _decode_qbytearray, encode_qbytearray as _encode_qbytearray,
)
from app.mixer_dialog import InstrumentationMixerDialog
from app.settings_dialog import SettingsDialog
from app.source_track_dialog import SourceTrackDialog
from core.app_paths import AppPaths
from core.app_settings import AppSettings
from core.atomic_io import atomic_write_json
from core.candidate_index import close_candidate_index_caches
from core.dependency_setup import find_fluidsynth, find_soundfont
from core.fluidsynth_player import FluidSynthPlayer
from core.library_index import LibraryIndex
from core.logging_config import append_bounded, clear_logs
from core.pair_assessment_cache import configure_pair_assessment_cache, get_pair_assessment_cache, close_pair_assessment_cache
from core.pair_dispositions import PairDispositionStore
from core.operation_diagnostics import operation_failure, write_diagnostic
from core.staged_library import enrich_deep_profile, is_provisional_profile, quick_result_to_profile
from core.remix_guidance import recipe_advice
from core.session_workspace import SessionWorkspace
from core.multi_source import (
    DEFAULT_MULTI_SOURCE_RECIPE_KEYS, MULTI_SOURCE_RECIPES, MultiSourceSettings,
    SourceSpec, generate_multi_source_takes, recommended_multi_source_recipes,
)
from core.playback_engine import PlaybackEngine
from core.reference_catalog import ReferenceCatalog
from core.remix_engine import (
    DEFAULT_TAKE_STRATEGY_KEYS, TAKE_STRATEGIES, RemixSettings, RemixTake,
    generate_remix_takes, remix_take_from_dict, take_identity_key,
)
from core.remix_profile import MidiProfile, NOTE_NAMES, PairAssessment, assess_pair
from core.guided_beginner import score_band
from core.remix_project import MidiRemixerProject
from core.remix_version import APP_NAME, APP_VERSION_DISPLAY, TAGLINE
from core.runtime_cleanup import runtime_supervisor
from core.take_quality import inspect_generated_take
from app.remixer_library_attachment import RemixerLibraryAttachment
from app.offline_library_index import UnavailableLibraryIndex

from app.studio_library_mixin import StudioLibraryMixin


PAIR_PREFIX = "pair:"
MULTI_PREFIX = "multi:"
MAX_ACTIVE_SOURCES = 12

class MidiRemixerStudio(StudioLibraryMixin, QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.root = Path(__file__).resolve().parents[1]  # immutable application files
        self.paths = AppPaths.for_app(self.root).ensure()
        migration_report = self.paths.migrate_legacy_state()
        if migration_report.get("status") not in {"complete", "already-recorded"}:
            append_bounded(
                self.paths.logs / "migration.log",
                json.dumps(migration_report, sort_keys=True, default=str) + "\n",
            )
        self.paths.cleanup_stale_partials()
        self._prepare_persistent_library_storage()
        self.settings_store = AppSettings(self.root, self.paths.config)
        configure_pair_assessment_cache(self.paths.cache_root / "pair_assessments.sqlite3")
        self.pair_dispositions = PairDispositionStore(self.paths.config / "remixer_pair_dispositions.sqlite3")
        # Library Engine owns scanning, detailed-analysis workers and persistent writes.
        # Remixer attaches to committed data through a read-only view so library work
        # can continue independently without competing with the creative UI process.
        self.library_scan = RemixerLibraryAttachment(self.root)
        self.library_engine_client = self.library_scan.client
        self.library_index = (
            LibraryIndex(self.library_scan.library_index_path, read_only=True)
            if self.library_scan.available else UnavailableLibraryIndex()
        )
        self._quick_results = {}
        # Paths retired by a replacement library scan must not be reinserted
        # if an already-running deep-analysis job finishes a moment later.
        self._retired_library_paths: set[str] = set()
        self.library_scan_bridge = LibraryScanBridge(self)
        self.library_scan_bridge.startupComplete.connect(self._finish_library_startup_load)
        self.library_scan_bridge.startupFailed.connect(self._fail_library_startup_load)
        self.library_scan_bridge.deepState.connect(self._on_deep_analysis_state)
        self.guidance_mode = str(self.settings_store.get("guidance_mode", "guided"))
        self.session_base = self.paths.sessions
        SessionWorkspace.cleanup_retention(self.session_base, empty_after_hours=0)
        self._recoverable_sessions = SessionWorkspace.discover_recoverable(self.session_base)
        self.session_workspace = SessionWorkspace(self.session_base)
        self.session_workspace.set_guidance_mode(self.guidance_mode)
        self.project = MidiRemixerProject(guidance_mode=self.guidance_mode, session_id=self.session_workspace.session_id)
        self.current_trial_id: str | None = None
        self._session_dirty = False
        self._session_saved = False
        self.profiles: dict[str, MidiProfile] = {}
        self.source_specs: list[SourceSpec] = []
        self.takes: list[RemixTake] = []
        self.selected_take: RemixTake | None = None
        self.current_playback_path: str | None = None
        self.current_playback_label: str | None = None
        self.current_playback_key: str | None = None
        self.current_playback_generation: int = 0
        self._pending_open_midi_paths: set[str] = set()
        self.library_selector_dialog = None
        self.mixer_dialog: InstrumentationMixerDialog | None = None
        self._classification_in_progress = False
        self._hover_take_row = -1
        self._relationship_rows: list[PairAssessment] = []
        self._library_dirty = False
        self._shutdown_requested = False
        self._shutdown_complete = False
        self.shutdown_coordinator = ShutdownCoordinator(self)
        self._quick_scan_thread: threading.Thread | None = None
        # Compatibility mirror for older diagnostics/tests. Process ownership is
        # now isolated in DeepAnalysisProcessRunner.
        self._deep_analysis_process: subprocess.Popen | None = None
        # UI progress callbacks may belong to temporary Guided/Assisted screens.
        # Keep bound callbacks weakly and detach them when their QObject is closed
        # so a completed background scan cannot call a deleted PySide wrapper.
        # Canonical onboarding compatibility state. A remix mode may not start while
        # the committed Library view is loading/refreshing or Library-owned setup remains incomplete.
        self._library_quick_scan_completed = False
        self._library_policy_decision_pending = False
        self._library_cached_ready_count = self._library_cached_pending_count = 0
        self._library_cached_guided_ready_count = 0
        self._library_cached_assisted_ready_count = 0
        self._deep_completed_session: set[str] = set()
        self._library_cached_distinct_count = 0
        # Monotonic in-process catalogue revision. Detailed-profile enrichment
        # does not change this value, so Assisted Back/Forward can reuse a
        # recommendation batch while a 96k background deep scan continues.
        self._library_catalogue_revision = 0
        self._show_selected_deep_policy = True
        # Pair-engine contribution defaults. Guided/Assisted modes may set these
        # transparently; Creative Workbench can expose them in later detail views.
        self.pair_backing_mode = "all-except-melody"
        self.pair_lead_gain_percent = 100
        self.pair_backing_gain_percent = 100
        self.preserve_signature_assets = True
        self.signature_source = "auto"
        self.signature_placement = "intro-alone"
        # Shared Assisted/Workbench arrangement blueprint. Empty values preserve
        # each recipe's original source order.
        self.assisted_section_source_order: tuple[int, ...] = ()
        self.assisted_section_labels: tuple[str, ...] = ()
        self.assisted_section_decisions: tuple[str, ...] = ()
        # Composition controllers own cross-screen state transitions while the
        # main window remains the public compatibility surface for older UI code.
        self.library_controller = LibraryController(self)
        runtime_supervisor.register_cleanup("Candidate discovery index", close_candidate_index_caches)
        runtime_supervisor.register_cleanup("Pair assessment cache", close_pair_assessment_cache)
        runtime_supervisor.register_cleanup("Pair disposition store", self.pair_dispositions.close)
        self.session_controller = SessionController(self)
        self.export_controller = ExportController(self)
        self.operation_manager = OperationManager(self)
        runtime_supervisor.register_cleanup("Background UI operations", self.operation_manager.shutdown)
        fs_path = find_fluidsynth(self.root, self.settings_store.get("fluidsynth_path"))
        self.player = FluidSynthPlayer(self.root, fs_path)
        self.playback_engine = PlaybackEngine(
            self.player,
            prepare_midi=lambda source, label, version_key: str(source),
            duration_seconds=lambda source: __import__("mido").MidiFile(source).length,
        )
        self.playback_engine.set_audio_driver(self.settings_store.get("audio_driver", "default"))
        self.playback_controller = PlaybackController(self)
        self.refinement_controller = RefinementController(self)
        self.performance_controller = PerformanceController(self)
        self.audio_setup_controller = AudioSetupController(self)
        runtime_supervisor.register_cleanup("FluidSynth renderer/player", self.player.stop)
        runtime_supervisor.register_cleanup("Playback scheduler and live synth", self.playback_engine.close)
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(250)
        self.poll_timer.timeout.connect(self._poll_playback)
        self.autosave_timer = QTimer(self)
        self.autosave_timer.setInterval(60_000)
        self.autosave_timer.timeout.connect(self._autosave_active_session)
        self.autosave_timer.start()
        runtime_supervisor.register_cleanup("Session autosave timer", self.autosave_timer.stop)

        self._configure_library_monitoring_timers()
        self.setWindowTitle(f"{APP_NAME} {APP_VERSION_DISPLAY}")
        self.resize(1180, 780)
        self.setMinimumSize(760, 540)
        self._build_actions()
        self._build_ui()
        self._apply_style()
        install_button_policy(self)
        self._restore_settings()
        schedule_owned(self, "initialize-library", 160, self._initialize_library)
        schedule_owned(self, "offer-recovery", 420, self._offer_recoverable_session)

    # ---------- actions ----------
    def _build_actions(self) -> None:
        file_menu = self.menuBar().addMenu("File")
        open_project = QAction("Open Remix Project…", self)
        open_project.triggered.connect(self.open_project)
        save_project = QAction("Save Remix Project…", self)
        save_project.triggered.connect(self.save_project)
        save_later = QAction("Save Session for Later", self)
        save_later.triggered.connect(self.save_session_for_later)
        reject_session = QAction("Reject Session and Clean Up…", self)
        reject_session.triggered.connect(self.reject_session)
        open_library = QAction("Open MIDI Library Manager…", self)
        open_library.triggered.connect(self._open_library_manager)
        select_sources = QAction("Select Remix Sources from Library…", self)
        select_sources.triggered.connect(lambda _checked=False: self.open_library_browser(purpose="sources"))
        open_midi = QAction("Open Library-Approved MIDI File into Current Mashup…", self)
        open_midi.triggered.connect(self.open_midi_files_into_mash)
        review_suspicious = QAction("Review Library Health in MIDI Library Manager…", self)
        review_suspicious.triggered.connect(self._open_library_manager)
        refresh = QAction("Refresh Committed Library View", self)
        refresh.triggered.connect(self.refresh_library)
        ignored_pairs = QAction("Manage Suppressed Mash / Fusion Ideas…", self)
        ignored_pairs.triggered.connect(self.manage_ignored_mash_ideas)
        output = QAction("Choose Working Output Folder…", self)
        output.triggered.connect(self.choose_output_directory)
        file_menu.addActions([open_project, save_project, save_later])
        file_menu.addAction(reject_session)
        file_menu.addSeparator()
        file_menu.addActions([open_library, select_sources, open_midi, refresh, review_suspicious, ignored_pairs])
        file_menu.addSeparator()
        file_menu.addAction(output)
        file_menu.addSeparator()
        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        settings_menu = self.menuBar().addMenu("Settings")
        preferences = QAction("Preferences…", self)
        preferences.triggered.connect(self.show_settings)
        soundfont = QAction("Choose Instrument Library…", self)
        soundfont.triggered.connect(self.choose_soundfont)
        audio = QAction("Audio Setup…", self)
        audio.triggered.connect(self.show_audio_setup)
        import_references = QAction("Import Reference Evidence…", self)
        import_references.triggered.connect(self.import_reference_evidence)
        reference_summary = QAction("Reference Evidence Summary…", self)
        reference_summary.triggered.connect(self.show_reference_evidence_summary)
        settings_menu.addActions([preferences, soundfont, audio])
        settings_menu.addSeparator()
        settings_menu.addActions([import_references, reference_summary])

        view_menu = self.menuBar().addMenu("View")
        fit = QAction("Fit Window to Current Screen", self)
        fit.triggered.connect(self.fit_window_to_screen)
        reset = QAction("Reset Workspace Layout", self)
        reset.triggered.connect(self.reset_layout)
        view_menu.addActions([fit, reset])

        help_menu = self.menuBar().addMenu("Help")
        diagnostics = QAction("Runtime Diagnostics…", self)
        diagnostics.triggered.connect(self.show_runtime_diagnostics)
        clear_diagnostics = QAction("Clear Diagnostic Logs…", self)
        clear_diagnostics.triggered.connect(self.clear_diagnostic_logs)
        about = QAction("About MIDI Remixer", self)
        about.triggered.connect(self.show_about)
        help_menu.addActions([diagnostics, clear_diagnostics, about])

    # ---------- UI ----------
    def _build_ui(self) -> None:
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(8, 5, 8, 7)
        outer.setSpacing(5)

        header = QHBoxLayout()
        header.setSpacing(6)
        self.title_label = QLabel(APP_NAME)
        self.title_label.setObjectName("AppTitle")
        self.library_status = QLabel("Library not established")
        self.library_status.setObjectName("StatusPill")
        self.soundfont_status = QLabel("SoundFont: not selected")
        self.soundfont_status.setObjectName("StatusPill")
        self.soundfont_status.setMinimumWidth(210)
        self.soundfont_status.setMaximumWidth(420)
        self.soundfont_status.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.load_soundfont_button = QPushButton("Load Instrument Library…")
        self.load_soundfont_button.setObjectName("UtilityButton")
        self.load_soundfont_button.clicked.connect(self.choose_soundfont)
        self.audio_status = QLabel("Audio setup required")
        self.audio_status.setObjectName("WarningPill")
        self.audio_setup_button = QPushButton("Audio Setup…")
        self.audio_setup_button.clicked.connect(self.show_audio_setup)
        self.header_library_button = QPushButton("Open MIDI Library Manager…")
        self.header_library_button.setObjectName("UtilityButton")
        self.header_library_button.clicked.connect(self.open_or_setup_library)
        header.addWidget(self.title_label)
        header.addWidget(self.library_status)
        header.addStretch(1)
        header.addWidget(self.soundfont_status)
        header.addWidget(self.load_soundfont_button)
        header.addWidget(self.audio_status)
        header.addWidget(self.audio_setup_button)
        header.addWidget(self.header_library_button)
        outer.addLayout(header)

        outer.addWidget(self._build_library_scan_panel())

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self._build_discovery_tab(), "1. Songs")
        self.tabs.addTab(self._build_analysis_tab(), "2. Related Songs")
        self.tabs.addTab(self._build_builder_tab(), "3. Mash Builder")
        self.tabs.addTab(self._build_takes_tab(), "4. Takes & Mixer")
        outer.addWidget(self.tabs, 1)

        self.global_transport = QFrame()
        self.global_transport.setObjectName("ActionFooter")
        transport = QHBoxLayout(self.global_transport)
        transport.setContentsMargins(7, 5, 7, 5)
        self.transport_play = QPushButton("Play")
        self.transport_play.setObjectName("PlayButton")
        self.transport_play.clicked.connect(self.toggle_play_pause)
        self.transport_stop = QPushButton("Stop")
        self.transport_stop.setObjectName("StopButton")
        self.transport_stop.clicked.connect(self.stop_playback)
        self.playback_label = QLabel("Ready")
        self.playback_label.setObjectName("StatusPill")
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 200)
        self.volume_slider.setMaximumWidth(260)
        self.volume_value = QLabel("100%")
        self.volume_slider.valueChanged.connect(self._volume_changed)
        transport.addWidget(self.transport_play)
        transport.addWidget(self.transport_stop)
        transport.addWidget(self.playback_label, 1)
        transport.addWidget(QLabel("Volume"))
        transport.addWidget(self.volume_slider)
        transport.addWidget(self.volume_value)
        outer.addWidget(self.global_transport)
        self.setCentralWidget(central)

    def _build_library_scan_panel(self) -> QFrame:
        """Observer-only Library status panel; maintenance lives in Library Manager."""
        panel = QFrame()
        panel.setObjectName("LibraryScanPanel")
        scan_layout = QVBoxLayout(panel)
        scan_layout.setContentsMargins(11, 8, 11, 8)
        scan_layout.setSpacing(6)

        header = QHBoxLayout()
        header.setSpacing(8)
        self.library_scan_panel_label = QLabel("Reading committed MIDI Library state…")
        self.library_scan_panel_label.setObjectName("LibraryScanTitle")
        self.library_scan_panel_label.setWordWrap(True)
        self.library_scan_manager_button = QPushButton("Open MIDI Library Manager…")
        self.library_scan_manager_button.clicked.connect(self._open_library_manager)
        header.addWidget(self.library_scan_panel_label, 1)
        header.addWidget(self.library_scan_manager_button)
        scan_layout.addLayout(header)

        self.library_scan_progress = QProgressBar()
        self.library_scan_progress.setRange(0, 0)
        self.library_scan_progress.setTextVisible(True)
        self.library_scan_progress.setFormat("Reading Library state…")
        scan_layout.addWidget(self.library_scan_progress)

        self.library_scan_detail_label = QLabel(
            "MIDI Library Manager/Engine owns scanning, analysis, lifecycle, eligibility, repair, and maintenance."
        )
        self.library_scan_detail_label.setObjectName("LibraryScanDetail")
        self.library_scan_detail_label.setWordWrap(True)
        scan_layout.addWidget(self.library_scan_detail_label)

        panel.setVisible(False)
        self.library_scan_panel = panel
        self._set_library_scan_panel_mode("hidden")
        return panel

    def _build_discovery_tab(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(3, 4, 3, 4)
        root.setSpacing(5)

        self.discovery_empty_panel = QFrame()
        self.discovery_empty_panel.setObjectName("EmptyState")
        empty_layout = QVBoxLayout(self.discovery_empty_panel)
        empty_layout.setContentsMargins(22, 22, 22, 22)
        empty_layout.setSpacing(8)
        empty_title = QLabel("Start with a song")
        empty_title.setObjectName("EmptyTitle")
        empty_text = QLabel(
            "Open MIDI Library Manager to create or recover your authoritative MIDI Library. "
            "After eligible songs are committed there, return to Remixer, browse the Library, listen, and add one to the mashup."
        )
        empty_text.setWordWrap(True)
        empty_text.setObjectName("Subtitle")
        empty_actions = QHBoxLayout()
        empty_library = QPushButton("Open MIDI Library Manager…")
        empty_library.setObjectName("PrimaryButton")
        empty_library.clicked.connect(self.open_or_setup_library)
        empty_setup = QPushButton("Library Setup…")
        empty_setup.clicked.connect(self.show_library_setup_prompt)
        empty_actions.addWidget(empty_library)
        empty_actions.addWidget(empty_setup)
        empty_actions.addStretch(1)
        empty_layout.addWidget(empty_title)
        empty_layout.addWidget(empty_text)
        empty_layout.addLayout(empty_actions)
        root.addWidget(self.discovery_empty_panel)

        self.soundfont_banner = QFrame()
        self.soundfont_banner.setObjectName("WarningPanel")
        sf_layout = QHBoxLayout(self.soundfont_banner)
        sf_layout.setContentsMargins(10, 7, 10, 7)
        self.soundfont_banner_label = QLabel("No instrument library is loaded. MIDI export works, but playback and audio rendering need a SoundFont.")
        self.soundfont_banner_label.setWordWrap(True)
        sf_button = QPushButton("Load Instrument Library…")
        sf_button.setObjectName("PrimaryButton")
        sf_button.clicked.connect(self.choose_soundfont)
        sf_layout.addWidget(self.soundfont_banner_label, 1)
        sf_layout.addWidget(sf_button)
        root.addWidget(self.soundfont_banner)

        source_header = QHBoxLayout()
        heading = QLabel("Songs in This Mashup")
        heading.setObjectName("SectionTitle")
        self.source_count = QLabel("0 songs")
        self.source_count.setObjectName("StatusPill")
        open_library = QPushButton("Add from Library…")
        open_library.setObjectName("UtilityButton")
        open_library.clicked.connect(lambda _checked=False: self.open_library_browser(purpose="sources"))
        open_midi = QPushButton("Open MIDI File…")
        open_midi.setObjectName("UtilityButton")
        open_midi.clicked.connect(self.open_midi_files_into_mash)
        self.remove_source_button = QPushButton("Remove Selected")
        self.remove_source_button.clicked.connect(self.remove_selected_source)
        self.set_anchor_button = QPushButton("Make Selected the Main Song")
        self.set_anchor_button.clicked.connect(self.set_selected_anchor)
        self.play_anchor_button = QPushButton("Play Main Song")
        self.play_anchor_button.setObjectName("PlayButton")
        self.play_anchor_button.clicked.connect(self.play_anchor_raw)
        source_header.addWidget(heading)
        source_header.addWidget(self.source_count)
        source_header.addStretch(1)
        source_header.addWidget(self.play_anchor_button)
        source_header.addWidget(self.set_anchor_button)
        source_header.addWidget(self.remove_source_button)
        source_header.addWidget(open_midi)
        source_header.addWidget(open_library)
        root.addLayout(source_header)

        self.source_table = QTableWidget(0, 7)
        self.source_table.setHorizontalHeaderLabels(["#", "Song", "What it provides", "Start bar", "Melody use", "Remix readiness", "Instrument roles"])
        self.source_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.source_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.source_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.source_table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.source_table.setMinimumHeight(105)
        self.source_table.setMaximumHeight(285)
        self.source_table.verticalHeader().setDefaultSectionSize(34)
        sh = self.source_table.horizontalHeader()
        sh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        sh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for col in (2, 3, 4, 5, 6):
            sh.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.source_table.itemSelectionChanged.connect(self._source_selection_changed)
        self.source_table.cellDoubleClicked.connect(lambda *_: self.play_selected_source_raw())
        self.source_frame = self._framed("Selected songs — the first row is the main song", self.source_table)
        root.addWidget(self.source_frame, 0)

        next_hint = QLabel(
            "Next: choose Find Related Songs. MIDI Remixer searches the indexed library around the main song without rescanning it."
        )
        next_hint.setObjectName("Subtitle")
        next_hint.setWordWrap(True)
        root.addWidget(next_hint)
        root.addStretch(1)

        bottom = QHBoxLayout()
        self.discovery_next = QPushButton("Find Related Songs")
        self.discovery_next.setObjectName("PrimaryButton")
        self.discovery_next.clicked.connect(lambda: self.tabs.setCurrentIndex(1))
        self.discovery_next.setEnabled(False)
        bottom.addStretch(1)
        bottom.addWidget(self.discovery_next)
        root.addLayout(bottom)
        return page

    def _build_analysis_tab(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(3, 4, 3, 4)
        root.setSpacing(5)

        head = QHBoxLayout()
        title = QLabel("What MIDI Remixer found")
        title.setObjectName("SectionTitle")
        self.analysis_anchor_label = QLabel("Choose a main song on the Songs screen.")
        self.analysis_anchor_label.setObjectName("StatusPill")
        back = QPushButton("← Back to Songs")
        back.setObjectName("BackButton")
        back.clicked.connect(lambda: self.tabs.setCurrentIndex(0))
        self.play_related_button = QPushButton("Play Selected Related Song")
        self.play_related_button.setObjectName("PlayButton")
        self.play_related_button.clicked.connect(self.play_related_source_raw)
        head.addWidget(title)
        head.addWidget(self.analysis_anchor_label)
        head.addStretch(1)
        head.addWidget(self.play_related_button)
        head.addWidget(back)
        root.addLayout(head)

        hint = QLabel(
            "Related songs are compared with the main song on demand. The labels estimate preparation and possible conflicts; "
            "they do not decide whether a result is entertaining."
        )
        hint.setObjectName("InfoPanel")
        hint.setWordWrap(True)
        root.addWidget(hint)

        self.relationship_progress = QProgressBar()
        self.relationship_progress.setRange(0, 100)
        self.relationship_progress.setValue(0)
        self.relationship_progress.setFormat("Searching related songs… %p%")
        self.relationship_progress.setVisible(False)
        root.addWidget(self.relationship_progress)

        self.analysis_empty_panel = QFrame()
        self.analysis_empty_panel.setObjectName("EmptyState")
        analysis_empty_layout = QVBoxLayout(self.analysis_empty_panel)
        analysis_empty_title = QLabel("Choose a main song to find related songs")
        analysis_empty_title.setObjectName("EmptyTitle")
        analysis_empty_text = QLabel(
            "Choose a main song on the Songs screen. MIDI Remixer will search the indexed library for compatible related songs. "
            "Select one here to inspect key, harmony, pulse, meter, melody/backing fit, and needed adjustments."
        )
        analysis_empty_text.setObjectName("Subtitle")
        analysis_empty_text.setWordWrap(True)
        analysis_empty_button = QPushButton("Open Library…")
        analysis_empty_button.setObjectName("PrimaryButton")
        analysis_empty_button.clicked.connect(lambda _checked=False: self.open_library_browser(purpose="sources"))
        analysis_empty_layout.addWidget(analysis_empty_title)
        analysis_empty_layout.addWidget(analysis_empty_text)
        analysis_empty_layout.addWidget(analysis_empty_button, 0, Qt.AlignmentFlag.AlignLeft)
        root.addWidget(self.analysis_empty_panel)

        self.analysis_splitter = QSplitter(Qt.Orientation.Vertical)
        self.analysis_splitter.setChildrenCollapsible(False)
        self.analysis_splitter.setHandleWidth(7)

        self.relationship_table = QTableWidget(0, 9)
        self.relationship_table.setHorizontalHeaderLabels([
            "Related Song", "Overall Fit", "Preparation", "Harmony", "Main melody → related backing", "Related melody → main backing",
            "Key", "Tempo", "Meter",
        ])
        self.relationship_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.relationship_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.relationship_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.relationship_table.setSortingEnabled(True)
        self.relationship_table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        rh = self.relationship_table.horizontalHeader()
        rh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, 9):
            rh.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.relationship_table.itemSelectionChanged.connect(self._relationship_selection_changed)
        self.relationship_table.cellDoubleClicked.connect(lambda *_: self.play_related_source_raw())
        self.analysis_splitter.addWidget(self.relationship_table)
        self.relationship_detail = QPlainTextEdit()
        self.relationship_detail.setReadOnly(True)
        self.analysis_splitter.addWidget(self.relationship_detail)
        self.analysis_splitter.setSizes([360, 220])
        root.addWidget(self.analysis_splitter, 1)

        bottom = QHBoxLayout()
        self.analysis_next = QPushButton("Continue to Mash Builder")
        self.analysis_next.setObjectName("PrimaryButton")
        self.analysis_next.clicked.connect(self.continue_to_mash_builder)
        self.analysis_next.setEnabled(False)
        bottom.addStretch(1)
        bottom.addWidget(self.analysis_next)
        root.addLayout(bottom)
        return page

    def _build_builder_tab(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(3, 4, 3, 4)
        root.setSpacing(5)

        top = QHBoxLayout()
        self.builder_source_summary = QLabel("Choose at least two songs.")
        self.builder_source_summary.setObjectName("StatusPill")
        back = QPushButton("← Back to Discovery")
        back.setObjectName("BackButton")
        back.clicked.connect(lambda: self.tabs.setCurrentIndex(1))
        top.addWidget(self.builder_source_summary, 1)
        top.addWidget(back)
        root.addLayout(top)

        self.builder_scroll = QScrollArea()
        self.builder_scroll.setWidgetResizable(True)
        self.builder_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(3, 3, 3, 3)
        content_layout.setSpacing(6)

        normalization = QGroupBox("Target musical frame")
        grid = QGridLayout(normalization)
        self.target_bpm = QSpinBox()
        self.target_bpm.setRange(30, 300)
        self.target_bpm.setSuffix(" felt BPM")
        self.target_key = QComboBox()
        self.target_key.addItem("Auto — main song tonic", None)
        for pc, name in enumerate(NOTE_NAMES):
            self.target_key.addItem(name, pc)
        self.target_mode = QComboBox()
        self.target_mode.addItem("Anchor mode", None)
        self.target_mode.addItem("Major", "major")
        self.target_mode.addItem("Minor", "minor")
        self.mode_handling = QComboBox()
        self.mode_handling.addItems(["Preserve source mode", "Adapt diatonic notes to target mode"])
        self.mode_handling.setToolTip("Preserve mode transposes each source tonic without rewriting major/minor scale degrees. Adapt mode changes diatonic 3rd, 6th, and 7th degrees where appropriate; review the result by ear.")
        self.length_mode = QComboBox()
        self.length_mode.addItems(["Anchor full length", "Shortest selected source", "Shared 64-bar form", "Custom bars"])
        self.length_mode.currentTextChanged.connect(self._length_mode_changed)
        self.bars_spin = QSpinBox()
        self.bars_spin.setRange(4, 2048)
        self.bars_spin.setSingleStep(4)
        self.section_bars = QSpinBox()
        self.section_bars.setRange(2, 64)
        self.section_bars.setSingleStep(2)
        self.density = QComboBox()
        self.density.addItems(["Lean", "Balanced", "Full"])
        self.auto_balance = QCheckBox("Balance song dynamics before the final mixer")
        self.auto_balance.setChecked(True)
        grid.addWidget(QLabel("Target pulse"), 0, 0)
        grid.addWidget(self.target_bpm, 0, 1)
        grid.addWidget(QLabel("Target tonic"), 0, 2)
        grid.addWidget(self.target_key, 0, 3)
        grid.addWidget(QLabel("Target mode"), 1, 0)
        grid.addWidget(self.target_mode, 1, 1)
        grid.addWidget(QLabel("Mode treatment"), 1, 2)
        grid.addWidget(self.mode_handling, 1, 3)
        grid.addWidget(QLabel("Arrangement length"), 2, 0)
        grid.addWidget(self.length_mode, 2, 1)
        grid.addWidget(QLabel("Bars"), 2, 2)
        grid.addWidget(self.bars_spin, 2, 3)
        grid.addWidget(QLabel("Phrase / section block"), 3, 0)
        grid.addWidget(self.section_bars, 3, 1)
        grid.addWidget(QLabel("Density"), 3, 2)
        grid.addWidget(self.density, 3, 3)
        grid.addWidget(self.auto_balance, 4, 0, 1, 4)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        content_layout.addWidget(normalization)

        recipe_group = QGroupBox("Visible arrangement choices — select any number and generate variations")
        recipe_layout = QVBoxLayout(recipe_group)
        self.recipe_table = QTableWidget(0, 4)
        self.recipe_table.setHorizontalHeaderLabels(["Select", "Arrangement", "What may be heard", "Why the app suggests it"])
        self.recipe_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.recipe_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.recipe_table.setWordWrap(True)
        self.recipe_table.verticalHeader().setVisible(False)
        hh = self.recipe_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.recipe_checks: dict[str, QPushButton] = {}
        recipe_layout.addWidget(self.recipe_table)
        recipe_controls = QHBoxLayout()
        self.variations = QSpinBox()
        self.variations.setRange(1, 12)
        self.variations.valueChanged.connect(self._update_generate_button)
        self.variation_strength = QComboBox()
        self.variation_strength.addItems(["Low", "Moderate", "High"])
        self.random_seed = QSpinBox()
        self.random_seed.setRange(0, 999999)
        self.random_seed.setSpecialValueText("Automatic")
        recommend = QPushButton("Select App Recommendations")
        recommend.clicked.connect(self.select_recommended_recipes)
        select_all = QPushButton("Select All Available")
        select_all.clicked.connect(lambda: self._set_all_recipes(True))
        clear = QPushButton("Clear")
        clear.clicked.connect(lambda: self._set_all_recipes(False))
        recipe_controls.addWidget(QLabel("Variations per arrangement"))
        recipe_controls.addWidget(self.variations)
        recipe_controls.addWidget(QLabel("Variation strength"))
        recipe_controls.addWidget(self.variation_strength)
        recipe_controls.addWidget(QLabel("Seed"))
        recipe_controls.addWidget(self.random_seed)
        recipe_controls.addStretch(1)
        recipe_controls.addWidget(recommend)
        recipe_controls.addWidget(select_all)
        recipe_controls.addWidget(clear)
        recipe_layout.addLayout(recipe_controls)
        content_layout.addWidget(recipe_group)

        workflow_note = QLabel(
            "Musician path: audition songs → inspect why they may fit → choose what each song provides and the musical frame → generate several arrangements → compare Takes → finish instrumentation and dynamics in the Mixer."
        )
        workflow_note.setWordWrap(True)
        workflow_note.setObjectName("InfoPanel")
        content_layout.addWidget(workflow_note)
        content_layout.addStretch(1)
        self.builder_scroll.setWidget(content)
        root.addWidget(self.builder_scroll, 1)

        footer = QFrame()
        footer.setObjectName("ActionFooter")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(8, 6, 8, 6)
        self.output_label = QLabel("Working output: not selected")
        self.output_label.setObjectName("StatusPill")
        output = QPushButton("Output Folder…")
        output.clicked.connect(self.choose_output_directory)
        self.generate_btn = QPushButton("Choose arrangements")
        self.generate_btn.setObjectName("PrimaryButton")
        self.generate_btn.clicked.connect(self.generate_takes)
        footer_layout.addWidget(self.output_label, 1)
        footer_layout.addWidget(output)
        footer_layout.addWidget(self.generate_btn)
        root.addWidget(footer)
        return page

    def _build_takes_tab(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(3, 4, 3, 4)
        root.setSpacing(5)

        self.takes_empty_panel = QFrame()
        self.takes_empty_panel.setObjectName("EmptyState")
        empty_layout = QVBoxLayout(self.takes_empty_panel)
        empty_title = QLabel("No Takes have been generated yet")
        empty_title.setObjectName("EmptyTitle")
        empty_text = QLabel("Choose arrangements in the Mash Builder, generate one or more Takes, then compare and finish them here.")
        empty_text.setObjectName("Subtitle")
        empty_text.setWordWrap(True)
        empty_button = QPushButton("Go to Mash Builder")
        empty_button.setObjectName("PrimaryButton")
        empty_button.clicked.connect(lambda: self.tabs.setCurrentIndex(2))
        empty_layout.addWidget(empty_title)
        empty_layout.addWidget(empty_text)
        empty_layout.addWidget(empty_button, 0, Qt.AlignmentFlag.AlignLeft)
        root.addWidget(self.takes_empty_panel)

        self.takes_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.takes_splitter.setChildrenCollapsible(False)
        self.takes_splitter.setHandleWidth(7)

        self.takes_table = HoverTableWidget(0, 7)
        self.takes_table.setHorizontalHeaderLabels(["Take", "Arrangement", "Recipe", "Variation", "Songs", "Bars", "Target"])
        self.takes_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.takes_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.takes_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.takes_table.setSortingEnabled(True)
        self.takes_table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        th = self.takes_table.horizontalHeader()
        th.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        th.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for col in (2, 3, 4, 5, 6):
            th.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.takes_table.itemSelectionChanged.connect(self._take_selection_changed)
        self.takes_table.cellDoubleClicked.connect(lambda *_: self.play_selected_take())
        self.takes_table.hoverRowChanged.connect(self._highlight_take_hover)
        self.takes_splitter.addWidget(self.takes_table)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(5, 3, 3, 3)
        detail_title = QLabel("Selected Take")
        detail_title.setObjectName("SectionTitle")
        right_layout.addWidget(detail_title)
        self.take_detail = QPlainTextEdit()
        self.take_detail.setReadOnly(True)
        right_layout.addWidget(self.take_detail, 1)
        compare = QGroupBox("Compare and finish")
        compare_layout = QGridLayout(compare)
        self.compare_source_combo = QComboBox()
        self.compare_source_combo.setMinimumWidth(210)
        self.play_compare_source_button = QPushButton("Play Original Song")
        self.play_compare_source_button.setObjectName("PlayButton")
        self.play_compare_source_button.clicked.connect(self.play_compare_source_raw)
        self.play_take_button = QPushButton("Play Selected Take")
        self.play_take_button.setObjectName("PlayButton")
        self.play_take_button.clicked.connect(self.play_selected_take)
        self.mixer_button = QPushButton("Open Instrumentation & Dynamics Mixer")
        self.mixer_button.setObjectName("PrimaryButton")
        self.mixer_button.clicked.connect(self.open_mixer)
        self.refine_section_button = QPushButton("Refine One Section…")
        self.refine_section_button.setObjectName("PrimaryButton")
        self.refine_section_button.clicked.connect(self.open_selective_refinement)
        self.performance_polish_button = QPushButton("Performance Polish…")
        self.performance_polish_button.setObjectName("PrimaryButton")
        self.performance_polish_button.clicked.connect(self.open_performance_polish)
        compare_layout.addWidget(QLabel("Original song"), 0, 0)
        compare_layout.addWidget(self.compare_source_combo, 0, 1)
        compare_layout.addWidget(self.play_compare_source_button, 1, 0)
        compare_layout.addWidget(self.play_take_button, 1, 1)
        action_row = QHBoxLayout()
        action_row.addWidget(self.refine_section_button)
        action_row.addWidget(self.performance_polish_button)
        action_row.addWidget(self.mixer_button)
        action_row.addStretch(1)
        self.play_changed_section_button = QPushButton("Play Changed Section")
        self.play_changed_section_button.setObjectName("PlayButton")
        self.play_changed_section_button.clicked.connect(self.play_selected_changed_section)
        self.play_parent_section_button = QPushButton("Play Parent Section")
        self.play_parent_section_button.setObjectName("UtilityButton")
        self.play_parent_section_button.clicked.connect(self.play_selected_parent_section)
        focused_row = QHBoxLayout()
        focused_row.addWidget(self.play_changed_section_button)
        focused_row.addWidget(self.play_parent_section_button)
        focused_row.addStretch(1)
        compare_layout.addLayout(action_row, 2, 0, 1, 2)
        compare_layout.addLayout(focused_row, 3, 0, 1, 2)
        right_layout.addWidget(compare)
        self.takes_splitter.addWidget(right)
        self.takes_splitter.setSizes([720, 380])
        root.addWidget(self.takes_splitter, 1)

        actions = QFrame()
        actions.setObjectName("ActionFooter")
        row = QHBoxLayout(actions)
        row.setContentsMargins(8, 6, 8, 6)
        # Keep direct actions while one prominent export center handles multi-format delivery.
        self.export_midi_button = QPushButton("Quick MIDI")
        self.export_midi_button.clicked.connect(self.export_selected_midi)
        self.render_wav_button = QPushButton("Quick WAV")
        self.render_wav_button.clicked.connect(lambda: self.render_selected_audio("wav"))
        self.render_mp3_button = QPushButton("Quick MP3")
        self.render_mp3_button.clicked.connect(lambda: self.render_selected_audio("mp3"))
        self.export_package_button = QPushButton("Export Selected…")
        self.export_package_button.setObjectName("PrimaryButton")
        self.export_package_button.clicked.connect(self.export_selected)
        self.cancel_export_button = QPushButton("Cancel Export")
        self.cancel_export_button.setObjectName("StopButton")
        self.cancel_export_button.clicked.connect(self.cancel_export)
        self.cancel_export_button.setVisible(False)
        self.cancel_export_button.setEnabled(False)
        folder_btn = QPushButton("Open Working Folder")
        folder_btn.clicked.connect(self.open_working_folder)
        more = QPushButton("Generate More Takes")
        more.clicked.connect(lambda: self.tabs.setCurrentIndex(2))
        row.addWidget(more)
        row.addStretch(1)
        row.addWidget(self.export_midi_button)
        row.addWidget(self.render_wav_button)
        row.addWidget(self.render_mp3_button)
        row.addWidget(self.export_package_button)
        row.addWidget(self.cancel_export_button)
        row.addWidget(folder_btn)
        root.addWidget(actions)
        return page

    def _framed(self, title: str, widget: QWidget) -> QFrame:
        frame = QFrame()
        frame.setObjectName("Card")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(8, 6, 8, 7)
        layout.setSpacing(4)
        label = QLabel(title)
        label.setObjectName("CardTitle")
        layout.addWidget(label)
        layout.addWidget(widget, 1)
        return frame

    def _apply_style(self) -> None:
        self.setStyleSheet(load_stylesheet("base.qss"))

    # ---------- settings/layout ----------
    def _restore_settings(self) -> None:
        self.target_bpm.setValue(int(self.settings_store.get("remix_default_bpm", 110)))
        stored_mode = self.settings_store.get("remix_target_mode")
        if self.target_mode.findData(stored_mode) >= 0:
            self.target_mode.setCurrentIndex(self.target_mode.findData(stored_mode))
        self.mode_handling.setCurrentText(str(self.settings_store.get("remix_mode_handling", "Preserve source mode")))
        self.length_mode.setCurrentText(str(self.settings_store.get("remix_multisource_length_mode", "Anchor full length")))
        self.bars_spin.setValue(int(self.settings_store.get("remix_default_bars", 64)))
        self.section_bars.setValue(int(self.settings_store.get("remix_default_section_bars", 8)))
        self.density.setCurrentText(str(self.settings_store.get("remix_default_density", "Balanced")))
        self.variations.setValue(int(self.settings_store.get("remix_variations_per_strategy", 1)))
        self.variation_strength.setCurrentText(str(self.settings_store.get("remix_variation_strength", "Moderate")))
        self.random_seed.setValue(int(self.settings_store.get("remix_random_seed", 0)))
        self.auto_balance.setChecked(bool(self.settings_store.get("remix_auto_balance", True)))
        self.volume_slider.setValue(int(self.settings_store.get("master_volume", 100)))
        output = self.settings_store.get("last_output_directory")
        if output:
            self.project.output_directory = str(output)
            self.output_label.setText(f"Working output: {output}")
        soundfont = find_soundfont(self.root, self.settings_store.get("last_soundfont_path"), self.settings_store.get("last_soundfont_directory"))
        if soundfont:
            self._apply_soundfont_path(soundfont)
        else:
            self._set_soundfont_state(None)
        self._auto_detect_fluidsynth(False)
        self._set_transport_state("stopped")
        self._populate_recipe_table()
        self.tabs.setCurrentIndex(max(0, min(3, int(self.settings_store.get("ui_active_tab", 0)))))
        if bool(self.settings_store.get("ui_restore_layout", True)):
            geometry = _decode_qbytearray(self.settings_store.get("ui_window_geometry"))
            state = _decode_qbytearray(self.settings_store.get("ui_window_state"))
            analysis = _decode_qbytearray(self.settings_store.get("ui_analysis_splitter_state") or self.settings_store.get("ui_discovery_splitter_state"))
            takes = _decode_qbytearray(self.settings_store.get("ui_takes_splitter_state"))
            if not geometry.isEmpty():
                self.restoreGeometry(geometry)
            if not state.isEmpty():
                self.restoreState(state)
            if not analysis.isEmpty():
                self.analysis_splitter.restoreState(analysis)
            if not takes.isEmpty():
                self.takes_splitter.restoreState(takes)
        self._ensure_window_on_screen()
        self._length_mode_changed(self.length_mode.currentText())
        self._update_generate_button()
        self._refresh_source_set()

    def _save_ui_state(self) -> None:
        self.settings_store.update_many({
            "ui_window_geometry": _encode_qbytearray(self.saveGeometry()),
            "ui_window_state": _encode_qbytearray(self.saveState()),
            "ui_analysis_splitter_state": _encode_qbytearray(self.analysis_splitter.saveState()),
            "ui_takes_splitter_state": _encode_qbytearray(self.takes_splitter.saveState()),
            "ui_active_tab": self.tabs.currentIndex(),
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
        })

    def fit_window_to_screen(self) -> None:
        screen = self.screen() or QApplication.primaryScreen()
        if not screen:
            return
        available = screen.availableGeometry()
        self.resize(min(1180, available.width() - 30), min(780, available.height() - 40))
        self.move(available.center() - self.rect().center())

    def _ensure_window_on_screen(self) -> None:
        screen = self.screen() or QApplication.primaryScreen()
        if not screen:
            return
        available = screen.availableGeometry()
        if not available.intersects(self.frameGeometry()):
            self.fit_window_to_screen()
        if self.width() > available.width() or self.height() > available.height():
            self.resize(min(self.width(), available.width() - 20), min(self.height(), available.height() - 30))

    def reset_layout(self) -> None:
        self.resize(1180, 780)
        self.analysis_splitter.setSizes([360, 220])
        self.takes_splitter.setSizes([720, 380])
        self.fit_window_to_screen()

    def show_settings(self) -> None:
        dialog = SettingsDialog(self.settings_store, self.root, self)
        if dialog.exec():
            self.playback_engine.set_audio_driver(self.settings_store.get("audio_driver", "default"))
            self._configure_folder_watcher()

    # ---------- source set and discovery ----------
    def _add_source_paths(self, paths: list[str], replace: bool = False) -> None:
        resolved = [str(Path(path).resolve(strict=False)) for path in paths]
        valid: list[str] = []
        rejected: list[tuple[str, str]] = []
        for path in resolved:
            if path not in self.profiles:
                rejected.append((path, "not loaded from the authoritative Library"))
                continue
            allowed, reason = self._library_path_admission(path, mode=getattr(self, "guidance_mode", "guided"))
            if allowed:
                valid.append(path)
            else:
                rejected.append((path, reason))
        if rejected:
            names = ", ".join(f"{Path(path).name} ({reason})" for path, reason in rejected[:3])
            more = f" and {len(rejected) - 3} more" if len(rejected) > 3 else ""
            self.statusBar().showMessage(f"Not added: {names}{more}. Resolve eligibility in MIDI Library Manager.", 9000)
        # The resident 96k map intentionally contains compact quick summaries.
        # Before calling a selected song "not ready", consult the lazy detailed
        # profile store; otherwise already-analyzed songs appear stuck forever.
        resolved_profiles = self.load_deep_profiles(valid)
        pending = [path for path in valid if is_provisional_profile(resolved_profiles.get(path, self.profiles[path]))]
        if pending:
            self._pending_open_midi_paths.update(pending)
            for path in pending:
                self.analyze_library_song_on_demand(path)
            names = ", ".join(self.profiles[path].title for path in pending[:3])
            if len(pending) > 3:
                names += f" and {len(pending) - 3} more"
            self.statusBar().showMessage(
                f"Preparing {names} in background. They will be added automatically when Ready."
            )
            valid = [path for path in valid if path not in pending]
        if not valid:
            return
        if replace:
            self.source_specs = []
        existing = {spec.path for spec in self.source_specs}
        for path in valid:
            if path in existing:
                continue
            if len(self.source_specs) >= MAX_ACTIVE_SOURCES:
                QMessageBox.information(self, "Songs in This Mashup", f"This preview supports up to {MAX_ACTIVE_SOURCES} active songs in one arrangement. The library itself can contain far more.")
                break
            role = "anchor" if not self.source_specs else "foundation" if len(self.source_specs) == 1 else "hook"
            treatment = str(self.settings_store.get("remix_default_melody_treatment", "full"))
            self.source_specs.append(SourceSpec(path=path, role=role, melody_treatment=treatment))
            existing.add(path)
        self._refresh_source_set()
        self.tabs.setCurrentIndex(0)

    def _refresh_source_set(self) -> None:
        self.source_table.setRowCount(len(self.source_specs))
        for row, spec in enumerate(self.source_specs):
            profile = self.deep_profile_for_path(spec.path)
            if not profile:
                continue
            number = QTableWidgetItem(str(row + 1))
            number.setData(Qt.ItemDataRole.UserRole, spec.path)
            self.source_table.setItem(row, 0, number)
            source = QTableWidgetItem(f"{profile.title} — {profile.composer}")
            source.setToolTip(profile.compact_summary() + "\n" + "\n".join(profile.warnings))
            self.source_table.setItem(row, 1, source)
            role = QComboBox()
            role_options = [
                ("Main song", "anchor"),
                ("Let MIDI Remixer decide", "auto"),
                ("Lead melody", "melody"),
                ("Complete backing", "foundation"),
                ("Hook or signature part", "hook"),
                ("Complete section", "section"),
            ]
            for label, value in role_options:
                role.addItem(label, value)
            current_role = "anchor" if row == 0 else (spec.role if spec.role != "anchor" else "auto")
            role.setCurrentIndex(max(0, role.findData(current_role)))
            role.setEnabled(row != 0)
            role.currentIndexChanged.connect(
                lambda _value, index=row, combo=role: self._source_role_changed(index, str(combo.currentData()))
            )
            self.source_table.setCellWidget(row, 2, role)
            start = QSpinBox()
            start.setRange(0, max(0, int(profile.bars) - 1))
            start.setValue(spec.start_bar)
            start.valueChanged.connect(lambda value, index=row: self._source_start_changed(index, value))
            self.source_table.setCellWidget(row, 3, start)
            voice = QComboBox()
            voice.addItem("Use the complete track", "full")
            voice.addItem("Top voice only (manual extraction)", "top")
            voice.addItem("Bottom voice only (manual extraction)", "bottom")
            if voice.findData(spec.melody_treatment) >= 0:
                voice.setCurrentIndex(voice.findData(spec.melody_treatment))
            voice.currentIndexChanged.connect(lambda _value, index=row, combo=voice: self._source_voice_changed(index, str(combo.currentData())))
            self.source_table.setCellWidget(row, 4, voice)
            readiness_label = score_band(int(profile.orchestration_readiness))[0]
            roles_ready = profile.melody_track_index is not None and profile.accompaniment_track_index is not None
            readiness = QTableWidgetItem(
                f"{'Usable musical parts' if roles_ready else 'Role review needed'} · {readiness_label} ({profile.orchestration_readiness}%)"
            )
            readiness.setToolTip("\n".join(profile.warnings) or "No special warnings")
            self.source_table.setItem(row, 5, readiness)
            tracks = QPushButton("Tracks…")
            tracks.clicked.connect(lambda _checked=False, index=row: self.edit_source_tracks(index))
            self.source_table.setCellWidget(row, 6, tracks)
        self.source_count.setText(f"{len(self.source_specs)} song{'s' if len(self.source_specs) != 1 else ''}")
        self.discovery_next.setEnabled(len(self.source_specs) >= 1)
        self.builder_source_summary.setText(
            " → ".join(self.profiles[spec.path].title for spec in self.source_specs if spec.path in self.profiles)
            if self.source_specs else "Choose at least two songs."
        )
        self.compare_source_combo.blockSignals(True)
        self.compare_source_combo.clear()
        for index, spec in enumerate(self.source_specs):
            profile = self.profiles.get(spec.path)
            if profile:
                self.compare_source_combo.addItem(f"{index + 1}. {profile.title}", spec.path)
        self.compare_source_combo.blockSignals(False)
        self._update_length_from_sources()
        self._refresh_relationships()
        self._populate_recipe_table()
        self._update_workflow_state()

    def _update_workflow_state(self) -> None:
        source_count = len(self.source_specs)
        has_anchor = source_count >= 1
        has_pair = source_count >= 2
        self.discovery_empty_panel.setVisible(not has_anchor)
        self.source_frame.setVisible(has_anchor)
        self.play_anchor_button.setEnabled(has_anchor)
        self.play_related_button.setEnabled(self.relationship_table.currentRow() >= 0)
        self.discovery_next.setEnabled(has_anchor)
        self.analysis_next.setEnabled(self.relationship_table.currentRow() >= 0)
        self.analysis_empty_panel.setVisible(not has_anchor or self.relationship_table.rowCount() == 0)
        self.analysis_splitter.setVisible(has_anchor and self.relationship_table.rowCount() > 0)
        self.analysis_anchor_label.setText(
            f"Main song: {self.profiles[self.source_specs[0].path].title}" if has_anchor and self.source_specs[0].path in self.profiles
            else "Choose a main song on the Songs screen."
        )
        self.tabs.setTabEnabled(1, has_anchor)
        self.tabs.setTabEnabled(2, has_pair)
        self.tabs.setTabEnabled(3, True)
        selected_source = self.source_table.currentRow()
        self.remove_source_button.setEnabled(has_anchor and selected_source >= 0)
        self.set_anchor_button.setEnabled(source_count > 1 and selected_source > 0)
        self.transport_play.setEnabled(has_anchor or bool(self.selected_take))

    def _source_role_changed(self, index: int, role: str) -> None:
        if 0 <= index < len(self.source_specs):
            self.source_specs[index] = replace(self.source_specs[index], role="anchor" if index == 0 else role)
            self._populate_recipe_table()

    def _source_start_changed(self, index: int, value: int) -> None:
        if 0 <= index < len(self.source_specs):
            self.source_specs[index] = replace(self.source_specs[index], start_bar=int(value))

    def _source_voice_changed(self, index: int, value: str) -> None:
        if 0 <= index < len(self.source_specs):
            self.source_specs[index] = replace(self.source_specs[index], melody_treatment=value)

    def _selected_source_row(self) -> int:
        return self.source_table.currentRow()

    def _source_selection_changed(self) -> None:
        row = self._selected_source_row()
        self.remove_source_button.setEnabled(0 <= row < len(self.source_specs))
        self.set_anchor_button.setEnabled(0 < row < len(self.source_specs))
        if row < 0 or row >= len(self.source_specs):
            return
        profile = self.deep_profile_for_path(self.source_specs[row].path)
        if profile:
            self.statusBar().showMessage(profile.compact_summary())

    def remove_selected_source(self) -> None:
        row = self._selected_source_row()
        if row < 0 or row >= len(self.source_specs):
            return
        self.source_specs.pop(row)
        if self.source_specs:
            self.source_specs[0] = replace(self.source_specs[0], role="anchor")
        self._refresh_source_set()

    def set_selected_anchor(self) -> None:
        row = self._selected_source_row()
        if row <= 0 or row >= len(self.source_specs):
            return
        spec = self.source_specs.pop(row)
        self.source_specs.insert(0, replace(spec, role="anchor"))
        for index in range(1, len(self.source_specs)):
            if self.source_specs[index].role == "anchor":
                self.source_specs[index] = replace(self.source_specs[index], role="auto")
        self._refresh_source_set()
        self.source_table.selectRow(0)

    def edit_source_tracks(self, index: int) -> None:
        if not (0 <= index < len(self.source_specs)):
            return
        spec = self.source_specs[index]
        profile = self.deep_profile_for_path(spec.path)
        if not profile:
            return
        dialog = SourceTrackDialog(profile, spec, self)
        if dialog.exec():
            self.source_specs[index] = dialog.updated_spec()
            self._refresh_source_set()

    def _effective_profile(self, spec: SourceSpec) -> MidiProfile:
        profile = self.deep_profile_for_path(spec.path) or self.profiles[spec.path]
        return replace(
            profile,
            melody_track_index=spec.melody_track_index if spec.melody_track_index is not None else profile.melody_track_index,
            accompaniment_track_index=spec.foundation_track_index if spec.foundation_track_index is not None else profile.accompaniment_track_index,
        )

    def _effective_profiles(self) -> list[MidiProfile]:
        return [self._effective_profile(spec) for spec in self.source_specs if spec.enabled and spec.path in self.profiles]

    def _relationship_selection_changed(self) -> None:
        row = self.relationship_table.currentRow()
        cell = self.relationship_table.item(row, 0) if row >= 0 else None
        candidate_path = cell.data(Qt.ItemDataRole.UserRole) if cell else None
        item = next((assessment for assessment in self._relationship_rows if assessment.source_b == candidate_path), None)
        if item is None:
            self.play_related_button.setEnabled(False)
            self.analysis_next.setEnabled(False)
            return
        lines = [
            f"{item.title_a} + {item.title_b}",
            f"Category: {item.pairing_category} · adaptation: {item.adaptation_required}",
            f"Preparation {item.preparation_score}% · harmony {item.harmonic_score}% · key {item.key_score}% · tempo {item.tempo_score}% · meter {item.meter_score}%",
            f"Direction: main-song melody over related-song backing {item.melody_a_over_b_score}% · related-song melody over main-song backing {item.melody_b_over_a_score}%",
            f"Suggested B transposition: {item.key_shift_b_to_a:+d} semitones · target {item.target_felt_bpm} felt BPM",
            "",
            *item.observations,
            "",
            "These values estimate preparation and conflict; they do not judge whether the result is entertaining or artistically worthwhile.",
        ]
        self.relationship_detail.setPlainText("\n".join(lines))
        self.play_related_button.setEnabled(True)
        self.analysis_next.setEnabled(True)

    def _play_source_spec_raw(self, spec: SourceSpec | None) -> None:
        if spec is None:
            QMessageBox.information(self, APP_NAME, "Choose a song first.")
            return
        profile = self.profiles.get(spec.path)
        if profile:
            self._play_path(profile.path, f"Original song — {profile.title}", f"source-{profile.fingerprint}")

    def play_anchor_raw(self) -> None:
        self._play_source_spec_raw(self.source_specs[0] if self.source_specs else None)

    def play_related_source_raw(self) -> None:
        row = self.relationship_table.currentRow()
        cell = self.relationship_table.item(row, 0) if row >= 0 else None
        path = str(cell.data(Qt.ItemDataRole.UserRole)) if cell and cell.data(Qt.ItemDataRole.UserRole) else None
        profile = self.profiles.get(path) if path else None
        if profile is None:
            QMessageBox.information(self, APP_NAME, "Select a relationship row first.")
            return
        self._play_path(profile.path, f"Related song — {profile.title}", f"candidate-{profile.fingerprint}")

    def continue_to_mash_builder(self) -> None:
        row = self.relationship_table.currentRow()
        cell = self.relationship_table.item(row, 0) if row >= 0 else None
        path = str(cell.data(Qt.ItemDataRole.UserRole)) if cell and cell.data(Qt.ItemDataRole.UserRole) else None
        if path and path not in {spec.path for spec in self.source_specs}:
            self._add_source_paths([path], replace=False)
        if len(self.source_specs) < 2:
            QMessageBox.information(self, APP_NAME, "Select a related song first.")
            return
        self.tabs.setCurrentIndex(2)

    def play_compare_source_raw(self) -> None:
        path = self.compare_source_combo.currentData()
        spec = next((item for item in self.source_specs if item.path == path), None)
        self._play_source_spec_raw(spec)

    def play_selected_source_raw(self) -> None:
        row = self._selected_source_row()
        if row < 0 and self.source_specs:
            row = 0
        if row < 0 or row >= len(self.source_specs):
            QMessageBox.information(self, APP_NAME, "Select a song first.")
            return
        profile = self.profiles.get(self.source_specs[row].path)
        if profile:
            self._play_path(profile.path, f"Original song — {profile.title}", f"source-{profile.fingerprint}")

    # ---------- builder ----------
    def _length_mode_changed(self, mode: str) -> None:
        self.bars_spin.setEnabled(mode == "Custom bars")
        self._update_length_from_sources()

    def _update_length_from_sources(self) -> None:
        profiles = self._effective_profiles() if self.source_specs else []
        if not profiles:
            return
        mode = self.length_mode.currentText()
        if mode == "Anchor full length":
            bars = int(profiles[0].bars)
        elif mode == "Shortest selected source":
            bars = int(min(profile.bars for profile in profiles))
        elif mode == "Shared 64-bar form":
            bars = 64
        else:
            return
        self.bars_spin.setValue(max(4, bars))

    def _populate_recipe_table(self) -> None:
        current = {key: check.isChecked() for key, check in getattr(self, "recipe_checks", {}).items()}
        profiles = self._effective_profiles() if self.source_specs else []
        source_count = len(profiles)
        guidance = {item.key: item for item in recipe_advice(profiles)}
        multi_reasons = recommended_multi_source_recipes(profiles)
        pair_reason: dict[str, str] = {}
        if source_count == 2:
            assessment = assess_pair(profiles[0], profiles[1])
            if assessment.harmonic_score >= 60:
                pair_reason.update({
                    "melody-a-accompaniment-b": f"Main-song melody over related-song backing tests at {assessment.melody_a_over_b_score}%.",
                    "melody-b-accompaniment-a": f"Related-song melody over main-song backing tests at {assessment.melody_b_over_a_score}%.",
                    "conversation": "Both directional melody/harmony checks are available for a call-and-response arrangement.",
                    "layered-blend": "Harmonic overlap is sufficient to test a controlled, register-separated layer.",
                })
            pair_reason["section-exchange"] = "Complete sections preserve each song's identity even when harmony differs."
            pair_reason["transformation"] = "A gradual handoff can make the relationship between the songs audible without constant layering."
            pair_reason["foundation-swap"] = "A midpoint role change exposes both directional combinations."
        rows: list[tuple[str, str, str, str, bool]] = []
        if source_count == 2:
            for strategy in TAKE_STRATEGIES:
                key = PAIR_PREFIX + strategy.key
                item = guidance.get(strategy.key)
                available = bool(item.eligible) if item else True
                reason = (f"{item.recommendation}: {item.reason}" if item else pair_reason.get(strategy.key, "Available for this two-song set."))
                heard = item.expected_result if item else strategy.description
                rows.append((key, strategy.label, heard, reason, available))
        elif source_count >= 3:
            for recipe in MULTI_SOURCE_RECIPES:
                key = MULTI_PREFIX + recipe.key
                item = guidance.get(recipe.key)
                available = bool(item.eligible) if item else source_count >= recipe.minimum_sources
                reason = (f"{item.recommendation}: {item.reason}" if item else multi_reasons.get(recipe.key, f"Requires at least {recipe.minimum_sources} songs." if not available else "Available; select it to explore this arrangement path."))
                heard = item.expected_result if item else recipe.description
                rows.append((key, recipe.label, heard, reason, available))
        self.recipe_table.setRowCount(len(rows))
        self.recipe_checks = {}
        default_pair = set(self.settings_store.get("remix_default_strategies", DEFAULT_TAKE_STRATEGY_KEYS) or DEFAULT_TAKE_STRATEGY_KEYS)
        default_multi = set(self.settings_store.get("remix_default_multisource_recipes", DEFAULT_MULTI_SOURCE_RECIPE_KEYS) or DEFAULT_MULTI_SOURCE_RECIPE_KEYS)
        for row, (key, label, description, reason, available) in enumerate(rows):
            check = QPushButton("Select")
            check.setObjectName("RecipeSelectButton")
            check.setCheckable(True)
            if key in current:
                checked = current[key]
            elif key.startswith(PAIR_PREFIX):
                checked = available and key[len(PAIR_PREFIX):] in default_pair
            else:
                checked = available and key[len(MULTI_PREFIX):] in default_multi
            check.setChecked(checked)
            check.setEnabled(available)
            check.setToolTip(reason if not available else f"{label}: {reason}")
            check.setText("Selected" if checked else "Select")
            if not available:
                check.setText("Unavailable")
            check.toggled.connect(lambda value, button=check: button.setText("Selected" if value else "Select" if button.isEnabled() else "Unavailable"))
            check.toggled.connect(self._update_generate_button)
            self.recipe_checks[key] = check
            self.recipe_table.setCellWidget(row, 0, check)
            label_item = QTableWidgetItem(label)
            label_item.setData(Qt.ItemDataRole.UserRole, key)
            self.recipe_table.setItem(row, 1, label_item)
            self.recipe_table.setItem(row, 2, QTableWidgetItem(description))
            self.recipe_table.setItem(row, 3, QTableWidgetItem(reason))
            self.recipe_table.setRowHeight(row, 58)
        self._update_generate_button()

    def select_recommended_recipes(self) -> None:
        profiles = self._effective_profiles()
        multi = set(recommended_multi_source_recipes(profiles))
        pair: set[str] = set()
        if len(profiles) == 2:
            item = assess_pair(profiles[0], profiles[1])
            pair.update({"section-exchange", "transformation"})
            if item.melody_a_over_b_score >= 52:
                pair.add("melody-a-accompaniment-b")
            if item.melody_b_over_a_score >= 52:
                pair.add("melody-b-accompaniment-a")
            if item.harmonic_score >= 60:
                pair.update({"conversation", "layered-blend", "foundation-swap"})
        for key, check in self.recipe_checks.items():
            selected = key[len(PAIR_PREFIX):] in pair if key.startswith(PAIR_PREFIX) else key[len(MULTI_PREFIX):] in multi
            check.setChecked(check.isEnabled() and selected)

    def _set_all_recipes(self, checked: bool) -> None:
        for check in self.recipe_checks.values():
            if check.isEnabled():
                check.setChecked(checked)

    def _selected_recipe_keys(self) -> tuple[str, ...]:
        return tuple(key for key, check in self.recipe_checks.items() if check.isEnabled() and check.isChecked())

    def _update_generate_button(self, *_args) -> None:
        selected = len(self._selected_recipe_keys())
        count = selected * self.variations.value()
        self.generate_btn.setText(f"Generate {count} Take{'s' if count != 1 else ''}" if count else "Choose arrangements")
        ready = len(self.source_specs) >= 2 and count > 0
        reason = "Choose at least two songs." if len(self.source_specs) < 2 else "Select at least one available arrangement."
        set_action_enabled(self.generate_btn, ready, reason)

    def _target_key_values(self) -> tuple[int | None, str]:
        tonic = self.target_key.currentData()
        target_pc = int(tonic) % 12 if isinstance(tonic, int) else None
        mode = self.target_mode.currentData()
        if mode not in {"major", "minor"}:
            profiles = self._effective_profiles()
            mode = profiles[0].mode if profiles else "major"
        return target_pc, str(mode)

    def _current_decision_summary(self) -> list[str]:
        target_pc, target_mode = self._target_key_values()
        target_name = "Auto focus tonic" if target_pc is None else f"{NOTE_NAMES[target_pc]} {target_mode}"
        lines = [
            f"Guidance mode: {self.guidance_mode}",
            f"Target: {target_name} at {self.target_bpm.value()} felt BPM",
            f"Form: {self.bars_spin.value()} bars in {self.section_bars.value()}-bar blocks",
            f"Density: {self.density.currentText()}; dynamics normalization {'on' if self.auto_balance.isChecked() else 'off'}",
            f"Mode treatment: {self.mode_handling.currentText()}",
        ]
        lines.extend(str(item) for item in self.assisted_section_decisions)
        return lines

    def _next_take_number(self) -> int:
        numbers: list[int] = []
        for take in self.takes:
            suffix = take.take_id.rsplit("-", 1)[-1]
            if suffix.isdigit():
                numbers.append(int(suffix))
        return max(numbers, default=0) + 1

    def generate_takes(self) -> None:
        profiles = self._effective_profiles()
        if len(profiles) < 2:
            QMessageBox.information(self, APP_NAME, "Choose at least two songs for the mashup.")
            return
        selected = self._selected_recipe_keys()
        pair_keys = tuple(key[len(PAIR_PREFIX):] for key in selected if key.startswith(PAIR_PREFIX))
        multi_keys = tuple(key[len(MULTI_PREFIX):] for key in selected if key.startswith(MULTI_PREFIX))
        if not pair_keys and not multi_keys:
            QMessageBox.information(self, APP_NAME, "Select at least one available arrangement approach.")
            return

        target_pc, target_mode = self._target_key_values()
        variations = self.variations.value()
        trial_number = len(self.session_workspace.trials()) + 1
        trial_name = f"Trial {trial_number}"
        decision_summary = self._current_decision_summary()
        trial = self.session_workspace.create_trial(
            trial_name,
            self.guidance_mode,
            decisions=decision_summary,
            source_paths=[profile.path for profile in profiles],
            settings={
                "target_bpm": self.target_bpm.value(), "target_key_pc": target_pc,
                "target_mode": target_mode, "bars": self.bars_spin.value(),
                "section_bars": self.section_bars.value(), "density": self.density.currentText(),
                "pair_recipes": list(pair_keys), "multi_recipes": list(multi_keys),
                "variations": variations,
                "lead_gain_percent": int(getattr(self, "pair_lead_gain_percent", 100)),
                "backing_gain_percent": int(getattr(self, "pair_backing_gain_percent", 100)),
                "section_source_order": list(self.assisted_section_source_order),
                "section_labels": list(self.assisted_section_labels),
            },
        )
        self.current_trial_id = trial.trial_id
        run = self.session_workspace.trial_directory(trial.trial_id)
        new_takes: list[RemixTake] = []
        failures: list[str] = []
        QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
        self.statusBar().showMessage(f"Generating {len(selected) * variations} requested Takes in {trial_name}…")
        try:
            if pair_keys and len(profiles) != 2:
                failures.append("Two-song recipes were skipped because this mashup contains more than two songs.")
            elif pair_keys:
                a_spec, b_spec = self.source_specs[:2]
                for recipe_key in pair_keys:
                    try:
                        pair_settings = RemixSettings(
                            target_bpm=self.target_bpm.value(), target_key_pc=target_pc, target_mode=target_mode,
                            mode_handling=self.mode_handling.currentText(), bars=self.bars_spin.value(),
                            start_bar_a=a_spec.start_bar, start_bar_b=b_spec.start_bar,
                            section_bars=self.section_bars.value(), density=self.density.currentText(),
                            strategies=(recipe_key,), variations_per_strategy=variations,
                            variation_strength=self.variation_strength.currentText(), random_seed=self.random_seed.value(),
                            melody_treatment_a=a_spec.melody_treatment, melody_treatment_b=b_spec.melody_treatment,
                            auto_balance=self.auto_balance.isChecked(),
                            lead_gain_percent=int(getattr(self, "pair_lead_gain_percent", 100)),
                            backing_gain_percent=int(getattr(self, "pair_backing_gain_percent", 100)),
                            backing_mode=getattr(self, "pair_backing_mode", "all-except-melody"),
                            preserve_signature_assets=bool(getattr(self, "preserve_signature_assets", True)),
                            signature_source=str(getattr(self, "signature_source", "auto")),
                            signature_placement=str(getattr(self, "signature_placement", "intro-alone")),
                            section_source_order=tuple(self.assisted_section_source_order),
                            section_labels=tuple(self.assisted_section_labels),
                        )
                        new_takes.extend(generate_remix_takes(profiles[0], profiles[1], run / "pair-recipes" / recipe_key, pair_settings))
                    except Exception as exc:  # one unavailable recipe must not abort the Trial
                        label = self._recipe_labels().get(recipe_key, recipe_key)
                        failures.append(f"{label}: {type(exc).__name__}: {exc}")
            for recipe_key in multi_keys:
                try:
                    multi_settings = MultiSourceSettings(
                        target_bpm=self.target_bpm.value(), target_key_pc=target_pc, target_mode=target_mode,
                        mode_handling=self.mode_handling.currentText(), bars=self.bars_spin.value(),
                        section_bars=self.section_bars.value(), density=self.density.currentText(),
                        recipes=(recipe_key,), variations_per_recipe=variations,
                        variation_strength=self.variation_strength.currentText(), random_seed=self.random_seed.value(),
                        auto_balance=self.auto_balance.isChecked(),
                        preserve_signature_assets=bool(getattr(self, "preserve_signature_assets", True)),
                        signature_source="foundation",
                        signature_placement=str(getattr(self, "signature_placement", "intro-alone")),
                        section_source_order=tuple(self.assisted_section_source_order),
                        section_labels=tuple(self.assisted_section_labels),
                    )
                    new_takes.extend(generate_multi_source_takes(profiles, run / "multi-source" / recipe_key, multi_settings, self.source_specs))
                except Exception as exc:
                    label = self._recipe_labels().get(recipe_key, recipe_key)
                    failures.append(f"{label}: {type(exc).__name__}: {exc}")

            if not new_takes:
                self.session_workspace.delete_trial(trial.trial_id)
                self.current_trial_id = None
                detail = "\n".join(failures) if failures else "No selected recipe produced usable MIDI."
                QMessageBox.warning(
                    self, "No Takes could be generated",
                    "These songs may still be useful together, but the selected arrangements need more preparation.\n\n" + detail
                    + "\n\nTry whole-section/transformational arrangements, top-voice extraction, or manual track mapping.",
                )
                return

            # A fast structural gate catches silent, percussion-only, corrupt,
            # severely hanging, or implausibly short output before the user is
            # asked to audition it.  Artistic quality still belongs to the
            # listening review; warnings are preserved as audition prompts.
            quality_payload: list[dict[str, object]] = []
            structurally_usable: list[RemixTake] = []
            run_root = run.resolve()
            for take in new_takes:
                report = inspect_generated_take(take.path)
                quality_payload.append(report.to_dict())
                if not report.passed:
                    failures.append(f"{take.name}: structural quality gate: {'; '.join(report.errors)}")
                    try:
                        generated = Path(take.path).resolve()
                        if generated.is_file() and run_root in generated.parents:
                            generated.unlink()
                    except OSError as exc:
                        self._write_ui_error_log("Remove failed generated Take", exc)
                    continue
                quality_notes = [
                    f"Structural quality gate passed: {report.note_on_count} note-ons across {report.audible_track_count} audible tracks"
                ]
                quality_notes.extend(f"Listening prompt: {warning}" for warning in report.warnings)
                structurally_usable.append(
                    replace(
                        take,
                        decision_summary=tuple(dict.fromkeys((*take.decision_summary, *quality_notes))),
                    )
                )
            new_takes = structurally_usable
            try:
                atomic_write_json(run / "take-quality.json", quality_payload)
            except OSError as exc:
                failures.append(f"Quality report could not be saved: {type(exc).__name__}: {exc}")

            if not new_takes:
                detail = "\n".join(failures) if failures else "Every generated Take failed the structural quality gate."
                self.session_workspace.delete_trial(trial.trial_id)
                self.current_trial_id = None
                QMessageBox.warning(
                    self,
                    "Generated Takes need correction",
                    "MIDI Remixer rejected the generated files before audition because they were silent, incomplete, or structurally unsafe.\n\n"
                    + detail
                    + "\n\nTry a different arrangement, role mapping, or source combination.",
                )
                return

            next_number = self._next_take_number()
            created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            renumbered: list[RemixTake] = []
            for offset, take in enumerate(new_takes):
                take_id = f"take-{next_number + offset:03d}"
                annotated = replace(
                    take,
                    take_id=take_id,
                    trial_id=trial.trial_id,
                    trial_name=trial_name,
                    generation_run=trial_number,
                    created_at=created_at,
                    decision_summary=tuple(dict.fromkeys((*take.decision_summary, *decision_summary))),
                )
                renumbered.append(annotated)
                self.session_workspace.register_take(trial.trial_id, take_id, annotated.path)
            self.takes.extend(renumbered)
            self.project.set_source_specs(self.source_specs)
            self.project.guidance_mode = self.guidance_mode
            self.project.session_id = self.session_workspace.session_id
            self.project.trials = list(self.session_workspace.manifest.trials)
            self.project.active_trial_id = trial.trial_id
            self.project.output_directory = str(self.session_workspace.root)
            self.project.settings = {
                "target_bpm": self.target_bpm.value(), "target_key_pc": target_pc,
                "target_mode": target_mode, "mode_handling": self.mode_handling.currentText(),
                "bars": self.bars_spin.value(), "section_bars": self.section_bars.value(),
                "density": self.density.currentText(), "pair_recipes": list(pair_keys),
                "recipes": list(multi_keys), "variations_per_recipe": variations,
                "variation_strength": self.variation_strength.currentText(),
                "random_seed": self.random_seed.value(), "auto_balance": self.auto_balance.isChecked(),
                "length_mode": self.length_mode.currentText(),
                "backing_mode": getattr(self, "pair_backing_mode", "all-except-melody"),
                "lead_gain_percent": int(getattr(self, "pair_lead_gain_percent", 100)),
                "backing_gain_percent": int(getattr(self, "pair_backing_gain_percent", 100)),
                "preserve_signature_assets": bool(getattr(self, "preserve_signature_assets", True)),
                "signature_source": str(getattr(self, "signature_source", "auto")),
                "signature_placement": str(getattr(self, "signature_placement", "intro-alone")),
                "section_source_order": list(self.assisted_section_source_order),
                "section_labels": list(self.assisted_section_labels),
                "section_decisions": list(self.assisted_section_decisions),
            }
            self.project.set_takes(self.takes)
            self._session_dirty = True
            self._session_saved = False
            self._refresh_takes()
            self._update_workflow_state()
            self.statusBar().showMessage(f"{trial_name}: generated {len(renumbered)} Takes; {len(failures)} recipe(s) skipped.")
            if failures:
                QMessageBox.information(
                    self, f"{trial_name} completed with skipped recipes",
                    f"Generated {len(renumbered)} usable Takes. The following recipes were skipped without stopping the Trial:\n\n"
                    + "\n".join(f"• {item}" for item in failures),
                )
        finally:
            QApplication.restoreOverrideCursor()

    def _selected_take_ids(self) -> list[tuple[str, str, str]]:
        """Return exact selected Take keys (legacy method name retained for compatibility)."""
        rows = sorted({index.row() for index in self.takes_table.selectionModel().selectedRows()}) if self.takes_table.selectionModel() else []
        result: list[tuple[str, str, str]] = []
        for row in rows:
            item = self.takes_table.item(row, 0)
            take_key = item.data(Qt.ItemDataRole.UserRole) if item else None
            if isinstance(take_key, (tuple, list)) and len(take_key) == 3:
                result.append(tuple(str(value) for value in take_key))
        return result

    def delete_selected_takes(self) -> None:
        take_ids = self._selected_take_ids()
        if not take_ids:
            QMessageBox.information(self, APP_NAME, "Select one or more Takes to delete.")
            return
        answer = QMessageBox.question(
            self, "Delete selected Takes",
            f"Delete {len(take_ids)} selected Take{'s' if len(take_ids) != 1 else ''}?\n\n"
            "Working MIDI files will move to the session trash so Undo can restore the most recent deletion. Exported copies are not affected.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.stop_playback()
        removed_payload: list[dict[str, object]] = []
        remaining: list[RemixTake] = []
        for take in self.takes:
            if take_identity_key(take) in take_ids:
                result = self.session_workspace.delete_take(take.take_id, trial_id=take.trial_id, allow_undo=True)
                removed_payload.append({"take": take.to_dict(), "file": result})
            else:
                remaining.append(take)
        self.takes = remaining
        self.selected_take = None
        self.session_workspace.push_undo("restore_takes", {"items": removed_payload})
        self.project.set_takes(self.takes)
        self._session_dirty = True
        self._refresh_takes()
        self.statusBar().showMessage(f"Deleted {len(removed_payload)} Take(s). Use Undo Last Change to restore them.")

    def delete_current_trial(self) -> None:
        trial_id = self.current_trial_id
        if not trial_id and self.selected_take:
            trial_id = self.selected_take.trial_id
        if not trial_id:
            QMessageBox.information(self, APP_NAME, "Select a Take from the Trial you want to delete.")
            return
        trial_takes = [take for take in self.takes if take.trial_id == trial_id]
        answer = QMessageBox.question(
            self, "Delete Trial",
            f"Delete this Trial and its {len(trial_takes)} unexported Take{'s' if len(trial_takes) != 1 else ''}?\n\n"
            "This removes the Trial working directory. Original song MIDI files and exported copies are protected.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.stop_playback()
        trashed = self.session_workspace.trash_trial(trial_id)
        if trashed:
            self.session_workspace.push_undo("restore_trial", {
                "workspace": trashed,
                "takes": [take.to_dict() for take in trial_takes],
            })
        self.takes = [take for take in self.takes if take.trial_id != trial_id]
        self.selected_take = None
        self.current_trial_id = self.takes[-1].trial_id if self.takes else None
        self.project.trials = list(self.session_workspace.manifest.trials)
        self.project.active_trial_id = self.current_trial_id
        self.project.set_takes(self.takes)
        self._session_dirty = True
        self._refresh_takes()
        self.statusBar().showMessage("Trial and its unexported working files were deleted.")

    def toggle_approve_selected_take(self) -> None:
        take_ids = set(self._selected_take_ids())
        if not take_ids and self.selected_take:
            take_ids = {take_identity_key(self.selected_take)}
        if not take_ids:
            return
        self.takes = [replace(take, approved=not take.approved, status="approved" if not take.approved else "candidate") if take_identity_key(take) in take_ids else take for take in self.takes]
        self.project.set_takes(self.takes)
        self._session_dirty = True
        self._refresh_takes()

    def undo_last_change(self) -> None:
        record = self.session_workspace.pop_undo()
        if not record:
            QMessageBox.information(self, APP_NAME, "There is no destructive change to undo.")
            return
        if record.get("action") == "restore_source":
            payload = record.get("payload", {})
            try:
                spec = SourceSpec(**dict(payload.get("source") or {}))
                index = max(0, min(len(self.source_specs), int(payload.get("index", len(self.source_specs)))))
                self.source_specs.insert(index, spec)
                self.session_workspace.update_sources(item.path for item in self.source_specs)
                self._refresh_source_set()
                self.statusBar().showMessage("Restored the removed song.")
                return
            except Exception:
                QMessageBox.warning(self, APP_NAME, "The removed song could not be restored.")
                return
        if record.get("action") == "restore_trial":
            payload = record.get("payload", {})
            if not self.session_workspace.restore_trial(dict(payload.get("workspace") or {})):
                QMessageBox.warning(self, APP_NAME, "The deleted Trial could not be restored.")
                return
            restored_trial_takes: list[RemixTake] = []
            for item in payload.get("takes", []):
                try:
                    restored_trial_takes.append(remix_take_from_dict(dict(item)))
                except TypeError:
                    continue
            self.takes.extend(restored_trial_takes)
            self.takes.sort(key=lambda take: int(take.take_id.rsplit("-", 1)[-1]) if take.take_id.rsplit("-", 1)[-1].isdigit() else 999999)
            self.current_trial_id = restored_trial_takes[0].trial_id if restored_trial_takes else self.current_trial_id
            self.project.trials = list(self.session_workspace.manifest.trials)
            self.project.active_trial_id = self.current_trial_id
            self.project.set_takes(self.takes)
            self._refresh_takes()
            self.statusBar().showMessage("Restored the deleted Trial and its Takes.")
            return
        if record.get("action") != "restore_takes":
            QMessageBox.information(self, APP_NAME, "The most recent change cannot be restored automatically.")
            return
        restored: list[RemixTake] = []
        for item in record.get("payload", {}).get("items", []):
            try:
                take = remix_take_from_dict(dict(item.get("take") or {}))
            except TypeError:
                continue
            file_info = item.get("file") or {}
            if self.session_workspace.restore_take(
                take.take_id, take.trial_id, str(file_info.get("original_path") or take.path), file_info.get("trash_path"),
            ):
                restored.append(take)
        self.takes.extend(restored)
        self.takes.sort(key=lambda take: int(take.take_id.rsplit("-", 1)[-1]) if take.take_id.rsplit("-", 1)[-1].isdigit() else 999999)
        self.project.set_takes(self.takes)
        self._session_dirty = True
        self._refresh_takes()
        self.statusBar().showMessage(f"Restored {len(restored)} Take(s).")

    def save_session_for_later(self) -> None:
        self.session_controller.save_for_later()

    def _autosave_active_session(self) -> None:
        """Write a recoverable project snapshot without interrupting the DJ."""
        self.session_controller.autosave()

    def _offer_recoverable_session(self) -> None:
        # The controller presents these canonical choices:
        # message.addButton("Recover", ...)
        # message.addButton("Reject and Clean Up", ...)
        # message.addButton("Not Now", ...)
        self.session_controller.offer_recoverable()

    def reject_session(self) -> bool:
        return self.session_controller.reject_current()

    # ---------- takes ----------
    def _recipe_labels(self) -> dict[str, str]:
        labels = {item.key: item.label for item in TAKE_STRATEGIES}
        labels.update({item.key: item.label for item in MULTI_SOURCE_RECIPES})
        return labels

    def _refresh_takes(self) -> None:
        labels = self._recipe_labels()
        self.takes_table.setSortingEnabled(False)
        self.takes_table.setRowCount(len(self.takes))
        column_count = self.takes_table.columnCount()
        for row, take in enumerate(self.takes):
            suffix = take.take_id.rsplit("-", 1)[-1]
            number = int(suffix) if suffix.isdigit() else row + 1
            source_count = len(take.source_paths) if take.source_paths else len([path for path in (take.source_a, take.source_b) if path])
            base_values = [
                (f"Take {number:03d}", number),
                (take.trial_name or take.trial_id or "Legacy Trial", take.generation_run),
                ("Approved" if take.approved else take.status.title(), 0 if take.approved else 1),
                (take.name, take.name.casefold()),
                (labels.get(take.recipe or take.kind, take.kind), take.recipe or take.kind),
                (str(take.variation), take.variation),
                (str(source_count), source_count),
                (str(take.bars), take.bars),
                (f"{take.target_bpm} felt · q={take.encoded_bpm:g} · {take.target_key}", take.target_bpm),
            ]
            values = base_values[:column_count]
            for col, (text, sort_value) in enumerate(values):
                item = SortableItem(str(text), sort_value)
                item.setData(Qt.ItemDataRole.UserRole, take_identity_key(take))
                item.setToolTip(take.description)
                if take.approved:
                    item.setBackground(QBrush(QColor("#dff2df")))
                self.takes_table.setItem(row, col, item)
        self.takes_table.setSortingEnabled(True)
        self.takes_table.sortItems(0, Qt.SortOrder.AscendingOrder)
        if self.takes and self.takes_table.currentRow() < 0:
            self.takes_table.selectRow(0)
        self._update_take_actions()
        self._update_workflow_state()

    def _update_take_actions(self) -> None:
        has_takes = bool(self.takes)
        has_selected = self.selected_take is not None
        self.takes_empty_panel.setVisible(not has_takes)
        self.takes_splitter.setVisible(has_takes)
        self.play_compare_source_button.setEnabled(bool(self.source_specs))
        self.play_take_button.setEnabled(has_selected)
        self.mixer_button.setEnabled(has_selected)
        if hasattr(self, "refine_section_button"):
            self.refine_section_button.setEnabled(has_selected)
        if hasattr(self, "performance_polish_button"):
            self.performance_polish_button.setEnabled(has_selected)
        has_revision = bool(has_selected and self.selected_take and self.selected_take.parent_take_id)
        if hasattr(self, "play_changed_section_button"):
            self.play_changed_section_button.setEnabled(has_revision)
        if hasattr(self, "play_parent_section_button"):
            self.play_parent_section_button.setEnabled(has_revision)
        self.export_midi_button.setEnabled(has_selected)
        self.render_wav_button.setEnabled(has_selected)
        self.render_mp3_button.setEnabled(has_selected)
        if hasattr(self, "export_package_button"):
            self.export_package_button.setEnabled(has_selected)

    def _highlight_take_hover(self, row: int) -> None:
        if row == self._hover_take_row:
            return
        for candidate in (self._hover_take_row, row):
            if candidate < 0 or candidate >= self.takes_table.rowCount():
                continue
            selected = candidate == self.takes_table.currentRow()
            color = "#bcd8f2" if selected else "#e8f2fb" if candidate == row else "#ffffff"
            brush = QBrush(QColor(color))
            for col in range(self.takes_table.columnCount()):
                item = self.takes_table.item(candidate, col)
                if item:
                    item.setBackground(brush)
        self._hover_take_row = row

    def _take_selection_changed(self) -> None:
        row = self.takes_table.currentRow()
        item = self.takes_table.item(row, 0) if row >= 0 else None
        raw_take_key = item.data(Qt.ItemDataRole.UserRole) if item else None
        take_key = tuple(str(value) for value in raw_take_key) if isinstance(raw_take_key, (tuple, list)) and len(raw_take_key) == 3 else None
        self.selected_take = next((take for take in self.takes if take_identity_key(take) == take_key), None)
        if not self.selected_take:
            self.take_detail.clear()
            self._update_take_actions()
            return
        take = self.selected_take
        source_paths = take.source_paths or tuple(path for path in (take.source_a, take.source_b) if path)
        source_names = [self.profiles[path].title if path in self.profiles else Path(path).stem for path in source_paths]
        lineage = ""
        if take.parent_take_id:
            changed = ", ".join(take.changed_sections) or "selected section"
            lineage = (
                f"Revision lineage: {take.comparison_slot or 'B'} · parent {take.parent_take_id} "
                f"({take.parent_trial_id or 'legacy trial'}) · generation {take.revision_generation}\n"
                f"Changed section(s): {changed} · reason: {take.refinement_reason or 'selective refinement'}\n"
            )
        self.take_detail.setPlainText(
            f"{take.name}\n{take.trial_name or take.trial_id or 'Legacy Trial'} · Recipe: {self._recipe_labels().get(take.recipe or take.kind, take.kind)} · Variation {take.variation}\n"
            f"Status: {'Approved' if take.approved else take.status.title()}\n"
            + lineage
            + f"Songs ({len(source_names)}): {', '.join(source_names)}\n"
            f"Applied target: {take.target_bpm} felt BPM · MIDI quarter={take.encoded_bpm:g} · {take.target_key} · {take.bars} bars\n"
            f"Key/tempo matching: applied non-destructively in this Take; original song files remain unchanged.\n"
            + ("Decisions:\n" + "\n".join(f"• {item}" for item in take.decision_summary) + "\n\n" if take.decision_summary else "\n")
            + f"{take.description}\n\nFile: {take.path}\n\n"
            "Single-click selects. Double-click plays. Moving the mouse over another row only highlights it; it does not change the selection."
        )
        if not self.playback_engine.is_playing() and not self.playback_engine.is_paused():
            self.playback_label.setText(take.name)
        self._update_take_actions()

    # ---------- playback ----------
    def _set_transport_state(self, state: str) -> None:
        self.playback_controller.set_transport_state(state)

    def _play_path(
        self,
        path: str,
        label: str,
        version_key: str,
        *,
        start_percent: int = 0,
        start_tick: int | None = None,
    ) -> bool:
        return self.playback_controller.play_path(
            path,
            label,
            version_key,
            start_percent=start_percent,
            start_tick=start_tick,
        )

    def play_selected_take(self) -> None:
        self.playback_controller.play_selected_take()

    def pause_or_resume_current(self) -> None:
        self.playback_controller.pause_or_resume_current()

    def toggle_play_pause(self) -> None:
        self.playback_controller.toggle_play_pause()

    def stop_playback(self) -> None:
        self.playback_controller.stop()

    def _poll_playback(self) -> None:
        self.playback_controller.poll()

    def _volume_changed(self, value: int) -> None:
        self.playback_controller.volume_changed(value)

    # ---------- audio ----------
    # Compatibility wrappers keep established Guided/Assisted call sites stable
    # while AudioSetupController owns dependency and readiness behavior.
    def resolved_fluidsynth_path(self) -> str | None:
        return self.audio_setup_controller.resolved_fluidsynth_path()

    def resolved_soundfont_path(self) -> str | None:
        return self.audio_setup_controller.resolved_soundfont_path()

    def audio_ready(self) -> bool:
        return self.audio_setup_controller.ready()

    def audio_readiness_message(self) -> str:
        return self.audio_setup_controller.readiness_message()

    def _refresh_audio_readiness_ui(self) -> None:
        self.audio_setup_controller.refresh_readiness_ui()

    def _set_audio_setup_visible(self, visible: bool) -> None:
        self.audio_setup_controller.set_setup_visible(visible)

    def _apply_fluidsynth_path(self, path: str | Path) -> None:
        self.audio_setup_controller.apply_fluidsynth_path(path)

    def _auto_detect_fluidsynth(self, show_result: bool = True) -> bool:
        return self.audio_setup_controller.auto_detect_fluidsynth(show_result)

    def _choose_fluidsynth_executable(self) -> bool:
        return self.audio_setup_controller.choose_fluidsynth_executable()

    def _install_fluidsynth(self) -> bool:
        return self.audio_setup_controller.install_fluidsynth()

    def _test_audio_tone(self) -> bool:
        return self.audio_setup_controller.test_audio_tone()

    def show_audio_setup(self) -> bool:
        return self.audio_setup_controller.show_setup()

    def _ensure_fluidsynth_ready(self) -> bool:
        return self.audio_setup_controller.ensure_fluidsynth_ready()

    def _apply_soundfont_path(self, path: str) -> None:
        self.audio_setup_controller.apply_soundfont_path(path)

    def _set_soundfont_state(self, path: str | None) -> None:
        self.audio_setup_controller.set_soundfont_state(path)

    def choose_soundfont(self) -> None:
        self.audio_setup_controller.choose_soundfont()

    # ---------- mixer/export ----------
    def open_selective_refinement(self) -> None:
        if not self.selected_take:
            QMessageBox.information(self, APP_NAME, "Single-click a Take first, then refine one section.")
            return
        from app.selective_refinement_dialog import SelectiveRefinementDialog

        parent = self.selected_take
        dialog = SelectiveRefinementDialog(parent, mode="workbench", parent=self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        created, failures = self.refinement_controller.create_revisions(parent, dialog.requests())
        if not created:
            detail = "\n".join(failures) if failures else "No refinement output passed preflight."
            QMessageBox.warning(
                self,
                "No safe section revision was created",
                "The parent Take remains unchanged.\n\n" + detail,
            )
            return
        self.select_exact_take(created[0])
        self.refinement_controller.play_changed_section(created[0])
        message = f"Created {len(created)} section revision(s). A remains the parent Take; the first revision is selected."
        if failures:
            message += "\n\nSkipped:\n" + "\n".join(f"• {item}" for item in failures)
        QMessageBox.information(self, "Section refinement created", message)

    def play_selected_changed_section(self) -> None:
        if self.selected_take is not None and self.selected_take.parent_take_id:
            self.refinement_controller.play_changed_section(self.selected_take)

    def play_selected_parent_section(self) -> None:
        child = self.selected_take
        if child is None or not child.parent_take_id:
            return
        parent = next(
            (
                item for item in self.takes
                if item.take_id == child.parent_take_id
                and (not child.parent_trial_id or item.trial_id == child.parent_trial_id)
            ),
            None,
        )
        if parent is not None:
            self.refinement_controller.play_take_at_reference_section(parent, child)

    def open_mixer(self) -> None:
        if not self.selected_take:
            QMessageBox.information(self, APP_NAME, "Single-click a Take first, then open the Mixer.")
            return
        output = Path(self.selected_take.path).parent
        dialog = InstrumentationMixerDialog(self.selected_take.path, output, self)
        dialog.auditionRequested.connect(lambda path, label: self._play_path(path, label, f"mix-{Path(path).stem}"))
        dialog.mixedTakeCreated.connect(lambda path: self.statusBar().showMessage(f"Mixed MIDI created: {path}"))
        dialog.pauseRequested.connect(self.pause_or_resume_current)
        dialog.stopRequested.connect(self.stop_playback)
        self.mixer_dialog = dialog
        dialog.set_transport_state("playing" if self.playback_engine.is_playing() else "paused" if self.playback_engine.is_paused() else "stopped")
        dialog.exec()
        self.mixer_dialog = None

    def open_performance_polish(self) -> None: self.performance_controller.open_dialog()
    def export_selected(self) -> None: self.export_controller.export_selected()
    def cancel_export(self) -> None: self.export_controller.cancel_export()
    def choose_output_directory(self) -> None: self.export_controller.choose_output_directory()
    def export_selected_midi(self) -> None: self.export_controller.export_selected_midi()
    def render_selected_audio(self, kind: str) -> None: self.export_controller.render_selected_audio(kind)
    def open_working_folder(self) -> None: self.export_controller.open_working_folder()

    # ---------- project/help ----------
    def save_project(self) -> None:
        default = self.settings_store.get("last_project_directory") or self.project.output_directory or str(Path.home())
        path, _ = QFileDialog.getSaveFileName(self, "Save MIDI Remixer project", str(Path(default) / "remix-project.miremix"), "MIDI Remixer projects (*.miremix)")
        if not path:
            return
        target_pc, target_mode = self._target_key_values()
        self.project.library_paths = list(self._normalised_library_paths())
        self.project.set_source_specs(self.source_specs)
        self.project.soundfont_path = self.project.soundfont_path or self.settings_store.get("last_soundfont_path")
        project_settings = asdict(MultiSourceSettings(
            target_bpm=self.target_bpm.value(), target_key_pc=target_pc, target_mode=target_mode, mode_handling=self.mode_handling.currentText(),
            bars=self.bars_spin.value(), section_bars=self.section_bars.value(), density=self.density.currentText(),
            recipes=tuple(key[len(MULTI_PREFIX):] for key in self._selected_recipe_keys() if key.startswith(MULTI_PREFIX)),
            variations_per_recipe=self.variations.value(), variation_strength=self.variation_strength.currentText(), random_seed=self.random_seed.value(), auto_balance=self.auto_balance.isChecked(),
            preserve_signature_assets=bool(getattr(self, "preserve_signature_assets", True)),
            signature_source="foundation", signature_placement=str(getattr(self, "signature_placement", "intro-alone")),
            section_source_order=tuple(self.assisted_section_source_order),
            section_labels=tuple(self.assisted_section_labels),
        ))
        project_settings.update({
            "pair_recipes": [key[len(PAIR_PREFIX):] for key in self._selected_recipe_keys() if key.startswith(PAIR_PREFIX)],
            "length_mode": self.length_mode.currentText(),
            "section_decisions": list(self.assisted_section_decisions),
        })
        self.project.settings = project_settings
        self.project.guidance_mode = self.guidance_mode
        self.project.session_id = self.session_workspace.session_id
        self.project.trials = list(self.session_workspace.manifest.trials)
        self.project.active_trial_id = self.current_trial_id
        self.project.listening_reviews = list(self.session_workspace.manifest.listening_reviews)
        self.project.output_directory = str(self.session_workspace.root)
        self.project.set_takes(self.takes)
        target = self.project.save(path)
        self.session_workspace.mark_saved(target)
        self.settings_store.set("last_project_directory", str(target.parent))
        self._session_dirty = False
        self._session_saved = True
        self.statusBar().showMessage(f"Project saved: {target}")

    def open_project(self) -> None:
        start = self.settings_store.get("last_project_directory") or str(Path.home())
        path, _ = QFileDialog.getOpenFileName(self, "Open MIDI Remixer project", start, "MIDI Remixer projects (*.miremix)")
        if not path:
            return
        self._load_project_path(path, recovered=False)

    def _load_project_path(self, path: str, *, recovered: bool = False) -> bool:
        try:
            project = MidiRemixerProject.load(path)
        except Exception as exc:
            QMessageBox.critical(self, "Open Project", f"Could not open project.\n\n{exc}")
            return False
        self.project = project
        self.guidance_mode = project.guidance_mode if project.guidance_mode in {"guided", "fusion", "assisted", "workbench"} else self.guidance_mode
        if project.session_id:
            self.session_workspace = SessionWorkspace(self.paths.sessions, project.session_id)
        self.session_workspace.set_guidance_mode(self.guidance_mode)
        if project.listening_reviews and not self.session_workspace.manifest.listening_reviews:
            self.session_workspace.replace_listening_reviews(project.listening_reviews)
        self.current_trial_id = project.active_trial_id
        # Remix projects may remember historical Library roots, but Contract 4
        # keeps those roots authoritative in MIDI Library Manager.  Opening a
        # creative project never changes roots, scans files, or migrates Library
        # persistence.
        project_specs = project.source_specs()
        specs: list[SourceSpec] = []
        missing_paths: list[str] = []
        ineligible_paths: list[str] = []
        for spec in project_specs:
            if spec.path not in self.profiles:
                missing_paths.append(spec.path)
                continue
            allowed, _reason = self._library_path_admission(spec.path, mode=self.guidance_mode)
            if allowed:
                specs.append(spec)
            else:
                ineligible_paths.append(spec.path)
        if missing_paths or ineligible_paths:
            detail = []
            if missing_paths:
                detail.append(f"{len(missing_paths)} source(s) are not in the committed Library view")
            if ineligible_paths:
                detail.append(f"{len(ineligible_paths)} source(s) are no longer eligible for this mode")
            self.statusBar().showMessage(
                "Project opened with Library-approved sources only · " + " · ".join(detail) + ". Use MIDI Library Manager to resolve missing/review items."
            )
        self.source_specs = specs
        data = project.settings if isinstance(project.settings, dict) else {}
        self.target_bpm.setValue(int(data.get("target_bpm", self.target_bpm.value())))
        self.length_mode.setCurrentText(str(data.get("length_mode", self.length_mode.currentText())))
        self.bars_spin.setValue(int(data.get("bars", self.bars_spin.value())))
        self.section_bars.setValue(int(data.get("section_bars", self.section_bars.value())))
        self.density.setCurrentText(str(data.get("density", self.density.currentText())))
        self.variations.setValue(int(data.get("variations_per_recipe", self.variations.value())))
        self.variation_strength.setCurrentText(str(data.get("variation_strength", self.variation_strength.currentText())))
        self.random_seed.setValue(int(data.get("random_seed", self.random_seed.value())))
        self.auto_balance.setChecked(bool(data.get("auto_balance", self.auto_balance.isChecked())))
        self.mode_handling.setCurrentText(str(data.get("mode_handling", self.mode_handling.currentText())))
        self.pair_backing_mode = str(data.get("backing_mode", "all-except-melody"))
        self.pair_lead_gain_percent = max(30, min(140, int(data.get("lead_gain_percent", 100))))
        self.pair_backing_gain_percent = max(10, min(140, int(data.get("backing_gain_percent", 100))))
        self.preserve_signature_assets = bool(data.get("preserve_signature_assets", True))
        self.signature_source = str(data.get("signature_source", "auto"))
        self.signature_placement = str(data.get("signature_placement", "intro-alone"))
        self.assisted_section_source_order = tuple(
            int(item) for item in (data.get("section_source_order", []) or []) if isinstance(item, int)
        )
        self.assisted_section_labels = tuple(str(item) for item in (data.get("section_labels", []) or []))
        self.assisted_section_decisions = tuple(str(item) for item in (data.get("section_decisions", []) or []))
        target_pc = data.get("target_key_pc")
        target_mode = data.get("target_mode")
        if target_pc is None:
            self.target_key.setCurrentIndex(0)
        elif self.target_key.findData(int(target_pc)) >= 0:
            self.target_key.setCurrentIndex(self.target_key.findData(int(target_pc)))
        if self.target_mode.findData(target_mode) >= 0:
            self.target_mode.setCurrentIndex(self.target_mode.findData(target_mode))
        if project.soundfont_path and Path(project.soundfont_path).exists():
            self._apply_soundfont_path(project.soundfont_path)
        self.takes = []
        for item in project.takes:
            try:
                self.takes.append(remix_take_from_dict(dict(item)))
            except TypeError:
                continue
        self._refresh_source_set()
        saved_pair = set(data.get("pair_recipes", []) or [])
        saved_multi = set(data.get("recipes", []) or [])
        if saved_pair or saved_multi:
            for key, check in self.recipe_checks.items():
                selected = key[len(PAIR_PREFIX):] in saved_pair if key.startswith(PAIR_PREFIX) else key[len(MULTI_PREFIX):] in saved_multi
                check.setChecked(check.isEnabled() and selected)
        if project.output_directory:
            self.output_label.setText(f"Working output: {project.output_directory}")
        self._refresh_takes()
        self.tabs.setCurrentIndex(0)
        self._session_dirty = False
        self._session_saved = True
        self.statusBar().showMessage(("Recovered session: " if recovered else "Project opened: ") + str(path))
        self._after_project_loaded(str(path), recovered=recovered)

        return True

    def _after_project_loaded(self, _path: str, *, recovered: bool = False) -> None:
        """Subclass hook for restoring the appropriate decision screen."""
        return

    def clear_diagnostic_logs(self) -> None:
        answer = QMessageBox.question(
            self,
            "Clear diagnostic logs?",
            "This removes only MIDI Remixer diagnostic log files from the per-user data folder. "
            "It does not remove projects, sessions, MIDI files, audio, settings, or library databases.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        removed = clear_logs(self.paths.logs)
        self.statusBar().showMessage(f"Cleared {removed} diagnostic log file{'s' if removed != 1 else ''}.", 5000)

    def show_runtime_diagnostics(self) -> None:
        """Show best-effort diagnostics without turning an optional report into a fatal action.

        Every diagnostic source is isolated because diagnostics are commonly used
        precisely when one subsystem is unhealthy. A failed probe is displayed as
        unavailable and written to the UI error log; the application remains open.
        """

        def probe(label: str, callback, fallback):
            try:
                return callback()
            except Exception as exc:  # diagnostics must remain non-fatal
                self._write_ui_error_log(f"Runtime Diagnostics: {label}", exc)
                return fallback

        snapshot = probe("runtime supervisor", runtime_supervisor.snapshot, {})
        backend = probe("audio backend", self.playback_engine.audio_diagnostics, {})
        pair_cache = probe("pair assessment cache", lambda: get_pair_assessment_cache().snapshot(), {})
        playback_snapshot = probe("playback snapshot", self.playback_engine.snapshot, None)
        scheduler_alive = probe("playback scheduler", self.playback_engine.scheduler_alive, False)
        candidate_stats = probe(
            "candidate discovery index",
            lambda: self.library_controller.candidate_index_stats,
            None,
        )

        active = snapshot.get("active_processes", {}) if isinstance(snapshot, dict) else {}
        active = active if isinstance(active, dict) else {}
        active_lines = [f"  PID {pid}: {label}" for pid, label in active.items()] or ["  None"]
        playback_state = getattr(getattr(playback_snapshot, "state", None), "value", "unavailable")
        backend_name = (
            backend.get("backend", backend.get("backend_name", "unknown"))
            if isinstance(backend, dict)
            else "unavailable"
        )
        pair_cache = pair_cache if isinstance(pair_cache, dict) else {}

        candidate_line = "\nCandidate discovery index: not built"
        if candidate_stats is not None:
            try:
                candidate_line = (
                    f"\nCandidate discovery index: {candidate_stats.profile_count} profiles · "
                    f"{candidate_stats.pair_count} bounded pairs · "
                    f"{'cache hit' if candidate_stats.cache_hit else 'rebuilt'}"
                )
            except Exception as exc:
                self._write_ui_error_log("Runtime Diagnostics: candidate index formatting", exc)
                candidate_line = "\nCandidate discovery index: unavailable"

        QMessageBox.information(
            self,
            "Runtime Diagnostics",
            "MIDI Remixer-owned child processes:\n"
            + "\n".join(active_lines)
            + f"\n\nPlayback scheduler alive: {bool(scheduler_alive)}"
            + f"\nPlayback state: {playback_state}"
            + f"\nAudio backend: {backend_name}"
            + f"\nPair assessment cache: {pair_cache.get('memory_entries', 0)} memory entries · "
              f"{pair_cache.get('hits', 0)} hits · {pair_cache.get('misses', 0)} misses"
            + candidate_line
            + f"\n\nUser data: {self.paths.data_root}"
            + f"\nLogs: {self.paths.logs}"
            + "\nOn exit, owned children are stopped and verified in shutdown.log.",
        )

    def show_about(self) -> None:
        QMessageBox.information(
            self, "About MIDI Remixer",
            f"{APP_NAME} {APP_VERSION_DISPLAY}\n\n{TAGLINE}\n\n"
            "Local MIDI analysis, multi-song arrangement, instrumentation, and performance. No song MIDI files are distributed."
        )

    def shutdown_runtime(self) -> bool:
        """Run retryable, evidence-based shutdown phases.

        A partial failure is retained and retried on the next call. The window is
        marked fully shut down only after every owned thread, process, database,
        timer, and operation reports terminal state.
        """
        self._shutdown_requested = True
        result = self.shutdown_coordinator.run()
        self._shutdown_complete = result.complete
        if not result.complete:
            self._write_ui_error_log(
                "Application shutdown incomplete",
                RuntimeError("; ".join(result.failed_phases) or "unknown shutdown failure"),
            )
        return result.complete

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._session_dirty and bool(self.settings_store.get("session_prompt_on_close", True)):
            message = QMessageBox(self)
            message.setWindowTitle("Close MIDI Remixer")
            message.setIcon(QMessageBox.Icon.Question)
            message.setText("What should MIDI Remixer do with this session?")
            message.setInformativeText(
                f"The session contains {len(self.session_workspace.trials())} Trial(s) and {len(self.takes)} Take(s).\n\n"
                "Save Project keeps the project file and working session. Save for Later keeps a recoverable autosave. "
                "Reject Session deletes every unexported Trial/Take working file but never deletes original song MIDI files or exported copies."
            )
            save = message.addButton("Save Project…", QMessageBox.ButtonRole.AcceptRole)
            later = message.addButton("Save for Later", QMessageBox.ButtonRole.ActionRole)
            reject = message.addButton("Reject Session", QMessageBox.ButtonRole.DestructiveRole)
            cancel = message.addButton(QMessageBox.StandardButton.Cancel)
            message.exec()
            clicked = message.clickedButton()
            if clicked is save:
                self.save_project()
                if not self._session_saved:
                    event.ignore()
                    return
            elif clicked is later:
                self.save_session_for_later()
            elif clicked is reject:
                if not self.reject_session():
                    event.ignore()
                    return
            elif clicked is cancel or clicked is None:
                event.ignore()
                return
        elif self._session_dirty:
            # Prompting was disabled. Preserve the work instead of leaving an
            # ambiguous active session behind.
            self.save_session_for_later()
        if self.shutdown_runtime():
            event.accept()
            # Do not rely solely on Qt's lastWindowClosed heuristic. Hidden or
            # transient top-level Qt objects (including platform dialogs) can keep
            # the Windows event loop alive after the main Remixer window has
            # closed even though every owned runtime resource is already gone.
            # A successful Close means "exit MIDI Remixer", so terminate the
            # application event loop explicitly. aboutToQuit will call the
            # idempotent shutdown barrier once more before QApplication returns.
            app = QApplication.instance()
            if app is not None:
                app.quit()
        else:
            # Qualification/autoclose probes must fail fast with their persisted
            # shutdown evidence instead of hanging behind a modal dialog. Normal
            # users keep the retryable window-open behavior below.
            if os.environ.get("MIDI_REMIXER_QA_AUTOCLOSE_MS", "").strip():
                app = QApplication.instance()
                if app is not None:
                    app.exit(96)
                event.accept()
                return
            QMessageBox.critical(
                self,
                "MIDI Remixer could not close cleanly",
                "One or more background resources did not stop. MIDI Remixer will remain open so cleanup can be retried. "
                "See Runtime Diagnostics and logs/shutdown.log for exact details.",
            )
            event.ignore()

