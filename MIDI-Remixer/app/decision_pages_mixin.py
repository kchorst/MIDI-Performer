from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import signal
import statistics
import tempfile
import traceback
from typing import Any

from PySide6.QtCore import QLockFile, Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QFrame, QGridLayout,
    QGroupBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QListWidget, QListWidgetItem,
    QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QSlider,
    QSpinBox, QSplitter, QStackedWidget, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from app.guided_remix_wizard import GuidedRemixWizard
from app.assisted_studio_wizard import AssistedStudioWizard
from app.button_policy import set_action_enabled
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




class DecisionWorkflowPagesMixin:
    def _page_shell(self, title: str, subtitle: str) -> tuple[QWidget, QVBoxLayout]:
        page = QScrollArea()
        page.setWidgetResizable(True)
        page.setFrameShape(QFrame.Shape.NoFrame)
        page.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        page.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        content = QWidget()
        root = QVBoxLayout(content)
        root.setContentsMargins(5, 4, 8, 6)
        root.setSpacing(7)
        page.setWidget(content)
        title_row = QHBoxLayout()
        heading = QLabel(title)
        heading.setObjectName("SectionTitle")
        title_row.addWidget(heading)
        title_row.addStretch(1)
        root.addLayout(title_row)
        sub = QLabel(subtitle)
        sub.setObjectName("Subtitle")
        sub.setWordWrap(True)
        root.addWidget(sub)
        return page, root

    def _add_remixer_decision(self, root: QVBoxLayout, key: str) -> QPlainTextEdit:
        group = QGroupBox("MIDI Remixer's decision and reasoning")
        layout = QVBoxLayout(group)
        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setMaximumHeight(125)
        text.setPlainText("MIDI Remixer will explain its recommendation when enough usable musical material is available.")
        actions = QHBoxLayout()
        use = QPushButton("Use Recommendation")
        use.setObjectName("PrimaryButton")
        use.clicked.connect(lambda: self._apply_guidance_for_step(self.tabs.currentIndex(), force=True))
        alternate = QPushButton("Try Another Recommendation")
        alternate.clicked.connect(self._try_another_recommendation)
        manual = QPushButton("Change Manually")
        manual.clicked.connect(self._change_current_decision_manually)
        actions.addWidget(use)
        actions.addWidget(alternate)
        actions.addWidget(manual)
        actions.addStretch(1)
        layout.addWidget(text)
        layout.addLayout(actions)
        root.addWidget(group)
        setattr(self, f"{key}_decision_group", group)
        setattr(self, f"{key}_decision_text", text)
        setattr(self, f"{key}_decision_use", use)
        setattr(self, f"{key}_decision_alternate", alternate)
        setattr(self, f"{key}_decision_manual", manual)
        return text

    def _build_start_page(self) -> QWidget:
        page, root = self._page_shell(
            "Set up audio and connect your MIDI Library, then choose a working mode",
            "Set up FluidSynth and a SoundFont, then open MIDI Library Manager to create or recover the authoritative catalogue. "
            "MIDI Remixer reads committed Library state and never performs Library scanning or analysis itself.",
        )
        # Keep references so the page can stop repeating setup instructions once
        # those one-time tasks are complete.
        title_row = root.itemAt(0).layout()
        self.start_page_heading = title_row.itemAt(0).widget() if title_row is not None else None
        self.start_page_subtitle = root.itemAt(1).widget()

        self.audio_ready_panel = QFrame()
        self.audio_ready_panel.setObjectName("AudioReadinessPanel")
        setup_layout = QGridLayout(self.audio_ready_panel)
        setup_layout.setContentsMargins(12, 9, 12, 9)
        setup_layout.setHorizontalSpacing(12)
        setup_layout.setVerticalSpacing(6)
        setup_title = QLabel("1. Complete audio setup and connect the Library")
        setup_title.setObjectName("CardTitle")
        self.start_audio_message = QLabel("Checking audio setup…")
        self.start_audio_message.setWordWrap(True)
        self.start_fluidsynth_status = QLabel("1. FluidSynth: checking…")
        self.start_soundfont_status = QLabel("2. SoundFont: checking…")
        self.start_fluidsynth_button = QPushButton("Locate FluidSynth…")
        self.start_fluidsynth_button.setObjectName("PrimaryButton")
        self.start_fluidsynth_button.clicked.connect(self.show_audio_setup)
        self.start_soundfont_button = QPushButton("Choose SoundFont…")
        self.start_soundfont_button.setObjectName("PrimaryButton")
        self.start_soundfont_button.clicked.connect(self.choose_soundfont)
        self.start_library_status = QLabel("3. MIDI Library: not established")
        self.start_library_status.setObjectName("Subtitle")
        self.start_library_status.setWordWrap(True)
        self.start_library_button = QPushButton("Open MIDI Library Manager…")
        self.start_library_button.setObjectName("PrimaryButton")
        self.start_library_button.clicked.connect(self.open_or_setup_library)

        setup_layout.addWidget(setup_title, 0, 0, 1, 2)
        setup_layout.addWidget(self.start_audio_message, 1, 0, 1, 2)
        setup_layout.addWidget(self.start_fluidsynth_status, 2, 0)
        setup_layout.addWidget(self.start_fluidsynth_button, 2, 1)
        setup_layout.addWidget(self.start_soundfont_status, 3, 0)
        setup_layout.addWidget(self.start_soundfont_button, 3, 1)
        setup_layout.addWidget(self.start_library_status, 4, 0)
        setup_layout.addWidget(self.start_library_button, 4, 1)
        setup_layout.setColumnStretch(0, 1)
        root.addWidget(self.audio_ready_panel)

        # After setup, replace the large checklist with one compact library row.
        # The top application header already reports the active SoundFont.
        self.library_ready_strip = QFrame()
        self.library_ready_strip.setObjectName("CompactLibraryStrip")
        compact_layout = QHBoxLayout(self.library_ready_strip)
        compact_layout.setContentsMargins(10, 5, 10, 5)
        compact_layout.setSpacing(8)
        self.library_ready_summary = QLabel("MIDI library ready")
        self.library_ready_summary.setObjectName("CompactLibrarySummary")
        self.library_ready_summary.setWordWrap(True)
        self.compact_browse_library_button = QPushButton("Open Full MIDI Library…")
        self.compact_browse_library_button.setObjectName("UtilityButton")
        self.compact_browse_library_button.setToolTip(
            "Launch the authoritative MIDI Library Manager with its full playback, volume, filtering, eligibility, quarantine, repair, and maintenance tools."
        )
        self.compact_browse_library_button.clicked.connect(self.open_or_setup_library)
        compact_layout.addWidget(self.library_ready_summary, 1)
        compact_layout.addWidget(self.compact_browse_library_button)
        self.library_ready_strip.setVisible(False)
        root.addWidget(self.library_ready_strip)
        root.addWidget(self.library_scan_panel)

        self.mode_unlock_status = QLabel("Working modes unlock after an eligible authoritative MIDI Library is available.")
        self.mode_unlock_status.setObjectName("InfoPanel")
        self.mode_unlock_status.setWordWrap(True)
        root.addWidget(self.mode_unlock_status)

        guidance = QFrame()
        guidance.setObjectName("ModeChoiceGroup")
        self.guidance_group = guidance
        guidance_layout = QVBoxLayout(guidance)
        guidance_layout.setContentsMargins(14, 12, 14, 14)
        guidance_layout.setSpacing(9)
        self.mode_choice_title = QLabel("2. Choose how you want to remix")
        self.mode_choice_title.setObjectName("OnboardingModeTitle")
        self.mode_choice_subtitle = QLabel(
            "Pick the amount of help you want. This choice changes the interface and how many decisions MIDI Remixer makes for you."
        )
        self.mode_choice_subtitle.setObjectName("OnboardingModeSubtitle")
        self.mode_choice_subtitle.setWordWrap(True)
        guidance_layout.addWidget(self.mode_choice_title)
        guidance_layout.addWidget(self.mode_choice_subtitle)

        guidance_grid = QGridLayout()
        guidance_grid.setContentsMargins(0, 2, 0, 0)
        guidance_grid.setHorizontalSpacing(12)
        self.guidance_mode_buttons: dict[str, QPushButton] = {}
        self.guidance_mode_cards: dict[str, QFrame] = {}
        self.guidance_mode_selected_labels: dict[str, QLabel] = {}
        best_for = {
            "guided": "Best for a first successful mash",
            "fusion": "Best for combining useful alternate versions of one song",
            "assisted": "Best when you want recommendations plus control",
            "workbench": "Best for detailed MIDI editing and experimentation",
        }
        level_labels = {
            "guided": "1 · GUIDED",
            "fusion": "2 · VERSION FUSION",
            "assisted": "3 · ASSISTED",
            "workbench": "4 · CUSTOM / ADVANCED",
        }
        for column, (key, (_label, description)) in enumerate(GUIDANCE_MODES.items()):
            card = QFrame()
            card.setObjectName("ModeCard")
            card.setProperty("selected", False)
            card.setMinimumHeight(150)
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(12, 10, 12, 11)
            card_layout.setSpacing(7)
            top = QHBoxLayout()
            level = QLabel(level_labels[key])
            level.setObjectName("ModeLevel")
            selected_label = QLabel("SELECTED")
            selected_label.setObjectName("ModeSelectedBadge")
            selected_label.setVisible(False)
            top.addWidget(level)
            top.addStretch(1)
            top.addWidget(selected_label)
            button = QPushButton(self._mode_button_labels[key])
            button.setObjectName("DecisionButton")
            button.setCheckable(True)
            button.setMinimumHeight(46)
            button.clicked.connect(lambda _checked=False, value=key: self.choose_guidance_mode(value))
            detail = QLabel(description)
            detail.setWordWrap(True)
            detail.setObjectName("ModeDescription")
            audience = QLabel(best_for[key])
            audience.setObjectName("ModeBestFor")
            card_layout.addLayout(top)
            card_layout.addWidget(button)
            card_layout.addWidget(detail, 1)
            card_layout.addWidget(audience)
            guidance_grid.addWidget(card, 0, column)
            self.guidance_mode_buttons[key] = button
            self.guidance_mode_cards[key] = card
            self.guidance_mode_selected_labels[key] = selected_label
        for column in range(len(GUIDANCE_MODES)):
            guidance_grid.setColumnStretch(column, 1)
        guidance_layout.addLayout(guidance_grid)
        root.addWidget(guidance)

        # Kept for compatibility with the base SoundFont state updater; setup is
        # presented through the explicit readiness panel above.
        self.soundfont_banner = QFrame()
        self.soundfont_banner.setVisible(False)
        root.addWidget(self.soundfont_banner)
        root.addStretch(1)
        return page

    def _build_start_method_page(self) -> QWidget:
        page, root = self._page_shell(
            "How would you like to begin?",
            "Choose a creative starting point. A temporary discovery focus may help the search engine, but no song is permanently locked as the project's main song.",
        )
        modes = QGroupBox("Choose a starting path")
        mode_grid = QGridLayout(modes)
        self.start_mode_buttons: dict[str, QPushButton] = {}
        for index, (key, (label, description)) in enumerate(START_MODES.items()):
            card = QFrame()
            card.setObjectName("Card")
            card_layout = QVBoxLayout(card)
            button = QPushButton(label)
            button.setObjectName("DecisionButton")
            button.clicked.connect(lambda _checked=False, value=key: self.choose_start_mode(value))
            description_label = QLabel(description)
            description_label.setWordWrap(True)
            description_label.setObjectName("Subtitle")
            card_layout.addWidget(button)
            card_layout.addWidget(description_label)
            mode_grid.addWidget(card, index // 2, index % 2)
            self.start_mode_buttons[key] = button
        mode_grid.setColumnStretch(0, 1)
        mode_grid.setColumnStretch(1, 1)
        root.addWidget(modes, 1)
        note = QLabel(
            "You may return here later without losing songs, Trials, or Takes. Starting with several files, a musical role, or open exploration is just as valid as starting with one MIDI."
        )
        note.setObjectName("InfoPanel")
        note.setWordWrap(True)
        root.addWidget(note)
        return page

    def _build_select_page(self) -> QWidget:
        page, root = self._page_shell(
            "Choose or explore songs",
            "Browse the indexed library, hear songs before choosing them, or add new MIDI files. You may begin with one song, several songs, or explore before deciding.",
        )
        self.start_mode_summary = QLabel("No starting path selected.")
        self.start_mode_summary.setObjectName("InfoPanel")
        self.start_mode_summary.setWordWrap(True)
        root.addWidget(self.start_mode_summary)
        controls = QHBoxLayout()
        browse = QPushButton("Select from MIDI Library…")
        browse.setObjectName("PrimaryButton")
        browse.clicked.connect(lambda _checked=False: self.open_library_browser(purpose="sources"))
        add_files = QPushButton("Open MIDI Library Manager…")
        add_files.clicked.connect(self.add_files)
        change = QPushButton("Change Starting Path")
        change.clicked.connect(lambda: self.navigate_to(STEP_START_METHOD))
        controls.addWidget(browse)
        controls.addWidget(add_files)
        controls.addWidget(change)
        controls.addStretch(1)
        root.addLayout(controls)

        self.discovery_empty_panel = QFrame()
        self.discovery_empty_panel.setObjectName("EmptyState")
        empty_layout = QVBoxLayout(self.discovery_empty_panel)
        empty_title = QLabel("No songs selected yet")
        empty_title.setObjectName("EmptyTitle")
        empty_text = QLabel(
            "Select Library-approved songs for this mashup. The selector includes Play/Pause, Stop, and Volume; use Open MIDI Library Manager for the complete Library application."
        )
        empty_text.setWordWrap(True)
        empty_layout.addWidget(empty_title)
        empty_layout.addWidget(empty_text)
        root.addWidget(self.discovery_empty_panel)

        self.selection_table = QTableWidget(0, 5)
        self.selection_table.setHorizontalHeaderLabels(["Song", "Composer", "Style", "Felt BPM", "Current use"])
        self.selection_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.selection_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.selection_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.selection_table.cellDoubleClicked.connect(lambda *_: self._play_selection_raw())
        sh = self.selection_table.horizontalHeader()
        sh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        sh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        sh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        sh.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        sh.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        root.addWidget(self.selection_table, 1)
        audition = QHBoxLayout()
        play = QPushButton("Play Selected Song")
        play.setObjectName("PlayButton")
        play.clicked.connect(self._play_selection_raw)
        remove = QPushButton("Remove Selected")
        remove.clicked.connect(self._remove_selection_row)
        audition.addWidget(play)
        audition.addWidget(remove)
        audition.addStretch(1)
        root.addLayout(audition)
        self.discovery_next = QPushButton()  # Compatibility attribute; global navigation is used.
        self.discovery_next.setVisible(False)
        return page

    def _build_review_page(self) -> QWidget:
        page, root = self._page_shell(
            "Review songs in this mashup",
            "Confirm the songs you want available. The first row is only the current discovery focus; change it at any time. It does not permanently dominate the arrangement.",
        )
        top = QHBoxLayout()
        self.review_count = QLabel("0 songs")
        self.review_count.setObjectName("StatusPill")
        add = QPushButton("Add More from Library…")
        add.clicked.connect(lambda _checked=False: self.open_library_browser(purpose="sources"))
        play = QPushButton("Play Selected Song")
        play.setObjectName("PlayButton")
        play.clicked.connect(self._play_review_raw)
        focus = QPushButton("Use Selected as Discovery Focus")
        focus.clicked.connect(self._set_review_focus)
        remove = QPushButton("Remove Selected")
        remove.clicked.connect(self._remove_review_row)
        top.addWidget(self.review_count)
        top.addStretch(1)
        top.addWidget(play)
        top.addWidget(focus)
        top.addWidget(remove)
        top.addWidget(add)
        root.addLayout(top)
        self.review_table = QTableWidget(0, 7)
        self.review_table.setHorizontalHeaderLabels(["#", "Song", "Style", "Original key", "Felt BPM", "Arrangement", "Readiness"])
        self.review_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.review_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.review_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.review_table.cellDoubleClicked.connect(lambda *_: self._play_review_raw())
        rh = self.review_table.horizontalHeader()
        rh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        rh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column in range(2, 7):
            rh.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        root.addWidget(self.review_table, 1)
        note = QLabel("Tip: with a large library, begin with any song or group of songs, then find related material by musical role, structure, harmony, style, or contrast.")
        note.setObjectName("InfoPanel")
        note.setWordWrap(True)
        root.addWidget(note)
        return page

    def _build_discovery_page(self) -> QWidget:
        page, root = self._page_shell(
            "Find related songs",
            "Choose any current song as the temporary focus. Related songs are possibilities to explore, not judgments about artistic quality.",
        )
        focus_row = QHBoxLayout()
        focus_row.addWidget(QLabel("Current discovery focus"))
        self.discovery_focus_combo = QComboBox()
        self.discovery_focus_combo.currentIndexChanged.connect(self._focus_combo_changed)
        focus_row.addWidget(self.discovery_focus_combo, 1)
        self.play_anchor_button = QPushButton("Play Focus Song")
        self.play_anchor_button.setObjectName("PlayButton")
        self.play_anchor_button.clicked.connect(self.play_anchor_raw)
        self.play_related_button = QPushButton("Play Related Song")
        self.play_related_button.setObjectName("PlayButton")
        self.play_related_button.clicked.connect(self.play_related_source_raw)
        add_candidate = QPushButton("Add Related Song to Mashup")
        add_candidate.setObjectName("PrimaryButton")
        add_candidate.clicked.connect(self._add_selected_candidate)
        focus_row.addWidget(self.play_anchor_button)
        focus_row.addWidget(self.play_related_button)
        focus_row.addWidget(add_candidate)
        root.addLayout(focus_row)

        audition_row = QHBoxLayout()
        discovery = QPushButton("Open Remix Discovery & Audition…")
        discovery.setObjectName("PrimaryButton")
        discovery.setToolTip("Use the current focus song to preview, save, and compare several mash ideas before generating full Takes.")
        discovery.clicked.connect(self.launch_anchor_discovery)
        audition_note = QLabel("Creates short session-owned previews; it does not replace or modify the original MIDI files.")
        audition_note.setObjectName("Subtitle")
        audition_note.setWordWrap(True)
        audition_row.addWidget(discovery)
        audition_row.addWidget(audition_note, 1)
        root.addLayout(audition_row)

        self.analysis_anchor_label = QLabel("Choose a current discovery focus.")
        self.analysis_anchor_label.setObjectName("StatusPill")
        root.addWidget(self.analysis_anchor_label)

        self.analysis_empty_panel = QFrame()
        self.analysis_empty_panel.setObjectName("EmptyState")
        ep = QVBoxLayout(self.analysis_empty_panel)
        et = QLabel("No related-song analysis is available yet")
        et.setObjectName("EmptyTitle")
        ee = QLabel("Choose at least one song and make it the current discovery focus. MIDI Remixer will retrieve a manageable set of related songs from the indexed library.")
        ee.setWordWrap(True)
        ep.addWidget(et)
        ep.addWidget(ee)
        root.addWidget(self.analysis_empty_panel)

        self.analysis_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.analysis_splitter.setChildrenCollapsible(False)
        self.relationship_table = QTableWidget(0, 9)
        self.relationship_table.setHorizontalHeaderLabels([
            "Related Song", "Relationship", "Preparation", "Harmony",
            "Focus melody → related song", "Related melody → focus", "Key", "Tempo", "Meter",
        ])
        self.relationship_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.relationship_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.relationship_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.relationship_table.itemSelectionChanged.connect(self._relationship_selection_changed)
        self.relationship_table.cellDoubleClicked.connect(lambda *_: self.play_related_source_raw())
        ah = self.relationship_table.horizontalHeader()
        ah.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, 9):
            ah.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.analysis_splitter.addWidget(self.relationship_table)
        self.relationship_detail = QPlainTextEdit()
        self.relationship_detail.setReadOnly(True)
        self.analysis_splitter.addWidget(self.relationship_detail)
        self.analysis_splitter.setSizes([720, 430])
        root.addWidget(self.analysis_splitter, 1)
        self.analysis_next = QPushButton()
        self.analysis_next.setVisible(False)
        return page

    def _build_structure_page(self) -> QWidget:
        page, root = self._page_shell(
            "Inspect structure and usable ranges",
            "Review what the app detected before deciding what each song should contribute. Song start bars are editable; target duration and phrase-block decisions come later.",
        )
        self._add_remixer_decision(root, "structure")
        self.structure_table = QTableWidget(0, 8)
        self.structure_table.setHorizontalHeaderLabels([
            "Song", "Detected form", "Bars", "Meter", "Opening key", "Key changes", "Use from bar", "Notes",
        ])
        self.structure_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.structure_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        sth = self.structure_table.horizontalHeader()
        sth.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        sth.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        for column in range(2, 7):
            sth.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        sth.setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)
        root.addWidget(self.structure_table, 1)
        note = QLabel("Section labels are still approximate for unmarked MIDI files. This screen deliberately exposes uncertainty instead of silently treating a whole file as one indivisible song.")
        note.setObjectName("InfoPanel")
        note.setWordWrap(True)
        root.addWidget(note)
        return page

    def _build_roles_page(self) -> QWidget:
        page, root = self._page_shell(
            "Assign musical roles and voices",
            "Decide what each song may provide. Correct track mappings when needed. Voice extraction is an explicit manual tool for merged piano parts—not an automatic default.",
        )
        self._add_remixer_decision(root, "roles")
        row = QHBoxLayout()
        self.source_count = QLabel("0 songs")
        self.source_count.setObjectName("StatusPill")
        self.set_anchor_button = QPushButton("Use Selected as Discovery Focus")
        self.set_anchor_button.clicked.connect(self.set_selected_anchor)
        self.remove_source_button = QPushButton("Remove Selected")
        self.remove_source_button.clicked.connect(self.remove_selected_source)
        add = QPushButton("Add More…")
        add.clicked.connect(lambda _checked=False: self.open_library_browser(purpose="sources"))
        row.addWidget(self.source_count)
        row.addStretch(1)
        row.addWidget(self.set_anchor_button)
        row.addWidget(self.remove_source_button)
        row.addWidget(add)
        root.addLayout(row)
        self.source_frame = QFrame()
        self.source_frame.setObjectName("Card")
        source_layout = QVBoxLayout(self.source_frame)
        self.source_table = QTableWidget(0, 7)
        self.source_table.setHorizontalHeaderLabels(["#", "Song", "What it provides", "Start bar", "Voice handling", "Readiness", "Track mapping"])
        self.source_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.source_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.source_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.source_table.itemSelectionChanged.connect(self._source_selection_changed)
        self.source_table.cellDoubleClicked.connect(lambda *_: self.play_selected_source_raw())
        h = self.source_table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        source_layout.addWidget(self.source_table)
        root.addWidget(self.source_frame, 1)
        warning = QLabel("Piano-reduction warning: assigning an entire polyphonic right-hand track to one trumpet, flute, or other solo instrument can sound impossible. Use Tracks… and Top voice when appropriate.")
        warning.setObjectName("WarningPanel")
        warning.setWordWrap(True)
        root.addWidget(warning)
        return page

    def _build_frame_page(self) -> QWidget:
        page, root = self._page_shell(
            "Choose the shared musical frame",
            "Set the musical environment for the selected songs. Original MIDI files remain unchanged.",
        )
        self._add_remixer_decision(root, "frame")
        self.builder_source_summary = QLabel("Choose at least two songs.")
        self.builder_source_summary.setObjectName("StatusPill")
        root.addWidget(self.builder_source_summary)
        normalization = QGroupBox("Target tempo, key, mode, form, and density")
        grid = QGridLayout(normalization)
        self.target_bpm = QSpinBox()
        self.target_bpm.setRange(30, 300)
        self.target_bpm.setSuffix(" felt BPM")
        self.target_key = QComboBox()
        self.target_key.addItem("Auto — current focus tonic", None)
        for pc, name in enumerate(NOTE_NAMES):
            self.target_key.addItem(name, pc)
        self.target_mode = QComboBox()
        self.target_mode.addItem("Current focus mode", None)
        self.target_mode.addItem("Major", "major")
        self.target_mode.addItem("Minor", "minor")
        self.mode_handling = QComboBox()
        self.mode_handling.addItems(["Preserve source mode", "Adapt diatonic notes to target mode"])
        self.length_mode = QComboBox()
        self.length_mode.addItems(["Current focus full length", "Shortest selected song", "Shared 64-bar form", "Custom bars"])
        self.length_mode.currentTextChanged.connect(self._length_mode_changed)
        self.bars_spin = QSpinBox()
        self.bars_spin.setRange(4, 2048)
        self.bars_spin.setSingleStep(4)
        self.section_bars = QSpinBox()
        self.section_bars.setRange(2, 64)
        self.section_bars.setSingleStep(2)
        self.density = QComboBox()
        self.density.addItems(["Lean", "Balanced", "Full"])
        self.auto_balance = QCheckBox("Normalize song dynamics before final mixing")
        self.auto_balance.setChecked(True)
        self.target_bpm.valueChanged.connect(self._refresh_frame_summary)
        self.target_key.currentIndexChanged.connect(self._refresh_frame_summary)
        self.target_mode.currentIndexChanged.connect(self._refresh_frame_summary)
        self.mode_handling.currentIndexChanged.connect(self._refresh_frame_summary)
        self.bars_spin.valueChanged.connect(self._refresh_frame_summary)
        self.section_bars.valueChanged.connect(self._refresh_frame_summary)
        self.density.currentIndexChanged.connect(self._refresh_frame_summary)
        labels = (
            ("Target felt pulse", self.target_bpm, 0, 0),
            ("Target tonic", self.target_key, 0, 2),
            ("Target mode", self.target_mode, 1, 0),
            ("Mode treatment", self.mode_handling, 1, 2),
            ("Arrangement length", self.length_mode, 2, 0),
            ("Bars", self.bars_spin, 2, 2),
            ("Phrase / section block", self.section_bars, 3, 0),
            ("Density", self.density, 3, 2),
        )
        for label, widget, row, column in labels:
            grid.addWidget(QLabel(label), row, column)
            grid.addWidget(widget, row, column + 1)
        grid.addWidget(self.auto_balance, 4, 0, 1, 4)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        root.addWidget(normalization)
        self.frame_summary = QPlainTextEdit()
        self.frame_summary.setReadOnly(True)
        root.addWidget(self.frame_summary, 1)
        return page

    def _build_conflicts_page(self) -> QWidget:
        page, root = self._page_shell(
            "Review compatibility, conflicts, and adaptation",
            "The app reports measurable preparation and collision risks. The user decides whether tension is useful, whether mode adaptation is appropriate, and which relationships deserve an audition.",
        )
        self._add_remixer_decision(root, "conflicts")
        self.conflict_table = QTableWidget(0, 8)
        self.conflict_table.setHorizontalHeaderLabels([
            "Source relationship", "Harmony", "Focus melody fit", "Other melody fit", "Key", "Tempo", "Meter", "Adaptation",
        ])
        self.conflict_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.conflict_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        ch = self.conflict_table.horizontalHeader()
        ch.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, 8):
            ch.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        root.addWidget(self.conflict_table, 1)
        controls = QGroupBox("Current automated handling")
        cgrid = QGridLayout(controls)
        self.conflict_preserve = QCheckBox("Preserve original song notes unless mode adaptation is selected")
        self.conflict_preserve.setChecked(True)
        self.conflict_dynamic = QCheckBox("Normalize broad dynamics before the final human-controlled mixer")
        self.conflict_dynamic.setChecked(True)
        self.conflict_dynamic.toggled.connect(self.auto_balance.setChecked)
        self.conflict_note = QLabel("Chord-level conflicts are reported here; this build does not pretend that every tension should be automatically removed.")
        self.conflict_note.setWordWrap(True)
        cgrid.addWidget(self.conflict_preserve, 0, 0)
        cgrid.addWidget(self.conflict_dynamic, 1, 0)
        cgrid.addWidget(self.conflict_note, 2, 0)
        root.addWidget(controls)
        return page

    def _build_arrangements_page(self) -> QWidget:
        page, root = self._page_shell(
            "Choose arrangement approaches",
            "Every available approach is visible. Select any number, read what may be heard and why the app exposed it, then choose how many variations to create.",
        )
        self._add_remixer_decision(root, "arrangements")
        self.section_blueprint_handoff = QLabel("")
        self.section_blueprint_handoff.setObjectName("SuccessPanel")
        self.section_blueprint_handoff.setWordWrap(True)
        self.section_blueprint_handoff.setVisible(False)
        root.addWidget(self.section_blueprint_handoff)
        self.recipe_table = QTableWidget(0, 4)
        self.recipe_table.setHorizontalHeaderLabels(["Use", "Arrangement", "What may be heard", "Why MIDI Remixer suggests it"])
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
        root.addWidget(self.recipe_table, 1)
        controls = QHBoxLayout()
        self.variations = QSpinBox()
        self.variations.setRange(1, 12)
        self.variations.valueChanged.connect(self._update_generate_button)
        self.variation_strength = QComboBox()
        self.variation_strength.addItems(["Low", "Moderate", "High"])
        self.random_seed = QSpinBox()
        self.random_seed.setRange(0, 999999)
        self.random_seed.setSpecialValueText("Automatic")
        self.recommend_recipes_button = QPushButton("Select App Recommendations")
        self.recommend_recipes_button.clicked.connect(self.select_recommended_recipes)
        self.select_all_recipes_button = QPushButton("Select All Available")
        self.select_all_recipes_button.clicked.connect(lambda: self._set_all_recipes(True))
        self.clear_recipes_button = QPushButton("Clear")
        self.clear_recipes_button.clicked.connect(lambda: self._set_all_recipes(False))
        self.variations_label = QLabel("Variations per approach")
        self.variation_strength_label = QLabel("Variation strength")
        self.random_seed_label = QLabel("Seed")
        controls.addWidget(self.variations_label)
        controls.addWidget(self.variations)
        controls.addWidget(self.variation_strength_label)
        controls.addWidget(self.variation_strength)
        controls.addWidget(self.random_seed_label)
        controls.addWidget(self.random_seed)
        controls.addStretch(1)
        controls.addWidget(self.recommend_recipes_button)
        controls.addWidget(self.select_all_recipes_button)
        controls.addWidget(self.clear_recipes_button)
        root.addLayout(controls)
        footer = QFrame()
        footer.setObjectName("ActionFooter")
        fl = QHBoxLayout(footer)
        self.output_label = QLabel("Session working files are managed automatically and removed if the session is rejected.")
        self.output_label.setObjectName("StatusPill")
        self.open_session_button = QPushButton("Open Session Folder")
        self.open_session_button.clicked.connect(self.open_working_folder)
        self.generate_btn = QPushButton("Choose arrangements")
        self.generate_btn.setObjectName("PrimaryButton")
        self.generate_btn.clicked.connect(self.generate_takes)
        fl.addWidget(self.output_label, 1)
        fl.addWidget(self.open_session_button)
        fl.addWidget(self.generate_btn)
        root.addWidget(footer)
        return page

    def _build_takes_page(self) -> QWidget:
        page, root = self._page_shell(
            "Generate and compare Takes",
            "Single-click selects a Take. Double-click plays it. Hover only highlights. Compare any Take against an original song before moving to instrumentation.",
        )
        self._add_remixer_decision(root, "takes")
        self.takes_empty_panel = QFrame()
        self.takes_empty_panel.setObjectName("EmptyState")
        el = QVBoxLayout(self.takes_empty_panel)
        et = QLabel("No Takes have been generated yet")
        et.setObjectName("EmptyTitle")
        ee = QLabel("Return to Choose Arrangements, select one or more approaches, choose variations, and generate Takes.")
        ee.setWordWrap(True)
        go = QPushButton("Go to Arrangement Choices")
        go.setObjectName("PrimaryButton")
        go.clicked.connect(lambda: self.navigate_to(STEP_ARRANGEMENTS))
        el.addWidget(et)
        el.addWidget(ee)
        el.addWidget(go, 0, Qt.AlignmentFlag.AlignLeft)
        root.addWidget(self.takes_empty_panel)

        self.takes_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.takes_splitter.setChildrenCollapsible(False)
        self.takes_table = HoverTableWidget(0, 9)
        self.takes_table.setHorizontalHeaderLabels(["Take", "Trial", "Status", "Arrangement", "Recipe", "Variation", "Sources", "Bars", "Target"])
        self.takes_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.takes_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.takes_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.takes_table.itemSelectionChanged.connect(self._take_selection_changed)
        self.takes_table.cellDoubleClicked.connect(lambda *_: self.play_selected_take())
        self.takes_table.hoverRowChanged.connect(self._highlight_take_hover)
        th = self.takes_table.horizontalHeader()
        th.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        th.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        th.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        th.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        for column in range(4, 9):
            th.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.takes_splitter.addWidget(self.takes_table)
        right = QWidget()
        rl = QVBoxLayout(right)
        selected = QLabel("Selected Take")
        selected.setObjectName("CardTitle")
        self.take_detail = QPlainTextEdit()
        self.take_detail.setReadOnly(True)
        compare = QGroupBox("Compare and decide")
        cg = QGridLayout(compare)
        self.compare_source_combo = QComboBox()
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
        cg.addWidget(QLabel("Original song"), 0, 0)
        cg.addWidget(self.compare_source_combo, 0, 1)
        cg.addWidget(self.play_compare_source_button, 1, 0)
        cg.addWidget(self.play_take_button, 1, 1)
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
        cg.addLayout(action_row, 2, 0, 1, 2)
        cg.addLayout(focused_row, 3, 0, 1, 2)
        rl.addWidget(selected)
        rl.addWidget(self.take_detail, 1)
        rl.addWidget(compare)
        self.takes_splitter.addWidget(right)
        self.takes_splitter.setSizes([720, 410])
        root.addWidget(self.takes_splitter, 1)
        manage = QHBoxLayout()
        self.approve_take_button = QPushButton("Approve / Unapprove")
        self.approve_take_button.setObjectName("SuccessButton")
        self.approve_take_button.clicked.connect(self.toggle_approve_selected_take)
        self.delete_take_button = QPushButton("Delete Selected Take(s)")
        self.delete_take_button.setObjectName("DangerButton")
        self.delete_take_button.clicked.connect(self.delete_selected_takes)
        self.delete_trial_button = QPushButton("Delete Current Trial")
        self.delete_trial_button.setObjectName("DangerButton")
        self.delete_trial_button.clicked.connect(self.delete_current_trial)
        self.undo_take_button = QPushButton("Undo Last Change")
        self.undo_take_button.setObjectName("UtilityButton")
        self.undo_take_button.clicked.connect(self.undo_last_change)
        self.listening_pass_button = QPushButton("Listening Check Passed")
        self.listening_pass_button.setObjectName("SuccessButton")
        self.listening_pass_button.clicked.connect(self.mark_listening_check_passed)
        self.take_feedback_button = QPushButton("This Doesn't Sound Right…")
        self.take_feedback_button.setObjectName("UtilityButton")
        self.take_feedback_button.clicked.connect(self.show_take_feedback)
        self.create_trial_button = QPushButton("Create Another Trial")
        self.create_trial_button.setObjectName("PrimaryButton")
        self.create_trial_button.clicked.connect(lambda: self.navigate_to(STEP_ARRANGEMENTS, force=True))
        manage.addWidget(self.approve_take_button)
        manage.addWidget(self.delete_take_button)
        manage.addWidget(self.delete_trial_button)
        manage.addWidget(self.undo_take_button)
        manage.addStretch(1)
        manage.addWidget(self.listening_pass_button)
        manage.addWidget(self.take_feedback_button)
        manage.addWidget(self.create_trial_button)
        root.addLayout(manage)
        return page

    def _build_instrumentation_page(self) -> QWidget:
        page, root = self._page_shell(
            "Choose instrumentation",
            "Inspect the selected Take before balancing it. Piano reductions may need voice extraction or ensemble treatment before assigning a solo trumpet, flute, or similar instrument.",
        )
        self.instrument_take_label = QLabel("Select a Take on the previous screen.")
        self.instrument_take_label.setObjectName("InfoPanel")
        self.instrument_take_label.setWordWrap(True)
        root.addWidget(self.instrument_take_label)
        self.instrument_warning_text = QPlainTextEdit()
        self.instrument_warning_text.setReadOnly(True)
        root.addWidget(self.instrument_warning_text, 1)
        open_editor = QPushButton("Open Instrumentation Editor")
        open_editor.setObjectName("PrimaryButton")
        open_editor.clicked.connect(self.open_mixer)
        root.addWidget(open_editor, 0, Qt.AlignmentFlag.AlignLeft)
        return page

    def _build_finish_page(self) -> QWidget:
        page, root = self._page_shell(
            "Mix, perform, save, and export",
            "Balance the selected Take, apply final human performance treatment, save the project, and deliver MIDI or rendered audio.",
        )
        self.finish_take_label = QLabel("No Take selected.")
        self.finish_take_label.setObjectName("InfoPanel")
        self.finish_take_label.setWordWrap(True)
        root.addWidget(self.finish_take_label)
        actions = QGroupBox("Final actions")
        grid = QGridLayout(actions)
        open_mix = QPushButton("Open Instrumentation & Dynamics Mixer")
        open_mix.setObjectName("PrimaryButton")
        open_mix.clicked.connect(self.open_mixer)
        performance = QPushButton("Performance Polish…")
        performance.setObjectName("PrimaryButton")
        performance.clicked.connect(self.open_performance_polish)
        self.export_package_button = QPushButton("Export Selected…")
        self.export_package_button.setObjectName("PrimaryButton")
        self.export_package_button.clicked.connect(self.export_selected)
        self.cancel_export_button = QPushButton("Cancel Export")
        self.cancel_export_button.setObjectName("StopButton")
        self.cancel_export_button.clicked.connect(self.cancel_export)
        self.cancel_export_button.setVisible(False)
        self.cancel_export_button.setEnabled(False)
        self.export_midi_button = QPushButton("Quick MIDI")
        self.export_midi_button.clicked.connect(self.export_selected_midi)
        self.render_wav_button = QPushButton("Quick WAV")
        self.render_wav_button.clicked.connect(lambda: self.render_selected_audio("wav"))
        self.render_mp3_button = QPushButton("Quick MP3")
        self.render_mp3_button.clicked.connect(lambda: self.render_selected_audio("mp3"))
        open_folder = QPushButton("Open Working Folder")
        open_folder.clicked.connect(self.open_working_folder)
        save = QPushButton("Save Remix Project…")
        save.setObjectName("PrimaryButton")
        save.clicked.connect(self.save_project)
        grid.addWidget(open_mix, 0, 0)
        grid.addWidget(performance, 0, 1)
        grid.addWidget(self.export_package_button, 1, 0, 1, 2)
        grid.addWidget(self.cancel_export_button, 2, 0, 1, 2)
        grid.addWidget(self.export_midi_button, 3, 0)
        grid.addWidget(self.render_wav_button, 3, 1)
        grid.addWidget(self.render_mp3_button, 4, 0)
        grid.addWidget(open_folder, 4, 1)
        grid.addWidget(save, 5, 0, 1, 2)
        root.addWidget(actions)
        self.performance_note = QLabel("Performance Polish now creates an audible, non-destructive Take revision with bounded timing, dynamics, articulation, and swing changes. Export Selected can deliver MIDI, WAV, and MP3 together without overwriting existing files.")
        self.performance_note.setObjectName("InfoPanel")
        self.performance_note.setWordWrap(True)
        root.addWidget(self.performance_note)
        root.addStretch(1)
        return page
