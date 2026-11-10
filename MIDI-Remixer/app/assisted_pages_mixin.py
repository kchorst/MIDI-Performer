from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING
import traceback

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QCursor
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
    assisted_set_recommendations,
)
from core.multi_source import SourceSpec
from core.remix_profile import key_name
from core.remix_engine import take_identity_key
from app.controllers.workflow_state import WorkflowBranchKey
from app.theme import load_stylesheet
from app.button_policy import install_button_policy, set_action_enabled

if TYPE_CHECKING:
    from app.decision_workflow import DecisionWorkflowStudio
    from core.remix_engine import RemixTake




class AssistedWizardPagesMixin:

    def _dispatch_choose_anchor_midi(self) -> None:
        """Dispatch the anchor picker through the concrete wizard when available.

        The page mixin owns the static Qt signal target so partial harnesses and
        future composition classes can construct the page safely.  The concrete
        wizard still owns the actual Library-browser behavior.
        """
        handler = getattr(self, "_choose_anchor_midi", None)
        if not callable(handler):
            raise RuntimeError("This Remixer surface does not provide an anchor Library picker.")
        handler()

    def _dispatch_sync_anchor_ui(self) -> None:
        """Synchronize anchor controls when the concrete discovery mixin is present.

        AssistedWizardPagesMixin is intentionally constructible in isolation for
        static-page tests and lightweight UI harnesses.  The real wizard composes
        AssistedWizardDiscoveryMixin, which owns ``_sync_anchor_ui``.  Connecting
        Qt signals through this stable page-owned adapter prevents page
        construction from depending on MRO details while preserving the concrete
        behavior in the full application.
        """
        handler = getattr(self, "_sync_anchor_ui", None)
        if callable(handler):
            handler()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("Version Fusion" if bool(getattr(self, "fusion_only", False)) else "Assisted Studio")
        title.setObjectName("SectionTitle")
        self.progress = QLabel("Step 1 of 5 · Prepare")
        self.progress.setObjectName("StatusPill")
        self.soundfont = QLabel("Instrument Library: none")
        self.soundfont.setObjectName("StatusPill")
        header.addWidget(title)
        header.addWidget(self.progress)
        header.addStretch(1)
        header.addWidget(self.soundfont)
        root.addLayout(header)

        intro = QLabel(
            (
                "Combine useful alternate versions of the same song. MIDI Remixer suppresses exact copies and tempo-only exports, while instrumentally different versions are valid Fusion material. Keep the strongest melody, backing, bass, drums, orchestration, or sections from each version."
            ) if bool(getattr(self, "fusion_only", False)) else (
                "MIDI Remixer recommends useful two-song and multi-song sets, explains why, and fills the technical preparation. "
                "You choose the major artistic direction; More Control remains available at every stage."
            )
        )
        intro.setObjectName("InfoPanel")
        intro.setWordWrap(True)
        root.addWidget(intro)

        self.pages = QStackedWidget()
        self.prepare_scroll = self._scroll_page(self._build_prepare_page())
        self.pages.addWidget(self.prepare_scroll)
        self.pages.addWidget(self._scroll_page(self._build_ideas_page()))
        self.pages.addWidget(self._scroll_page(self._build_shape_page()))
        self.pages.addWidget(self._scroll_page(self._build_plan_page()))
        self.pages.addWidget(self._scroll_page(self._build_results_page()))
        root.addWidget(self.pages, 1)
        root.addWidget(self._build_transport_bar())

        footer = QFrame()
        footer.setObjectName("ActionFooter")
        row = QHBoxLayout(footer)
        row.setContentsMargins(8, 5, 8, 5)
        self.back = QPushButton("← Change Working Mode")
        self.back.setObjectName("BackButton")
        self.back.setToolTip("Return to working-mode selection without deleting songs, saved ideas, Trials, or Takes.")
        self.back.clicked.connect(self._go_back)
        self.hint = QLabel("The engine recommends; you choose the broad direction.")
        self.hint.setObjectName("Subtitle")
        self.hint.setWordWrap(True)
        self.next = QPushButton("Find Version Fusion Candidates →" if bool(getattr(self, "fusion_only", False)) else "Discover Mash Ideas →")
        self.next.setObjectName("PrimaryButton")
        self.next.clicked.connect(self._go_next)
        row.addWidget(self.back)
        row.addWidget(self.hint, 1)
        row.addWidget(self.next)
        root.addWidget(footer)

    def _apply_style(self) -> None:
        self.setStyleSheet(self.studio.styleSheet() + load_stylesheet("assisted.qss"))

    def _fit_to_screen(self) -> None:
        parent = self.parentWidget()
        if parent is not None:
            self.setGeometry(parent.rect())
            return
        screen = self.screen() or QApplication.primaryScreen()
        if not screen:
            return
        available = screen.availableGeometry()
        width = min(1100, max(720, available.width() - 28))
        height = min(730, max(500, available.height() - 36))
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
        # AssistedWizardPagesMixin is also used by lightweight/partial Qt
        # compositions (including UI contract probes).  Do not make Close
        # depend on transport/discovery members that are owned by sibling
        # mixins in the concrete wizard.  The full wizard still stops its
        # transport timer and discovery work deterministically when present.
        transport_timer = getattr(self, "transport_timer", None)
        stop_timer = getattr(transport_timer, "stop", None)
        if callable(stop_timer):
            stop_timer()
        shutdown = getattr(self, "_shutdown_discovery_operations", None)
        if callable(shutdown):
            shutdown()
        super().closeEvent(event)

    def _scroll_page(self, page: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setWidget(page)
        return scroll

    def _page(self, title: str, subtitle: str) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(6, 4, 6, 4)
        root.setSpacing(8)
        heading = QLabel(title)
        heading.setObjectName("SectionTitle")
        root.addWidget(heading)
        detail = QLabel(subtitle)
        detail.setObjectName("Subtitle")
        detail.setWordWrap(True)
        root.addWidget(detail)
        return page, root

    def _build_prepare_page(self) -> QWidget:
        fusion = bool(getattr(self, "fusion_only", False))
        page, root = self._page(
            "Choose a song to fuse" if fusion else "Let MIDI Remixer recommend the songs",
            (
                "Choose one song from the saved Library to inspect its alternate arrangements, or ask MIDI Remixer for strong Fusion families. "
                "Only versions with materially different musical content are recommended."
            ) if fusion else (
                "Assisted Studio searches beyond the strict beginner gate. It may include adaptable piano reductions or whole-section songs, but it explains the preparation before you commit."
            ),
        )

        # Keep live catalogue/discovery feedback at the top of the scrollable
        # page. At laptop-height windows it must never sit below setup controls.
        self.scan_frame = QFrame()
        self.scan_frame.setObjectName("InfoPanel")
        scan = QVBoxLayout(self.scan_frame)
        scan.setContentsMargins(10, 7, 10, 7)
        scan.setSpacing(5)
        self.scan_status = QLabel("Ready")
        self.scan_status.setWordWrap(True)
        self.scan_progress = QProgressBar()
        self.scan_progress.setRange(0, 1)
        self.scan_progress.setValue(0)
        self.cancel_search_button = QPushButton("Cancel Search")
        self.cancel_search_button.setObjectName("UtilityButton")
        self.cancel_search_button.clicked.connect(self._cancel_discovery_search)
        self.cancel_search_button.setVisible(False)
        scan.addWidget(self.scan_status)
        scan.addWidget(self.scan_progress)
        scan.addWidget(self.cancel_search_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.scan_frame.setVisible(False)
        root.addWidget(self.scan_frame)

        self.readiness_group = QGroupBox("Ready to search")
        grid = QGridLayout(self.readiness_group)
        grid.setContentsMargins(10, 7, 10, 7)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(4)
        self.library_status = QLabel("MIDI library: 0 classified files")
        self.instrument_status = QLabel("Instrument Library: none")
        self.audio_status = QLabel("Audio engine: setup required")
        self.source_status = QLabel("Studio-eligible songs: 0")
        for label in (self.library_status, self.instrument_status, self.audio_status, self.source_status):
            label.setObjectName("WizardStatusLine")
            label.setWordWrap(True)
        grid.addWidget(self.library_status, 0, 0)
        grid.addWidget(self.instrument_status, 0, 1)
        grid.addWidget(self.source_status, 1, 0)
        grid.addWidget(self.audio_status, 1, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        root.addWidget(self.readiness_group)

        self.deep_analysis_controls = QFrame()
        self.deep_analysis_controls.setObjectName("ActionFooter")
        deep_row = QHBoxLayout(self.deep_analysis_controls)
        deep_row.setContentsMargins(8, 5, 8, 5)
        self.deep_analysis_status = QLabel("Detailed analysis: not running")
        self.deep_analysis_status.setObjectName("StatusPill")
        deep_row.addWidget(self.deep_analysis_status)
        deep_row.addStretch(1)
        root.addWidget(self.deep_analysis_controls)

        self.search_actions_group = QGroupBox("Where should MIDI Remixer look?")
        row = QGridLayout(self.search_actions_group)
        row.setContentsMargins(10, 7, 10, 7)
        row.setHorizontalSpacing(10)
        row.setVerticalSpacing(6)
        self.use_library = QPushButton("Find Version Fusion Candidates" if fusion else "Discover Mash Ideas")
        self.use_library.setObjectName("PrimaryButton")
        # The explicit button asks for fresh ideas. Footer Back/Next navigation
        # reuses the previous result when its anchor/library inputs are unchanged.
        self.use_library.clicked.connect(
            lambda _checked=False: self._find_recommendations(force=True, different=False)
        )
        self.choose_folder_button = QPushButton("Open MIDI Library Manager…")
        self.choose_folder_button.setObjectName("UtilityButton")
        self.choose_folder_button.clicked.connect(self._choose_folder)
        self.select_soundfont_button = QPushButton("Choose Instrument Library…")
        self.select_soundfont_button.setObjectName("UtilityButton")
        self.select_soundfont_button.clicked.connect(self._choose_soundfont)
        self.setup_audio_button = QPushButton("Locate FluidSynth…")
        self.setup_audio_button.setObjectName("UtilityButton")
        self.setup_audio_button.clicked.connect(self._setup_audio)
        row.addWidget(self.use_library, 0, 0)
        row.addWidget(self.choose_folder_button, 0, 1, 1, 2)
        row.addWidget(self.select_soundfont_button, 1, 0, 1, 2)
        row.addWidget(self.setup_audio_button, 1, 2)
        row.setColumnStretch(0, 1)
        row.setColumnStretch(1, 1)
        row.setColumnStretch(2, 1)
        root.addWidget(self.search_actions_group)

        self.discovery_options_group = QGroupBox("Discovery focus and audition length")
        dgrid = QGridLayout(self.discovery_options_group)
        dgrid.setContentsMargins(10, 7, 10, 7)
        dgrid.setHorizontalSpacing(8)
        dgrid.setVerticalSpacing(6)

        self.anchor_mode_group = QButtonGroup(self)
        self.anchor_mode_anchor = QRadioButton("Choose a Song to Fuse" if fusion else "Start with an Anchor Song")
        self.anchor_mode_any = QRadioButton("Recommend Strong Fusion Families" if fusion else "Recommend Songs for Me")
        self.anchor_mode_group.addButton(self.anchor_mode_anchor)
        self.anchor_mode_group.addButton(self.anchor_mode_any)
        if fusion:
            self.anchor_mode_anchor.setChecked(True)
            self.anchor_mode_any.setChecked(False)
        else:
            self.anchor_mode_any.setChecked(not bool(getattr(self, "_initial_anchor_path", "")))
            self.anchor_mode_anchor.setChecked(bool(getattr(self, "_initial_anchor_path", "")))

        self.anchor_combo = QComboBox()
        self.anchor_combo.setMinimumWidth(360)
        self.anchor_combo.setToolTip(
            "Choose a prepared song as the discovery anchor. The anchor focuses partner discovery; it does not have to dominate the final arrangement."
        )
        self.anchor_picker_button = QPushButton("Browse Library for Song…" if fusion else "Choose Anchor Song…")
        self.anchor_picker_button.setObjectName("UtilityButton")
        self.anchor_picker_button.setToolTip(
            "Choose any already-catalogued MIDI without loading tens of thousands of songs into a dropdown."
        )
        self.anchor_picker_button.clicked.connect(self._dispatch_choose_anchor_midi)
        self.anchor_combo.currentIndexChanged.connect(self._dispatch_sync_anchor_ui)
        self.anchor_mode_anchor.toggled.connect(self._dispatch_sync_anchor_ui)
        self.anchor_mode_any.toggled.connect(self._dispatch_sync_anchor_ui)

        self.preview_bars_combo = QComboBox()
        self.preview_bars_combo.addItem("8 bars — quick audition", 8)
        self.preview_bars_combo.addItem("16 bars — hear the handoff", 16)
        self.anchor_summary = QLabel(
            (
                "Choose a song from the saved Library. Version Fusion will examine alternate files of that composition, hide exact copies and tempo-only exports, and keep instrumentally different versions eligible."
            ) if fusion else (
                "Recommend Songs for Me explores prepared relationships across the library. Choose Start with an Anchor Song when you already know which MIDI should drive discovery."
            )
        )
        self.anchor_summary.setObjectName("InfoPanel")
        self.anchor_summary.setWordWrap(True)

        dgrid.addWidget(self.anchor_mode_anchor, 0, 0, 1, 2)
        dgrid.addWidget(self.anchor_mode_any, 0, 2, 1, 2)
        dgrid.addWidget(QLabel("Anchor"), 1, 0)
        dgrid.addWidget(self.anchor_combo, 1, 1, 1, 2)
        dgrid.addWidget(self.anchor_picker_button, 1, 3)
        dgrid.addWidget(QLabel("Preview"), 2, 0)
        dgrid.addWidget(self.preview_bars_combo, 2, 1)
        dgrid.addWidget(self.anchor_summary, 3, 0, 1, 4)
        dgrid.setColumnStretch(1, 1)
        dgrid.setColumnStretch(2, 1)
        root.addWidget(self.discovery_options_group)

        root.addStretch(1)
        return page

    def _build_ideas_page(self) -> QWidget:
        fusion = bool(getattr(self, "fusion_only", False))
        page, root = self._page(
            "Choose versions to fuse" if fusion else "Choose a recommended combination",
            (
                "Each card is one composition represented by materially different MIDI arrangements. Preview the originals, then choose the pair whose roles or sections complement one another."
            ) if fusion else (
                "The cards include pairs, trios, and—when the library supports it—larger song groups. Every idea explains the musical and technical reasons it was surfaced."
            ),
        )
        self.idea_summary = QLabel("No recommendations have been searched yet.")
        self.idea_summary.setObjectName("InfoPanel")
        self.idea_summary.setWordWrap(True)
        root.addWidget(self.idea_summary)

        saved = QFrame()
        saved.setObjectName("ActionFooter")
        saved_row = QHBoxLayout(saved)
        saved_row.setContentsMargins(8, 5, 8, 5)
        self.saved_ideas_label = QLabel("Saved ideas: 0")
        self.saved_ideas_label.setObjectName("StatusPill")
        self.compare_saved_button = QPushButton("Compare Saved Ideas…")
        self.compare_saved_button.setObjectName("UtilityButton")
        self.compare_saved_button.clicked.connect(self._compare_saved_ideas)
        self.clear_saved_button = QPushButton("Clear Saved")
        self.clear_saved_button.setObjectName("UtilityButton")
        self.clear_saved_button.clicked.connect(self._clear_saved_ideas)
        saved_row.addWidget(self.saved_ideas_label)
        saved_row.addStretch(1)
        saved_row.addWidget(self.compare_saved_button)
        saved_row.addWidget(self.clear_saved_button)
        root.addWidget(saved)

        self.preview_status = QLabel("Preview buttons generate session-owned 8- or 16-bar MIDI auditions; full Takes are created only after you choose an idea.")
        self.preview_status.setObjectName("Subtitle")
        self.preview_status.setWordWrap(True)
        root.addWidget(self.preview_status)

        container = QWidget()
        self.idea_layout = QVBoxLayout(container)
        self.idea_layout.setContentsMargins(0, 0, 0, 0)
        root.addWidget(container, 1)
        actions = QHBoxLayout()
        self.more_ideas_button = QPushButton("More Different Fusion Candidates" if fusion else "More Different Ideas")
        self.more_ideas_button.setObjectName("UtilityButton")
        self.more_ideas_button.setToolTip(
            "Open the next unseen batch already prepared for this anchor/library. No full-library search is repeated."
        )
        self.more_ideas_button.clicked.connect(
            lambda _checked=False: self._find_recommendations(force=True, different=True)
        )
        self.ideas_cancel_search_button = QPushButton("Cancel Search")
        self.ideas_cancel_search_button.setObjectName("UtilityButton")
        self.ideas_cancel_search_button.clicked.connect(self._cancel_discovery_search)
        self.ideas_cancel_search_button.setVisible(False)
        measurements = QPushButton("What Do the Measurements Mean?")
        measurements.setObjectName("UtilityButton")
        measurements.clicked.connect(self._show_measurement_help)
        manual = QPushButton("Open Full MIDI Library…" if fusion else "Choose Sources Manually…")
        manual.setObjectName("UtilityButton")
        manual.clicked.connect((lambda _checked=False: self.studio.open_library_browser(purpose="browse")) if fusion else (lambda: self._more_control(use_plan=False)))
        actions.addWidget(self.more_ideas_button)
        actions.addWidget(self.ideas_cancel_search_button)
        actions.addWidget(measurements)
        actions.addStretch(1)
        actions.addWidget(manual)
        root.addLayout(actions)
        return page

    def _build_shape_page(self) -> QWidget:
        fusion = bool(getattr(self, "fusion_only", False))
        page, root = self._page(
            "Choose how the versions should combine" if fusion else "Choose the broad artistic direction",
            (
                "MIDI Remixer has identified complementary versions of one composition. Choose the broad result you want; the engine aligns tempo/key and avoids doubling equivalent material."
            ) if fusion else (
                "MIDI Remixer has already chosen a workable group of songs and technical frame. Choose the kind of result you want; the engine handles track preparation, transposition, tempo alignment, and broad dynamics."
            ),
        )
        self.shape_title = QLabel("No group of songs selected.")
        self.shape_title.setObjectName("EmptyTitle")
        root.addWidget(self.shape_title)
        self.shape_summary = QLabel("")
        self.shape_summary.setObjectName("InfoPanel")
        self.shape_summary.setWordWrap(True)
        root.addWidget(self.shape_summary)

        intelligence = QGroupBox("Source roles and detected instruments")
        self.source_intelligence_layout = QGridLayout(intelligence)
        self.source_intelligence_layout.setHorizontalSpacing(10)
        self.source_intelligence_layout.setVerticalSpacing(10)
        root.addWidget(intelligence)

        conflicts = QGroupBox("Arrangement conflicts to review")
        conflict_layout = QVBoxLayout(conflicts)
        self.conflict_summary = QLabel("Choose a recommended set to calculate arrangement warnings.")
        self.conflict_summary.setObjectName("WarningPanel")
        self.conflict_summary.setWordWrap(True)
        self.conflict_details = QLabel("")
        self.conflict_details.setObjectName("DetailsPanel")
        self.conflict_details.setWordWrap(True)
        self.conflict_details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.conflict_details.setVisible(False)
        self.conflict_toggle = QPushButton("Show All Conflict Details ▾")
        self.conflict_toggle.setObjectName("UtilityButton")
        self.conflict_toggle.clicked.connect(self._toggle_conflict_details)
        conflict_layout.addWidget(self.conflict_summary)
        conflict_layout.addWidget(self.conflict_details)
        conflict_layout.addWidget(self.conflict_toggle, 0, Qt.AlignmentFlag.AlignLeft)
        root.addWidget(conflicts)

        direction = QGroupBox("1. What should the fused version keep?" if fusion else "1. What should organize the mash?")
        dgrid = QGridLayout(direction)
        self.direction_group = QButtonGroup(self)
        self.direction_buttons: dict[str, QPushButton] = {}
        direction_options = (
            (
                ("balanced", "Automatic Best-of-Both", "Let MIDI Remixer choose the strongest non-duplicated roles and sections from the two versions."),
                ("melody", "Lead / Solo + Full Backing", "Keep the stronger lead or solo from one version and use the complementary foundation from the other."),
                ("sections", "Best Version by Section", "Favor the stronger source for each section or hand off between arrangements instead of doubling equivalent material."),
                ("collage", "Layer Complementary Parts", "Combine unique bass, drums, chords, riffs, strings, or other roles while suppressing duplicate musical material."),
            )
            if fusion else (
                ("balanced", "Engine's Balanced Plan", "Use several contrasting recipes chosen from the measured strengths of these songs."),
                ("melody", "Lead Melody & Foundation", "Prioritize a recognizable lead over a supporting foundation or relay several leads."),
                ("sections", "Sections & Transformation", "Preserve more of each song's own harmony through exchanges, journeys, and gradual handoffs."),
                ("collage", "Build, Collage & Finale", "Use several songs by role or staged entrance; denser ideas remain limited to arrangements they can support."),
            )
        )
        for index, (key, label, description) in enumerate(direction_options):
            card = QFrame()
            card.setObjectName("Card")
            layout = QVBoxLayout(card)
            button = QPushButton(label)
            button.setObjectName("AssistedChoice")
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, value=key: self._choose_direction(value))
            detail = QLabel(description)
            detail.setObjectName("Subtitle")
            detail.setWordWrap(True)
            layout.addWidget(button)
            layout.addWidget(detail)
            dgrid.addWidget(card, index // 2, index % 2)
            self.direction_group.addButton(button)
            self.direction_buttons[key] = button
        self.direction_group.setExclusive(True)
        root.addWidget(direction)

        options = QGroupBox("2. Fine-tune the fused version" if fusion else "2. Choose the feel; MIDI Remixer handles the technical conversion")
        ogrid = QGridLayout(options)
        self.energy_combo = QComboBox()
        self.energy_combo.addItems(["Balanced target pulse", "More relaxed", "More energetic", "Use first song's pulse"])
        self.harmony_combo = QComboBox()
        self.harmony_combo.addItems(["Preserve each song's character", "Adapt severe clashes", "Prefer section-safe arrangements"])
        self.signature_combo = QComboBox()
        self.signature_combo.addItems([
            "Protect opening: play alone, then start the mash",
            "Protect opening and repeat it once as an outro",
            "Do not preserve a detected signature opening",
        ])
        self.length_combo = QComboBox()
        self.length_combo.addItems(["64-bar comparison", "96-bar development", "Shared full length"])
        self.variation_spin = QSpinBox()
        self.variation_spin.setRange(1, 4)
        self.variation_spin.setValue(1)
        for combo in (self.energy_combo, self.harmony_combo, self.signature_combo, self.length_combo):
            combo.currentIndexChanged.connect(self._generation_input_changed)
        self.variation_spin.valueChanged.connect(self._generation_input_changed)
        ogrid.addWidget(QLabel("Energy / tempo"), 0, 0)
        ogrid.addWidget(self.energy_combo, 0, 1)
        ogrid.addWidget(QLabel("Harmony treatment"), 0, 2)
        ogrid.addWidget(self.harmony_combo, 0, 3)
        ogrid.addWidget(QLabel("Signature opening"), 1, 0)
        ogrid.addWidget(self.signature_combo, 1, 1, 1, 3)
        ogrid.addWidget(QLabel("Trial length"), 2, 0)
        ogrid.addWidget(self.length_combo, 2, 1)
        ogrid.addWidget(QLabel("Variations per chosen recipe"), 2, 2)
        ogrid.addWidget(self.variation_spin, 2, 3)
        root.addWidget(options)

        more = QPushButton("More Control in Creative Workbench…")
        more.setObjectName("UtilityButton")
        more.clicked.connect(lambda: self._more_control())
        root.addWidget(more, 0, Qt.AlignmentFlag.AlignLeft)
        root.addStretch(1)
        return page

    def _build_plan_page(self) -> QWidget:
        page, root = self._page(
            "Review the plan MIDI Remixer will apply",
            "Choose the engine plan you want to hear. Each card shows the musical difference before a Trial is generated; full technical notes remain available only when needed.",
        )
        self.plan_title = QLabel("No plan selected")
        self.plan_title.setObjectName("EmptyTitle")
        root.addWidget(self.plan_title)

        plan_choices = QGroupBox("Engine plan choices")
        self.plan_choice_grid = QGridLayout(plan_choices)
        self.plan_choice_grid.setContentsMargins(8, 8, 8, 8)
        self.plan_choice_grid.setHorizontalSpacing(10)
        self.plan_choice_grid.setVerticalSpacing(10)
        root.addWidget(plan_choices)

        self.plan_summary = QLabel("Choose a recommended song set and broad direction first.")
        self.plan_summary.setObjectName("InfoPanel")
        self.plan_summary.setWordWrap(True)
        self.plan_summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(self.plan_summary)

        self.plan_change_summary = QLabel("")
        self.plan_change_summary.setObjectName("SuccessPanel")
        self.plan_change_summary.setWordWrap(True)
        self.plan_change_summary.setVisible(False)
        root.addWidget(self.plan_change_summary)

        section_plan = QGroupBox("Section lead plan — choose what the listener hears changing")
        section_layout = QVBoxLayout(section_plan)
        section_help = QLabel(
            "Each row chooses the source that leads that section. These choices affect section rotation, melody relay, staged entrances, midpoint role reversal, and the finale lead—not just the text on screen."
        )
        section_help.setObjectName("Subtitle")
        section_help.setWordWrap(True)
        section_layout.addWidget(section_help)
        section_grid_host = QWidget()
        self.section_plan_grid = QGridLayout(section_grid_host)
        self.section_plan_grid.setContentsMargins(0, 0, 0, 0)
        self.section_plan_grid.setHorizontalSpacing(8)
        self.section_plan_grid.setVerticalSpacing(6)
        section_layout.addWidget(section_grid_host)
        section_actions = QHBoxLayout()
        reset_sections = QPushButton("Reset to Engine Plan")
        reset_sections.setObjectName("UtilityButton")
        reset_sections.clicked.connect(lambda: self._reset_section_blueprint())
        self.section_plan_summary = QLabel("No section plan is active.")
        self.section_plan_summary.setObjectName("SuccessPanel")
        self.section_plan_summary.setWordWrap(True)
        section_actions.addWidget(reset_sections)
        section_actions.addStretch(1)
        section_layout.addLayout(section_actions)
        section_layout.addWidget(self.section_plan_summary)
        root.addWidget(section_plan)

        self.plan_detail_toggle = QPushButton("Show Full Technical Plan ▾")
        self.plan_detail_toggle.setObjectName("UtilityButton")
        self.plan_detail_toggle.clicked.connect(self._toggle_plan_details)
        root.addWidget(self.plan_detail_toggle, 0, Qt.AlignmentFlag.AlignLeft)

        self.plan_text = QPlainTextEdit()
        self.plan_text.setReadOnly(True)
        self.plan_text.setMaximumHeight(280)
        self.plan_text.setVisible(False)
        root.addWidget(self.plan_text)
        source_buttons = QHBoxLayout()
        self.plan_source_buttons = source_buttons
        root.addLayout(source_buttons)
        actions = QHBoxLayout()
        change_set = QPushButton("Choose Another Recommended Set")
        change_set.clicked.connect(lambda: self._show_page(1))
        more = QPushButton("Open More Control")
        more.setObjectName("UtilityButton")
        more.clicked.connect(lambda: self._more_control())
        actions.addWidget(change_set)
        actions.addStretch(1)
        actions.addWidget(more)
        root.addLayout(actions)
        root.addStretch(1)
        return page

    def _build_results_page(self) -> QWidget:
        page, root = self._page(
            "Listen, compare, and improve",
            "Each generation run is a Trial. Older Trials remain available when you revise the feel or direction, and Take/Trial deletion remains undoable in the main workspace.",
        )
        self.result_summary = QLabel("No Version Fusion Trial has been generated yet." if bool(getattr(self, "fusion_only", False)) else "No Assisted Studio Trial has been generated yet.")
        self.result_summary.setObjectName("InfoPanel")
        self.result_summary.setWordWrap(True)
        root.addWidget(self.result_summary)
        container = QWidget()
        self.results_layout = QGridLayout(container)
        self.results_layout.setContentsMargins(0, 0, 0, 0)
        self.results_layout.setHorizontalSpacing(10)
        self.results_layout.setVerticalSpacing(10)
        self.results_layout.setColumnStretch(0, 1)
        self.results_layout.setColumnStretch(1, 1)
        root.addWidget(container, 1)
        actions = QHBoxLayout()
        another = QPushButton("Create Another Fusion Trial" if bool(getattr(self, "fusion_only", False)) else "Create Another Assisted Trial")
        another.clicked.connect(lambda: self._show_page(2))
        self.results_performance_button = QPushButton("Performance Polish…")
        self.results_performance_button.setObjectName("UtilityButton")
        self.results_performance_button.setEnabled(False)
        self.results_performance_button.clicked.connect(self._performance_polish_selected)
        self.results_export_button = QPushButton("Export Selected…")
        self.results_export_button.setObjectName("PrimaryButton")
        self.results_export_button.setEnabled(False)
        self.results_export_button.clicked.connect(self.studio.export_selected)
        workbench = QPushButton("Choose a Take to Continue")
        workbench.setObjectName("PrimaryButton")
        workbench.clicked.connect(lambda: self._more_control(keep_results=True))
        self.results_workbench_button = workbench
        actions.addWidget(another)
        actions.addStretch(1)
        actions.addWidget(self.results_performance_button)
        actions.addWidget(self.results_export_button)
        actions.addWidget(workbench)
        root.addLayout(actions)
        return page

    def _build_transport_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("ActionFooter")
        row = QHBoxLayout(bar)
        row.setContentsMargins(8, 5, 8, 5)
        self.transport_play = QPushButton("Play")
        self.transport_play.setObjectName("PlayButton")
        self.transport_play.clicked.connect(self._transport_play_or_resume)
        self.transport_stop = QPushButton("Stop")
        self.transport_stop.setObjectName("StopButton")
        self.transport_stop.clicked.connect(self.studio.stop_playback)
        self.transport_status = QLabel("Ready")
        self.transport_status.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.transport_volume = QSlider(Qt.Orientation.Horizontal)
        self.transport_volume.setRange(0, 100)
        self.transport_volume.setMaximumWidth(150)
        self.transport_volume.setValue(self.studio.volume_slider.value())
        self.transport_volume.valueChanged.connect(self._volume_changed)
        self.transport_volume_value = QLabel(f"{self.transport_volume.value()}%")
        row.addWidget(self.transport_play)
        row.addWidget(self.transport_stop)
        row.addWidget(self.transport_status, 1)
        row.addWidget(QLabel("Volume"))
        row.addWidget(self.transport_volume)
        row.addWidget(self.transport_volume_value)
        return bar
