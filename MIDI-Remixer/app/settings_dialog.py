from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.app_settings import AppSettings, DEFAULT_SETTINGS
from core.dependency_setup import check_ffmpeg, check_fluidsynth, check_soundfont, find_fluidsynth, find_soundfont
from core.remix_engine import TAKE_STRATEGIES
from core.multi_source import MULTI_SOURCE_RECIPES


class SettingsDialog(QDialog):
    def __init__(self, store: AppSettings, app_root: str | Path, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.app_root = Path(app_root)
        self.setWindowTitle("MIDI Remixer Settings")
        self.resize(780, 620)
        self.setMinimumSize(650, 480)

        root = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._audio_tab(), "Audio & Paths")
        self.tabs.addTab(self._library_tab(), "Library")
        self.tabs.addTab(self._remix_tab(), "Remix Defaults")
        self.tabs.addTab(self._interface_tab(), "Interface")
        root.addWidget(self.tabs, 1)

        bottom = QHBoxLayout()
        reset = QPushButton("Reset All Defaults")
        reset.clicked.connect(self._reset_defaults)
        bottom.addWidget(reset)
        bottom.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        bottom.addWidget(buttons)
        root.addLayout(bottom)

    def _path_row(self, value: str | None, kind: str) -> tuple[QWidget, QLineEdit]:
        box = QWidget()
        layout = QHBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        edit = QLineEdit(value or "")
        browse = QPushButton("Browse…")
        if kind == "fluidsynth":
            browse.clicked.connect(lambda: self._browse_file(edit, "Choose fluidsynth.exe", "Programs (*.exe);;All files (*.*)"))
        elif kind == "soundfont":
            browse.clicked.connect(lambda: self._browse_file(edit, "Choose instrument library", "SoundFonts (*.sf2 *.sf3);;All files (*.*)"))
        else:
            browse.clicked.connect(lambda: self._browse_folder(edit, "Choose default output folder"))
        layout.addWidget(edit, 1)
        layout.addWidget(browse)
        return box, edit

    def _audio_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        paths = QGroupBox("External tools and resources")
        form = QFormLayout(paths)
        fs_row, self.fs_path = self._path_row(self.store.get("fluidsynth_path"), "fluidsynth")
        sf_row, self.sf_path = self._path_row(self.store.get("last_soundfont_path"), "soundfont")
        out_row, self.output_path = self._path_row(self.store.get("last_output_directory"), "output")
        form.addRow("FluidSynth executable", fs_row)
        form.addRow("Instrument library", sf_row)
        form.addRow("Default working output", out_row)
        self.audio_driver = QComboBox()
        self.audio_driver.addItems(["default", "dsound", "winmidi", "portaudio"])
        self.audio_driver.setCurrentText(str(self.store.get("audio_driver", "default")))
        form.addRow("Audio driver", self.audio_driver)
        layout.addWidget(paths)

        tools = QHBoxLayout()
        detect = QPushButton("Auto-detect Performer/Remixer Audio")
        detect.clicked.connect(self._auto_detect_audio)
        test = QPushButton("Check Selected Paths")
        test.clicked.connect(self._check_audio)
        tools.addWidget(detect)
        tools.addWidget(test)
        tools.addStretch(1)
        layout.addLayout(tools)
        self.audio_result = QLabel("Saved paths and neighboring MIDI Performer installations are checked automatically at launch.")
        self.audio_result.setWordWrap(True)
        self.audio_result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.audio_result)

        migrated = self.store.get("migrated_from")
        if migrated:
            migrated_label = QLabel(f"Imported compatible settings from: {migrated}")
            migrated_label.setWordWrap(True)
            layout.addWidget(migrated_label)
        layout.addStretch(1)
        return page

    def _library_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        ownership = QGroupBox("MIDI Library Engine")
        ownership_layout = QVBoxLayout(ownership)
        ownership_text = QLabel(
            "Library folders, recursive monitoring, health classification, Detailed Analysis, throughput, "
            "quarantine, and backups are managed by the independent MIDI Library Manager. MIDI Remixer "
            "reads committed results so creative work does not own or restart 96K-scale library jobs."
        )
        ownership_text.setWordWrap(True)
        ownership_layout.addWidget(ownership_text)
        layout.addWidget(ownership)

        candidates = QGroupBox("Remixer candidate display")
        cform = QFormLayout(candidates)
        self.max_pairs = QSpinBox()
        self.max_pairs.setRange(10, 5000)
        self.max_pairs.setSingleStep(50)
        self.max_pairs.setValue(int(self.store.get("library_max_candidate_pairs", 250)))
        self.min_preparation = QSpinBox()
        self.min_preparation.setRange(0, 100)
        self.min_preparation.setSuffix("%")
        self.min_preparation.setValue(int(self.store.get("library_min_preparation", 0)))
        cform.addRow("Maximum related-song rows", self.max_pairs)
        cform.addRow("Minimum preparation score", self.min_preparation)
        layout.addWidget(candidates)
        layout.addStretch(1)
        return page

    def _remix_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        defaults = QGroupBox("Arrangement defaults")
        form = QFormLayout(defaults)
        self.default_bpm = QSpinBox(); self.default_bpm.setRange(30, 300); self.default_bpm.setValue(int(self.store.get("remix_default_bpm", 110)))
        self.target_mode = QComboBox(); self.target_mode.addItem("Anchor mode", None); self.target_mode.addItem("Major", "major"); self.target_mode.addItem("Minor", "minor")
        if self.target_mode.findData(self.store.get("remix_target_mode")) >= 0: self.target_mode.setCurrentIndex(self.target_mode.findData(self.store.get("remix_target_mode")))
        self.mode_handling = QComboBox(); self.mode_handling.addItems(["Preserve source mode", "Adapt diatonic notes to target mode"]); self.mode_handling.setCurrentText(str(self.store.get("remix_mode_handling", "Preserve source mode")))
        self.length_mode = QComboBox(); self.length_mode.addItems(["Anchor full length", "Shortest selected source", "Shared 64-bar form", "Custom bars"]); self.length_mode.setCurrentText(str(self.store.get("remix_multisource_length_mode", "Anchor full length")))
        self.default_bars = QSpinBox(); self.default_bars.setRange(4, 2048); self.default_bars.setSingleStep(4); self.default_bars.setValue(int(self.store.get("remix_default_bars", 64)))
        self.default_section = QSpinBox(); self.default_section.setRange(2, 64); self.default_section.setSingleStep(2); self.default_section.setValue(int(self.store.get("remix_default_section_bars", 8)))
        self.default_density = QComboBox(); self.default_density.addItems(["Lean", "Balanced", "Full"]); self.default_density.setCurrentText(str(self.store.get("remix_default_density", "Balanced")))
        self.variations_per_strategy = QSpinBox(); self.variations_per_strategy.setRange(1, 12); self.variations_per_strategy.setValue(int(self.store.get("remix_variations_per_strategy", 1)))
        self.variation_strength = QComboBox(); self.variation_strength.addItems(["Low", "Moderate", "High"]); self.variation_strength.setCurrentText(str(self.store.get("remix_variation_strength", "Moderate")))
        self.random_seed = QSpinBox(); self.random_seed.setRange(0, 999999); self.random_seed.setSpecialValueText("Automatic"); self.random_seed.setValue(int(self.store.get("remix_random_seed", 0)))
        self.default_melody_treatment = QComboBox()
        self.default_melody_treatment.addItem("Keep complete part", "full")
        self.default_melody_treatment.addItem("Extract top voice", "top")
        self.default_melody_treatment.addItem("Extract bottom voice", "bottom")
        self.default_melody_treatment.setCurrentIndex(max(0, self.default_melody_treatment.findData(self.store.get("remix_default_melody_treatment", "full"))))
        self.auto_balance = QCheckBox("Auto-balance song dynamics before the final mixer")
        self.auto_balance.setChecked(bool(self.store.get("remix_auto_balance", True)))
        self.open_after = QCheckBox("Open the generated Take folder after generation")
        self.open_after.setChecked(bool(self.store.get("remix_open_folder_after_generation", False)))
        form.addRow("Target felt BPM", self.default_bpm)
        form.addRow("Default target mode", self.target_mode)
        form.addRow("Default mode treatment", self.mode_handling)
        form.addRow("Default arrangement length", self.length_mode)
        form.addRow("Custom bars", self.default_bars)
        form.addRow("Section/block size", self.default_section)
        form.addRow("Density", self.default_density)
        form.addRow("Variations per selected arrangement", self.variations_per_strategy)
        form.addRow("Variation strength", self.variation_strength)
        form.addRow("Repeatable variation seed", self.random_seed)
        form.addRow("Default melody treatment", self.default_melody_treatment)
        form.addRow(self.auto_balance)
        form.addRow(self.open_after)
        layout.addWidget(defaults)

        strategy_group = QGroupBox("Default two-song arrangements")
        strategy_layout = QGridLayout(strategy_group)
        selected = set(self.store.get("remix_default_strategies", DEFAULT_SETTINGS["remix_default_strategies"]) or [])
        self.strategy_checks: dict[str, QCheckBox] = {}
        for index, strategy in enumerate(TAKE_STRATEGIES):
            check = QCheckBox(strategy.label)
            check.setToolTip(strategy.description)
            check.setChecked(strategy.key in selected)
            self.strategy_checks[strategy.key] = check
            strategy_layout.addWidget(check, index // 2, index % 2)
        layout.addWidget(strategy_group)

        multi_group = QGroupBox("Default multi-song arrangements")
        multi_layout = QGridLayout(multi_group)
        selected_multi = set(self.store.get("remix_default_multisource_recipes", DEFAULT_SETTINGS["remix_default_multisource_recipes"]) or [])
        self.multi_recipe_checks: dict[str, QCheckBox] = {}
        for index, recipe in enumerate(MULTI_SOURCE_RECIPES):
            check = QCheckBox(recipe.label)
            check.setToolTip(recipe.description)
            check.setChecked(recipe.key in selected_multi)
            self.multi_recipe_checks[recipe.key] = check
            multi_layout.addWidget(check, index // 2, index % 2)
        layout.addWidget(multi_group)
        layout.addStretch(1)
        return page

    def _interface_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        appearance = QGroupBox("Window and tables")
        form = QFormLayout(appearance)
        self.compact_header = QCheckBox("Always use a compact application header")
        self.compact_header.setChecked(bool(self.store.get("ui_compact_header", False)))
        self.adaptive_header = QCheckBox("Automatically compact the header on smaller windows")
        self.adaptive_header.setChecked(bool(self.store.get("ui_adaptive_header", True)))
        self.restore_layout = QCheckBox("Restore window size, panes, columns, and active tab")
        self.restore_layout.setChecked(bool(self.store.get("ui_restore_layout", True)))
        self.full_paths = QCheckBox("Show full file paths in library tooltips and details")
        self.full_paths.setChecked(bool(self.store.get("ui_show_full_paths", False)))
        self.row_density = QComboBox()
        self.row_density.addItems(["Compact", "Comfortable", "Spacious"])
        self.row_density.setCurrentText(str(self.store.get("ui_row_density", "Comfortable")))
        self.show_take_description = QCheckBox("Show the long Description column in the Takes table")
        self.show_take_description.setChecked(bool(self.store.get("ui_takes_show_description_column", False)))
        form.addRow(self.compact_header)
        form.addRow(self.adaptive_header)
        form.addRow(self.restore_layout)
        form.addRow(self.full_paths)
        form.addRow(self.show_take_description)
        form.addRow("Table row density", self.row_density)
        layout.addWidget(appearance)
        info = QLabel("The header and action bars stay visible while the center workspace resizes. The Mash Builder scrolls independently, and the Library and Takes pages use draggable panes instead of forcing the whole window off-screen.")
        info.setWordWrap(True)
        layout.addWidget(info)
        layout.addStretch(1)
        return page

    def _browse_file(self, edit: QLineEdit, title: str, file_filter: str) -> None:
        start = edit.text().strip() or str(self.app_root)
        path, _ = QFileDialog.getOpenFileName(self, title, start, file_filter)
        if path:
            edit.setText(path)

    def _browse_folder(self, edit: QLineEdit, title: str) -> None:
        start = edit.text().strip() or str(self.app_root)
        path = QFileDialog.getExistingDirectory(self, title, start)
        if path:
            edit.setText(path)

    def _auto_detect_audio(self) -> None:
        fs = find_fluidsynth(self.app_root, self.fs_path.text().strip() or None)
        sf = find_soundfont(self.app_root, self.sf_path.text().strip() or None, self.store.get("last_soundfont_directory"))
        if fs:
            self.fs_path.setText(fs)
        if sf:
            self.sf_path.setText(sf)
        self._check_audio()

    def _check_audio(self) -> None:
        fs = check_fluidsynth(self.app_root, self.fs_path.text().strip() or None)
        sf = check_soundfont(
            self.app_root,
            self.sf_path.text().strip() or None,
            self.store.get("last_soundfont_directory"),
            fluidsynth_path=fs.path,
            probe_load=bool(fs.confirmed),
        )
        ffmpeg = check_ffmpeg(self.app_root)

        def describe(check) -> str:
            state = "ready" if check.confirmed else ("found but not confirmed" if check.found else "not found")
            lines = [f"{check.name}: {state}", check.path or check.detail]
            if check.path and check.detail:
                lines.append(check.detail)
            if check.command:
                lines.append("Test: " + " ".join(check.command))
            if check.exit_code is not None:
                lines.append(f"Exit code: {check.exit_code}")
            if check.corrective_action:
                lines.append("Next step: " + check.corrective_action)
            return "\n".join(line for line in lines if line)

        self.audio_result.setText(
            describe(fs) + "\n\n" + describe(sf) + "\n\n" + describe(ffmpeg)
        )

    def _reset_defaults(self) -> None:
        result = QMessageBox.question(self, "Reset Settings", "Reset all MIDI Remixer settings and layout preferences to defaults?")
        if result == QMessageBox.StandardButton.Yes:
            self.store.reset()
            QMessageBox.information(self, "Reset Settings", "Defaults were restored. Close and reopen Settings to see every default value.")

    def values(self) -> dict[str, Any]:
        strategies = [key for key, check in self.strategy_checks.items() if check.isChecked()]
        if not strategies:
            strategies = list(DEFAULT_SETTINGS["remix_default_strategies"])
        multi_recipes = [key for key, check in self.multi_recipe_checks.items() if check.isChecked()]
        if not multi_recipes:
            multi_recipes = list(DEFAULT_SETTINGS["remix_default_multisource_recipes"])
        sf = self.sf_path.text().strip() or None
        return {
            "fluidsynth_path": self.fs_path.text().strip() or None,
            "last_soundfont_path": sf,
            "last_soundfont_directory": str(Path(sf).parent) if sf else self.store.get("last_soundfont_directory"),
            "last_output_directory": self.output_path.text().strip() or None,
            "audio_driver": self.audio_driver.currentText(),
            "library_pair_scope": "selected",
            "library_max_candidate_pairs": self.max_pairs.value(),
            "library_min_preparation": self.min_preparation.value(),
            "remix_default_bpm": self.default_bpm.value(),
            "remix_target_mode": self.target_mode.currentData(),
            "remix_mode_handling": self.mode_handling.currentText(),
            "remix_multisource_length_mode": self.length_mode.currentText(),
            "remix_default_bars": self.default_bars.value(),
            "remix_default_section_bars": self.default_section.value(),
            "remix_default_density": self.default_density.currentText(),
            "remix_default_strategies": strategies,
            "remix_default_multisource_recipes": multi_recipes,
            "remix_variations_per_strategy": self.variations_per_strategy.value(),
            "remix_variation_strength": self.variation_strength.currentText(),
            "remix_random_seed": self.random_seed.value(),
            "remix_default_melody_treatment": self.default_melody_treatment.currentData(),
            "remix_auto_balance": self.auto_balance.isChecked(),
            "remix_open_folder_after_generation": self.open_after.isChecked(),
            "ui_compact_header": self.compact_header.isChecked(),
            "ui_adaptive_header": self.adaptive_header.isChecked(),
            "ui_restore_layout": self.restore_layout.isChecked(),
            "ui_show_full_paths": self.full_paths.isChecked(),
            "ui_takes_show_description_column": self.show_take_description.isChecked(),
            "ui_row_density": self.row_density.currentText(),
        }

    def accept(self) -> None:
        self.store.update_many(self.values())
        super().accept()
