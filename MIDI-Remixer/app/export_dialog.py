from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
)

from core.export_delivery import ExportSelection, safe_export_name


class ExportDialog(QDialog):
    """One clear delivery surface for MIDI, WAV, and MP3."""

    def __init__(
        self,
        take_name: str,
        default_directory: str | Path,
        *,
        audio_available: bool,
        mp3_available: bool,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Export Selected Take")
        self.setMinimumWidth(580)
        root = QVBoxLayout(self)

        title = QLabel(f"Deliver: {take_name}")
        title.setObjectName("SectionTitle")
        title.setWordWrap(True)
        root.addWidget(title)
        note = QLabel(
            "Choose one or more formats. Existing files are never overwritten; MIDI Remixer adds a number when a name is already used."
        )
        note.setObjectName("InfoPanel")
        note.setWordWrap(True)
        root.addWidget(note)

        form = QFormLayout()
        folder_row = QHBoxLayout()
        self.directory = QLineEdit(str(Path(default_directory).expanduser()))
        browse = QPushButton("Choose Folder…")
        browse.clicked.connect(self._browse)
        folder_row.addWidget(self.directory, 1)
        folder_row.addWidget(browse)
        self.basename = QLineEdit(safe_export_name(take_name))
        self.directory.textChanged.connect(self._update_state)
        self.basename.textChanged.connect(self._update_state)

        format_row = QHBoxLayout()
        self.midi = QCheckBox("MIDI")
        self.wav = QCheckBox("WAV")
        self.mp3 = QCheckBox("MP3")
        self.midi.setChecked(True)
        self.wav.setEnabled(audio_available)
        self.mp3.setEnabled(audio_available and mp3_available)
        self.wav.setToolTip("Requires FluidSynth and a selected SoundFont." if not audio_available else "Lossless rendered audio")
        self.mp3.setToolTip("Requires FluidSynth, a SoundFont, and FFmpeg." if not (audio_available and mp3_available) else "Compressed audio")
        for widget in (self.midi, self.wav, self.mp3):
            widget.toggled.connect(self._update_state)
            format_row.addWidget(widget)
        format_row.addStretch(1)

        self.bitrate = QComboBox()
        for value in (128, 192, 256, 320):
            self.bitrate.addItem(f"{value} kbps", value)
        self.bitrate.setCurrentIndex(self.bitrate.findData(192))
        self.bake_volume = QCheckBox("Bake current master volume into exported MIDI velocities")
        self.open_folder = QCheckBox("Open export folder when complete")
        self.open_folder.setChecked(True)

        form.addRow("Export folder", folder_row)
        form.addRow("File name", self.basename)
        form.addRow("Formats", format_row)
        form.addRow("MP3 quality", self.bitrate)
        form.addRow("MIDI volume", self.bake_volume)
        form.addRow("After export", self.open_folder)
        root.addLayout(form)

        self.status = QLabel()
        self.status.setObjectName("StatusPill")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Start Export")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName("PrimaryButton")
        self.buttons.accepted.connect(self._accept_if_valid)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)
        self._update_state()

    def _browse(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Choose export folder", self.directory.text())
        if selected:
            self.directory.setText(selected)
            self._update_state()

    def _selected_formats(self) -> tuple[str, ...]:
        return tuple(
            kind for kind, widget in (("midi", self.midi), ("wav", self.wav), ("mp3", self.mp3))
            if widget.isChecked() and widget.isEnabled()
        )

    def _update_state(self) -> None:
        formats = self._selected_formats()
        self.bitrate.setEnabled("mp3" in formats)
        self.bake_volume.setEnabled("midi" in formats)
        valid = bool(formats and self.directory.text().strip() and self.basename.text().strip())
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(valid)
        self.status.setText(
            f"Ready to export {len(formats)} format(s)." if formats else "Choose at least one available format."
        )

    def _accept_if_valid(self) -> None:
        try:
            self.selection()
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        self.accept()

    def selection(self) -> ExportSelection:
        return ExportSelection(
            output_directory=self.directory.text().strip(),
            basename=self.basename.text().strip(),
            formats=self._selected_formats(),
            mp3_bitrate_kbps=int(self.bitrate.currentData() or 192),
            bake_master_volume_into_midi=self.bake_volume.isChecked(),
            open_folder_after=self.open_folder.isChecked(),
        ).normalized()
