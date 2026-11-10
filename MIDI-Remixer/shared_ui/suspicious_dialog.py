from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from core.suspicious_midi import SuspiciousMidiCandidate


class SuspiciousMidiDialog(QDialog):
    auditionRequested = Signal(str, str)
    removeRequested = Signal(list, bool)  # paths, permanent

    def __init__(self, candidates: list[SuspiciousMidiCandidate], parent=None) -> None:
        super().__init__(parent)
        self.candidates = list(candidates)
        self.setWindowTitle("Review Suspicious MIDI Files")
        self.resize(1080, 650)
        self.setMinimumSize(760, 480)
        root = QVBoxLayout(self)

        intro = QLabel(
            "These files are unusual, not automatically bad. MIDI Remixer flagged very short, very long, empty, or nearly-empty files. "
            "Listen before removing anything. Moving to Quarantine is the safer default; permanent deletion always requires confirmation."
        )
        intro.setWordWrap(True)
        intro.setObjectName("InfoPanel")
        root.addWidget(intro)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Song", "Duration", "Notes", "Tracks", "Why flagged", "File"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        self._populate()
        root.addWidget(self.table, 1)

        actions = QHBoxLayout()
        self.play = QPushButton("Play Selected")
        self.play.setObjectName("PlayButton")
        self.play.clicked.connect(self._play_selected)
        quarantine = QPushButton("Move Selected to Quarantine…")
        quarantine.setObjectName("PrimaryButton")
        quarantine.clicked.connect(lambda: self._request_remove(False))
        delete = QPushButton("Delete Selected Permanently…")
        delete.setObjectName("StopButton")
        delete.clicked.connect(lambda: self._request_remove(True))
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        actions.addWidget(self.play)
        actions.addStretch(1)
        actions.addWidget(quarantine)
        actions.addWidget(delete)
        actions.addWidget(close)
        root.addLayout(actions)
        self.table.cellDoubleClicked.connect(lambda *_: self._play_selected())

    def _populate(self) -> None:
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self.candidates))
        for row, item in enumerate(self.candidates):
            values = [
                item.title,
                f"{item.duration_seconds:.1f}s" if item.duration_seconds < 600 else f"{item.duration_seconds / 60.0:.1f} min",
                f"{item.note_count:,}",
                str(item.track_count),
                "; ".join(item.reasons),
                item.path,
            ]
            for col, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setData(Qt.ItemDataRole.UserRole, item.path)
                cell.setToolTip(item.path)
                self.table.setItem(row, col, cell)
        self.table.setSortingEnabled(True)
        self.table.resizeColumnsToContents()
        if self.table.rowCount():
            self.table.selectRow(0)

    def _selected_paths(self) -> list[str]:
        paths: list[str] = []
        seen: set[str] = set()
        for index in self.table.selectionModel().selectedRows():
            cell = self.table.item(index.row(), 0)
            path = str(cell.data(Qt.ItemDataRole.UserRole) or "") if cell else ""
            if path and path not in seen:
                seen.add(path)
                paths.append(path)
        return paths

    def _play_selected(self) -> None:
        paths = self._selected_paths()
        if not paths:
            return
        path = paths[0]
        title = next((item.title for item in self.candidates if item.path == path), Path(path).stem)
        self.auditionRequested.emit(path, title)

    def _request_remove(self, permanent: bool) -> None:
        paths = self._selected_paths()
        if not paths:
            QMessageBox.information(self, "Review MIDI Files", "Select one or more rows first.")
            return
        verb = "permanently DELETE" if permanent else "move to MIDI Remixer Quarantine"
        warning = (
            f"{verb} {len(paths)} selected MIDI file{'s' if len(paths) != 1 else ''}?\n\n"
            + (
                "Permanent deletion cannot be undone by MIDI Remixer."
                if permanent
                else "Quarantine keeps the original files outside the active MIDI library so they can be recovered manually."
            )
        )
        if QMessageBox.question(self, "Confirm MIDI File Removal", warning) != QMessageBox.StandardButton.Yes:
            return
        # Close this modal dialog before the main window performs filesystem
        # work or shows completion/errors, so Windows cannot place those dialogs
        # behind the still-active review window.
        self.accept()
        self.removeRequested.emit(paths, permanent)
