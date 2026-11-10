from __future__ import annotations

from pathlib import Path
import sqlite3
import threading

from PySide6.QtCore import QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QPushButton, QSlider, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from core.persistent_catalogue import CataloguePage, CatalogueReader
from midi_library_scan.lifecycle import eligibility_label

PATH_ROLE = int(Qt.ItemDataRole.UserRole) + 21


class PersistentLibraryBrowserDialog(QDialog):
    """Bounded read-only selector over the Library Manager catalogue.

    This dialog exists only so Remixer workflows can choose sources/anchors
    without loading the whole 96K catalogue into memory.  It is deliberately
    *not* a replacement Library UI: full browsing and management are owned by
    MIDI Library Manager and are available through ``libraryManagerRequested``.
    """

    pageLoaded = Signal(int, object, str)
    sourcesSelected = Signal(list)
    anchorSelected = Signal(str)
    auditionRequested = Signal(str, str)
    pauseRequested = Signal()
    stopRequested = Signal()
    volumeChanged = Signal(int)
    libraryManagerRequested = Signal()

    COLUMNS = (
        ("Title", "title"), ("Artist", "artist"), ("Filename", "filename"),
        ("Duration", "duration"), ("BPM", "bpm"), ("Key", "key"),
        ("Meter", "meter"), ("Tracks", "tracks"), ("Notes", "notes"),
        ("Eligibility", "eligibility"), ("Deep Status", "deep_status"), ("Arrangement", "arrangement"),
        ("Folder", "path"),
    )

    def __init__(self, database_path: str | Path, parent=None, *, purpose: str = "sources") -> None:
        super().__init__(parent)
        self.reader = CatalogueReader(database_path)
        self.purpose = purpose if purpose in {"sources", "anchor", "fusion_anchor"} else "sources"
        self.setWindowTitle("Select MIDI from Library")
        self.resize(1450, 820)
        self.setMinimumSize(900, 580)
        self._offset = 0
        self._limit = 100
        self._total_matches = 0
        self._sort_key = "title"
        self._descending = False
        self._request_generation = 0
        self._request_inflight = False
        self._queued_refresh = False
        self._query_threads: list[threading.Thread] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        heading = QHBoxLayout()
        title = QLabel("Select MIDI from Library")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        heading.addWidget(title)
        heading.addStretch(1)
        self.summary_label = QLabel("Reading saved catalogue…")
        heading.addWidget(self.summary_label)
        layout.addLayout(heading)
        explanation = QLabel(
            "Choose Library-approved MIDI for this Remixer workflow. This is a bounded read-only selector, not a second Library Manager. "
            "Use Open Full MIDI Library for playback, scanning, eligibility review, quarantine, repair, and all Library management."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        filters = QHBoxLayout()
        filters.addWidget(QLabel("Search:"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Title, artist, filename, or folder…")
        self.search.textChanged.connect(self._queue_first_page)
        filters.addWidget(self.search, 1)
        filters.addWidget(QLabel("Status:"))
        self.status_filter = QComboBox()
        for label, value in (
            ("Remixer-usable", "usable"), ("Mash Ready", "mash_ready"),
            ("Version Fusion Eligible", "fusion_eligible"),
            ("Version Fusion Candidate", "fusion_candidate"), ("Library-usable, Not Mash", "usable_not_mash"),
            ("Needs Review", "needs_review"), ("Quarantined", "quarantined"), ("Rejected", "rejected"),
            ("All discovered", "all"), ("Deep complete", "complete"), ("Deep unfinished", "unfinished"),
            ("Deep pending", "pending"), ("Deep failed", "failed"),
            ("Fast-scan failed", "quick_failed"),
        ):
            self.status_filter.addItem(label, value)
        if self.purpose == "anchor":
            self.status_filter.setCurrentIndex(self.status_filter.findData("mash_ready"))
        elif self.purpose == "fusion_anchor":
            self.status_filter.setCurrentIndex(self.status_filter.findData("fusion_eligible"))
        self.status_filter.currentIndexChanged.connect(self._first_page)
        filters.addWidget(self.status_filter)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self._refresh)
        filters.addWidget(refresh)
        layout.addLayout(filters)

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels([name for name, _ in self.COLUMNS])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        selection = QAbstractItemView.SelectionMode.SingleSelection if self.purpose in {"anchor", "fusion_anchor"} else QAbstractItemView.SelectionMode.ExtendedSelection
        self.table.setSelectionMode(selection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionsClickable(True)
        header.sectionClicked.connect(self._sort_by_column)
        # Never use ResizeToContents across every column on a 96K-backed table:
        # Windows/high-DPI measurement can dominate page refresh and make the UI
        # appear hung.  Pages are intentionally bounded and column geometry is
        # deterministic/interactive.
        for column in range(len(self.COLUMNS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
        widths = (180, 150, 290, 80, 75, 95, 70, 70, 85, 155, 105, 145, 320)
        for column, width in enumerate(widths):
            self.table.setColumnWidth(column, width)
        header.setStretchLastSection(True)
        self.table.itemDoubleClicked.connect(lambda _item: self._play_or_pause())
        layout.addWidget(self.table, 1)

        nav = QHBoxLayout()
        self._transport_state = "stopped"
        self.play_pause_button = QPushButton("Play")
        self.play_pause_button.setObjectName("PlayButton")
        self.play_pause_button.clicked.connect(self._play_or_pause)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setObjectName("StopButton")
        self.stop_button.clicked.connect(self.stopRequested.emit)
        self.transport_status = QLabel("Select a MIDI to preview")
        self.transport_status.setMinimumWidth(145)
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        parent_volume = getattr(parent, "volume_slider", None)
        self.volume_slider.setValue(parent_volume.value() if parent_volume is not None else 100)
        self.volume_slider.setMaximumWidth(130)
        self.volume_value = QLabel(f"{self.volume_slider.value()}%")
        self.volume_slider.valueChanged.connect(self._volume_changed)
        nav.addWidget(self.play_pause_button)
        nav.addWidget(self.stop_button)
        nav.addWidget(self.transport_status)
        nav.addWidget(QLabel("Volume"))
        nav.addWidget(self.volume_slider)
        nav.addWidget(self.volume_value)
        nav.addSpacing(10)
        self.page_status = QLabel("Page —")
        nav.addWidget(self.page_status)
        nav.addStretch(1)
        self.full_library_button = QPushButton("Open Full MIDI Library…")
        self.full_library_button.setObjectName("UtilityButton")
        self.full_library_button.clicked.connect(self._open_full_library)
        nav.addWidget(self.full_library_button)
        open_folder = QPushButton("Open Selected Folder")
        open_folder.clicked.connect(self._open_selected_folder)
        nav.addWidget(open_folder)
        self.use_button = QPushButton("Use as Fusion Song" if self.purpose == "fusion_anchor" else ("Use as Anchor Song" if self.purpose == "anchor" else "Add Selected to Mashup"))
        self.use_button.setObjectName("PrimaryButton")
        self.use_button.clicked.connect(self._accept_selection)
        nav.addWidget(self.use_button)
        previous = QPushButton("Previous")
        previous.clicked.connect(self._previous_page)
        self.previous_button = previous
        nav.addWidget(previous)
        next_button = QPushButton("Next")
        next_button.clicked.connect(self._next_page)
        self.next_button = next_button
        nav.addWidget(next_button)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        nav.addWidget(close)
        layout.addLayout(nav)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)
        self._search_timer.timeout.connect(self._first_page)
        self.pageLoaded.connect(self._apply_page)
        self.finished.connect(lambda _result: self.stopRequested.emit())
        self.set_transport_state("stopped")
        self._refresh()

    def _open_full_library(self) -> None:
        # Do not leave hidden Remixer playback or a selector window behind when
        # handing the user to the complete Library application.
        self.stopRequested.emit()
        self.reject()
        self.libraryManagerRequested.emit()

    def set_transport_state(self, state: str) -> None:
        self._transport_state = state if state in {"playing", "paused"} else "stopped"
        active = self._transport_state in {"playing", "paused"}
        if self._transport_state == "playing":
            self.play_pause_button.setText("Pause")
            self.transport_status.setText("Playing")
        elif self._transport_state == "paused":
            self.play_pause_button.setText("Resume")
            self.transport_status.setText("Paused")
        else:
            self.play_pause_button.setText("Play")
            self.transport_status.setText("Select a MIDI to preview")
        self.stop_button.setEnabled(active)

    def _play_or_pause(self) -> None:
        if self._transport_state in {"playing", "paused"}:
            self.pauseRequested.emit()
            return
        paths = self._selected_paths()
        if not paths:
            self.transport_status.setText("Select a MIDI to preview")
            return
        path = paths[0]
        item = self.table.item(self.table.currentRow(), 0) if self.table.currentRow() >= 0 else None
        label = item.text() if item and item.text().strip() else Path(path).stem
        self.auditionRequested.emit(path, label)

    def _volume_changed(self, value: int) -> None:
        self.volume_value.setText(f"{int(value)}%")
        self.volumeChanged.emit(int(value))

    def _selected_paths(self) -> list[str]:
        rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        paths: list[str] = []
        for row in rows:
            item = self.table.item(row, 0)
            path = str(item.data(PATH_ROLE) or "") if item else ""
            if path and path not in paths:
                paths.append(path)
        return paths

    def _accept_selection(self) -> None:
        paths = self._selected_paths()
        if not paths:
            return
        if self.purpose in {"anchor", "fusion_anchor"}:
            self.anchorSelected.emit(paths[0])
        else:
            self.sourcesSelected.emit(paths[:4])
        self.accept()

    def _queue_first_page(self) -> None:
        self._search_timer.start()

    def _first_page(self) -> None:
        self._offset = 0
        self._load_page()

    def _refresh(self) -> None:
        summary = self.reader.summary()
        if summary.get("available"):
            self.summary_label.setText(
                f"{int(summary.get('catalogued') or 0):,} catalogued · {int(summary.get('mash_ready') or 0):,} Mash Ready · "
                f"{int(summary.get('fusion_candidate') or 0):,} Fusion Candidate · {int(summary.get('complete') or 0):,} deep complete"
            )
        else:
            self.summary_label.setText("Saved catalogue is not currently readable")
        self._load_page()

    def _load_page(self) -> None:
        if self._request_inflight:
            self._queued_refresh = True
            return
        self._request_inflight = True
        self._queued_refresh = False
        self._request_generation += 1
        generation = self._request_generation
        args = dict(
            search=self.search.text(), status=str(self.status_filter.currentData() or "usable"),
            sort_key=self._sort_key, descending=self._descending,
            offset=self._offset, limit=self._limit,
        )
        self.page_status.setText("Loading saved catalogue…")
        self.previous_button.setEnabled(False)
        self.next_button.setEnabled(False)

        def run() -> None:
            page = None
            error = ""
            try:
                page = self.reader.page(**args)
            except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
                error = f"{type(exc).__name__}: {exc}"
            self.pageLoaded.emit(generation, page, error)

        self._query_threads = [thread for thread in self._query_threads if thread.is_alive()]
        thread = threading.Thread(target=run, name="RemixerCatalogueBrowserQuery", daemon=False)
        self._query_threads.append(thread)
        thread.start()

    def _apply_page(self, generation: int, page_obj: object, error: str) -> None:
        if generation != self._request_generation:
            return
        self._request_inflight = False
        if error or not isinstance(page_obj, CataloguePage):
            self.table.setRowCount(0)
            self.page_status.setText(error or "Saved catalogue could not be read.")
            if self._queued_refresh:
                self._load_page()
            return
        page = page_obj
        self._total_matches = page.total_matches
        self.table.setUpdatesEnabled(False)
        try:
            self.table.setRowCount(len(page.rows))
            for r, row in enumerate(page.rows):
                duration = row.get("duration_seconds")
                duration_text = ""
                if duration is not None:
                    seconds = max(0, int(round(float(duration))))
                    duration_text = f"{seconds // 60}:{seconds % 60:02d}"
                bpm = row.get("bpm")
                key = str(row.get("key") or "")
                mode = str(row.get("mode") or "")
                key_display = key + (f" {mode}" if key and mode and mode.casefold() not in key.casefold() else "")
                values = (
                    str(row.get("title") or ""), str(row.get("artist") or ""), str(row.get("filename") or ""),
                    duration_text, f"{float(bpm):.1f}" if bpm is not None else "", key_display,
                    str(row.get("meter") or ""), f"{int(row.get('tracks') or 0):,}", f"{int(row.get('notes') or 0):,}",
                    eligibility_label(str(row.get("eligibility_state") or "unresolved")),
                    str(row.get("deep_status") or ""), str(row.get("arrangement") or ""), str(row.get("folder") or ""),
                )
                for c, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    if c == 0:
                        item.setData(PATH_ROLE, str(row.get("path") or ""))
                    self.table.setItem(r, c, item)
        finally:
            self.table.setUpdatesEnabled(True)
        start = self._offset + 1 if self._total_matches else 0
        end = min(self._offset + len(page.rows), self._total_matches)
        self.page_status.setText(f"Showing {start:,}–{end:,} of {self._total_matches:,}")
        self.previous_button.setEnabled(self._offset > 0)
        self.next_button.setEnabled(self._offset + self._limit < self._total_matches)
        if self._queued_refresh:
            self._load_page()

    def closeEvent(self, event) -> None:
        # Catalogue queries are bounded and SQLite has a short busy timeout. Own
        # them explicitly so closing the browser cannot leave a Python worker
        # behind or emit into an already-destroyed Qt object.
        self._request_generation += 1
        for thread in tuple(self._query_threads):
            if thread.is_alive():
                thread.join(timeout=3.0)
        still_running = [thread for thread in self._query_threads if thread.is_alive()]
        if still_running:
            event.ignore()
            self.page_status.setText("Finishing the current Library query before closing…")
            return
        self._query_threads.clear()
        super().closeEvent(event)

    def _sort_by_column(self, column: int) -> None:
        if not 0 <= column < len(self.COLUMNS):
            return
        key = self.COLUMNS[column][1]
        if key == self._sort_key:
            self._descending = not self._descending
        else:
            self._sort_key, self._descending = key, False
        self._first_page()

    def _previous_page(self) -> None:
        self._offset = max(0, self._offset - self._limit)
        self._load_page()

    def _next_page(self) -> None:
        if self._offset + self._limit < self._total_matches:
            self._offset += self._limit
            self._load_page()

    def _open_selected_folder(self) -> None:
        paths = self._selected_paths()
        if not paths:
            return
        folder = Path(paths[0]).parent
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
