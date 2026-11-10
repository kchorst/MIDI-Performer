from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QPlainTextEdit,
    QVBoxLayout,
)

from core.multi_source import SourceSpec
from core.remix_profile import MidiProfile


class SourceTrackDialog(QDialog):
    def __init__(self, profile: MidiProfile, spec: SourceSpec, parent=None) -> None:
        super().__init__(parent)
        self.profile = profile
        self.spec = spec
        self.setWindowTitle(f"Source roles — {profile.title}")
        self.resize(680, 520)
        root = QVBoxLayout(self)
        heading = QLabel(f"{profile.title} — {profile.composer}")
        heading.setStyleSheet("font-size:16px; font-weight:700;")
        root.addWidget(heading)
        warning = QLabel(
            f"Arrangement: {profile.arrangement_type} · orchestration readiness {profile.orchestration_readiness}% · "
            f"melody extraction confidence {profile.melody_extraction_confidence}%"
        )
        warning.setWordWrap(True)
        root.addWidget(warning)

        form = QFormLayout()
        self.melody = QComboBox()
        self.foundation = QComboBox()
        for track in profile.note_tracks:
            if not track.note_count or track.likely_role == "percussion":
                continue
            instruments = ", ".join(track.program_names) if track.program_names else "GM default"
            text = f"Track {track.index + 1}: {track.name or 'unnamed'} · {track.likely_role} · {instruments} · polyphony {track.max_polyphony}"
            self.melody.addItem(text, track.index)
            self.foundation.addItem(text, track.index)
        melody_index = spec.melody_track_index if spec.melody_track_index is not None else profile.melody_track_index
        foundation_index = spec.foundation_track_index if spec.foundation_track_index is not None else profile.accompaniment_track_index
        if self.melody.findData(melody_index) >= 0:
            self.melody.setCurrentIndex(self.melody.findData(melody_index))
        if self.foundation.findData(foundation_index) >= 0:
            self.foundation.setCurrentIndex(self.foundation.findData(foundation_index))
        self.voice = QComboBox()
        self.voice.addItem("Keep complete part", "full")
        self.voice.addItem("Extract top voice for solo-instrument use", "top")
        self.voice.addItem("Extract bottom voice", "bottom")
        if self.voice.findData(spec.melody_treatment) >= 0:
            self.voice.setCurrentIndex(self.voice.findData(spec.melody_treatment))
        form.addRow("Melody / lead track", self.melody)
        form.addRow("Foundation / accompaniment track", self.foundation)
        form.addRow("Melody voice treatment", self.voice)
        root.addLayout(form)

        details = QPlainTextEdit()
        details.setReadOnly(True)
        details.setPlainText("\n\n".join(
            f"{track.display()}\n{track.instrumentation_advice}"
            for track in profile.note_tracks if track.note_count
        ) + ("\n\nWarnings\n" + "\n".join(f"• {item}" for item in profile.warnings) if profile.warnings else ""))
        root.addWidget(details, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def updated_spec(self) -> SourceSpec:
        from dataclasses import replace
        return replace(
            self.spec,
            melody_track_index=int(self.melody.currentData()) if self.melody.currentData() is not None else None,
            foundation_track_index=int(self.foundation.currentData()) if self.foundation.currentData() is not None else None,
            melody_treatment=str(self.voice.currentData() or "full"),
        )
