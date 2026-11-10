from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import shutil
import sqlite3
import statistics
import threading
import time
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
from app.controllers.session import SessionController
from app.controllers.lifecycle import ShutdownCoordinator
from app.button_policy import install_button_policy, set_action_enabled
from app.deferred_actions import cancel_owned, schedule_owned
from app.library_manager_discovery import library_manager_candidates
from app.library_family_authority import family_reconciliation_readiness
from app.persistent_library_browser import PersistentLibraryBrowserDialog
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
from core.dependency_setup import find_fluidsynth, find_soundfont
from core.fluidsynth_player import FluidSynthPlayer
from core.library_index import LibraryIndex
from core.library_backup import create_library_intelligence_backup
from core.library_mode_cache import build_large_library_mode_cache, build_library_mode_cache
from core.logging_config import append_bounded, clear_logs
from core.mash_preferences import ignore_profile_pair, pair_identities, pair_is_ignored
from core.pair_assessment_cache import configure_pair_assessment_cache, get_pair_assessment_cache
from core.performance_trace import record_performance
from core.python_runtime import launch_gui_python_script
from core.operation_diagnostics import operation_failure, write_diagnostic
from core.staged_library import enrich_deep_profile, is_provisional_profile, quick_result_to_profile, quick_results_to_profiles
from core.suspicious_midi import suspicious_midi_candidates
from core.remix_guidance import recipe_advice
from core.recursive_directory_watcher import RecursiveDirectoryWatcher
from core.session_workspace import SessionWorkspace
from core.multi_source import (
    DEFAULT_MULTI_SOURCE_RECIPE_KEYS, MULTI_SOURCE_RECIPES, MultiSourceSettings,
    SourceSpec, generate_multi_source_takes, recommended_multi_source_recipes,
)
from core.playback_engine import PlaybackEngine
from core.reference_catalog import ReferenceCatalog
from core.remix_engine import (
    DEFAULT_TAKE_STRATEGY_KEYS, TAKE_STRATEGIES, RemixSettings, RemixTake,
    generate_remix_takes, take_identity_key,
)
from core.remix_profile import MidiProfile, NOTE_NAMES, PairAssessment, assess_pair
from core.guided_beginner import score_band
from core.remix_project import MidiRemixerProject
from core.remix_version import APP_NAME, APP_VERSION_DISPLAY, TAGLINE
from core.runtime_cleanup import runtime_supervisor
from core.take_quality import inspect_generated_take
from midi_library_scan.lifecycle import EligibilityState, eligibility_label


PAIR_PREFIX = "pair:"
MULTI_PREFIX = "multi:"
MAX_ACTIVE_SOURCES = 12




class StudioLibraryMixin:
    def _prepare_persistent_library_storage(self) -> None:
        """Remixer never migrates or manufactures Library-owned persistence.

        Contract 4 makes MIDI Library Manager/Engine the sole owner of catalogue
        creation and schema migration.  AppPaths.ensure() has already created the
        shared directories; Remixer must not copy legacy SQLite files into them.
        """
        return

    def _configure_library_monitoring_timers(self) -> None:
        """Remixer observes Library Engine revisions; it never watches/scans roots itself."""
        self.folder_watcher = QFileSystemWatcher(self)  # compatibility only; no 96K paths are registered
        self.recursive_library_watcher = RecursiveDirectoryWatcher(lambda _path: None)
        self.library_refresh_debounce = QTimer(self)
        self.library_refresh_debounce.setSingleShot(True)
        self.library_reconcile_timer = QTimer(self)
        self.library_reconcile_timer.setInterval(2000)
        self.library_reconcile_timer.timeout.connect(self._poll_library_engine_state)
        self.library_reconcile_timer.start()
        self._library_engine_seen_revision = -1
        self._library_engine_seen_deep_complete = -1
        self._family_reconciliation_state = "unknown"
        self._family_reconciliation_current = 0
        self._family_reconciliation_total = 0
        runtime_supervisor.register_cleanup("Library Engine poll timer", self.library_reconcile_timer.stop)

    def _poll_library_engine_state(self) -> None:
        if self._shutdown_requested:
            return
        if not bool(getattr(self.library_scan, "available", False)):
            try:
                if not self.library_scan.reconnect_existing():
                    return
                self.library_index.close()
                self.library_index = LibraryIndex(self.library_scan.library_index_path, read_only=True)
                self.library_engine_client = self.library_scan.client
                self.statusBar().showMessage("Connected to the authoritative MIDI Library Engine. Loading committed catalogue…", 5000)
                self._initialize_library()
            except (OSError, sqlite3.Error, RuntimeError, ValueError, TypeError):
                return
        try:
            status = self.library_engine_client.status(start_if_needed=False)
        except (OSError, RuntimeError, ValueError, TypeError):
            try:
                self.library_scan.reconnect_existing()
            except (OSError, RuntimeError, ValueError, TypeError) as exc:
                self._write_ui_error_log("Reconnect MIDI Library Engine", exc)
            return
        revision = int(status.get("catalogue_revision") or 0)
        deep = status.get("deep") or {}
        complete = int(deep.get("complete") or 0)
        remaining = int(deep.get("remaining") or 0)
        readiness = status.get("readiness") or {}
        previous_family_state = str(getattr(self, "_family_reconciliation_state", "unknown") or "unknown").casefold()
        family_reconciliation = status.get("family_reconciliation") or {}
        if isinstance(family_reconciliation, dict):
            self._family_reconciliation_state = str(family_reconciliation.get("state") or "unknown").casefold()
            self._family_reconciliation_current = int(family_reconciliation.get("current") or 0)
            self._family_reconciliation_total = int(family_reconciliation.get("total") or 0)
        else:
            self._family_reconciliation_state = "unknown"
            self._family_reconciliation_current = 0
            self._family_reconciliation_total = 0
        # Library v0.39.3 can reconcile persisted family IDs without changing
        # source MIDI files or the ordinary scan revision. If Remixer was already
        # attached while that one-time migration ran, its resident 96K family map
        # may therefore be stale even though catalogue_revision did not move.
        # Refresh exactly once at the incomplete -> complete boundary.
        if (
            self._family_reconciliation_state == "complete"
            and previous_family_state not in {"complete"}
            and bool(self.profiles)
            and not self._library_startup_loading
        ):
            self._library_mode_cache = {}
            self.statusBar().showMessage(
                "Library composition families reconciled · refreshing authoritative remix eligibility…", 6000
            )
            self._initialize_library()
            return
        self._library_cached_guided_ready_count = int(readiness.get("guided_ready") or 0)
        self._library_cached_assisted_ready_count = int(readiness.get("assisted_ready") or 0)
        if complete != self._library_engine_seen_deep_complete:
            previous_complete = self._library_engine_seen_deep_complete
            self._library_engine_seen_deep_complete = complete
            if self.profiles:
                self._library_cached_ready_count = min(len(self.profiles), complete)
                self._library_cached_pending_count = max(0, len(self.profiles) - self._library_cached_ready_count)
                self._library_mode_cache = {}
                self._update_library_status()
            if previous_complete >= 0 and complete > previous_complete:
                # Open-MIDI requests can be prioritized through the Engine. Poll
                # committed completion so the file is auto-added without the old
                # in-process deep-analysis callback.
                self._continue_pending_open_midi_paths()
        if revision != self._library_engine_seen_revision:
            previous = self._library_engine_seen_revision
            if previous >= 0 and not self._library_startup_loading:
                try:
                    delta = self.library_engine_client.command(
                        "catalogue_changes", timeout=3.0, start_if_needed=False, since_revision=previous
                    )
                except (OSError, RuntimeError, ValueError, TypeError):
                    delta = {"full_refresh": True, "revision": revision}
                if bool(delta.get("full_refresh")):
                    self.statusBar().showMessage(
                        "Library Engine committed a large catalogue update · refreshing saved summaries in background…"
                    )
                    self._library_engine_seen_revision = int(delta.get("revision") or revision)
                    self._initialize_library()
                else:
                    self._apply_engine_catalogue_delta(delta, status)
                    self._library_engine_seen_revision = int(delta.get("revision") or revision)
            else:
                self._library_engine_seen_revision = revision

    def _apply_engine_catalogue_delta(self, delta: dict[str, Any], status: dict[str, Any]) -> None:
        """Apply a small Engine commit without rebuilding/hydrating 96K summaries."""
        updated = [str(path) for path in delta.get("updated") or () if str(path)]
        removed = [str(path) for path in delta.get("removed") or () if str(path)]
        if not updated and not removed:
            return
        try:
            rows = self.library_scan.database.load_results(updated, validate_sources=False) if updated else []
        except (OSError, sqlite3.Error, RuntimeError, ValueError, TypeError):
            self._initialize_library()
            return
        for path in removed:
            self._quick_results.pop(path, None)
            self.profiles.pop(path, None)
            old_family = self._family_by_path.pop(path, None)
            if old_family:
                members = [item for item in self._family_members.get(old_family, ()) if item != path]
                if members:
                    self._family_members[old_family] = tuple(members)
                else:
                    self._family_members.pop(old_family, None)
        for result in rows:
            path = str(result.path)
            old_family = self._family_by_path.get(path)
            new_family = str(result.family_id or f"path:{path}")
            if old_family and old_family != new_family:
                old_members = [item for item in self._family_members.get(old_family, ()) if item != path]
                if old_members:
                    self._family_members[old_family] = tuple(old_members)
                else:
                    self._family_members.pop(old_family, None)
            self._quick_results[path] = result
            self.profiles[path] = quick_result_to_profile(result)
            self._family_by_path[path] = new_family
            members = list(self._family_members.get(new_family, ()))
            if path not in members:
                members.append(path)
            self._family_members[new_family] = tuple(members)
        catalogue = status.get("catalogue") or {}
        deep = status.get("deep") or {}
        readiness = status.get("readiness") or {}
        self._library_cached_distinct_count = int(catalogue.get("distinct_families") or len(self._family_members))
        self._library_cached_ready_count = min(len(self.profiles), int(deep.get("complete") or 0))
        self._library_cached_pending_count = max(0, len(self.profiles) - self._library_cached_ready_count)
        self._library_cached_guided_ready_count = int(readiness.get("guided_ready") or 0)
        self._library_cached_assisted_ready_count = int(readiness.get("assisted_ready") or 0)
        self._library_mode_cache = {}
        self.library_controller.invalidate_candidate_index()
        self._library_quick_scan_completed = bool(self.profiles)
        self._library_catalogue_revision = int(getattr(self, "_library_catalogue_revision", 0) or 0) + 1
        self._update_library_status()
        self.statusBar().showMessage(
            f"Library Engine committed {len(updated):,} updated and {len(removed):,} removed MIDI record(s); Remixer applied the delta without reloading the full library.",
            5000,
        )
        self._continue_pending_open_midi_paths()

    def _open_library_manager(self) -> bool:
        """Open the authoritative Library Manager without assuming co-location."""
        engine_source_root = None
        try:
            ping = self.library_engine_client.ping(timeout=0.4) or {}
            engine_source_root = ping.get("source_root")
        except (OSError, RuntimeError, ValueError, TypeError) as exc:
            self._write_ui_error_log("Discover MIDI Library Manager from Engine", exc)

        candidates = library_manager_candidates(
            self.root,
            remembered_home=self.settings_store.get("library_manager_home"),
            environment_home=os.environ.get("MIDI_LIBRARY_MANAGER_HOME"),
            engine_source_root=engine_source_root,
        )

        def launch(candidate: Path) -> bool:
            if not candidate.is_file():
                return False
            try:
                if candidate.suffix.casefold() == ".bat" and os.name == "nt":
                    os.startfile(str(candidate))
                elif candidate.suffix.casefold() == ".bat":
                    return False
                else:
                    launch_gui_python_script(candidate, cwd=candidate.parent)
                self.settings_store.set("library_manager_home", str(candidate.parent))
                self.statusBar().showMessage("Opened MIDI Library Manager.", 5000)
                return True
            except (OSError, RuntimeError, ValueError, TypeError) as exc:
                self._write_ui_error_log("Open MIDI Library Manager", exc)
                return False

        for candidate in candidates:
            if launch(candidate):
                return True

        selected, _ = QFileDialog.getOpenFileName(
            self,
            "Locate MIDI Library Manager",
            str(self.root.parent),
            "Library Manager launcher (MIDI Library Manager.pyw Launch MIDI Library Manager.bat launch.bat);;All files (*)",
        )
        if selected and launch(Path(selected)):
            return True
        QMessageBox.information(
            self, "MIDI Library Manager",
            "MIDI Library Manager could not be located. Install or locate the qualified Library Manager, then open it to create, scan, classify, or maintain the authoritative MIDI Library."
        )
        return False

    def _reference_catalog(self) -> ReferenceCatalog:
        return ReferenceCatalog(
            self.root / "data" / "known_pairings.json",
            import_directories=(self.paths.reference_imports,),
        )

    def _prioritize_reference_evidence(self, profiles: tuple[MidiProfile, ...], *, title_map=None) -> int:
        """Ask the Library Engine to import/prioritize documented relationship evidence.

        The parameters are retained for call-site compatibility; Remixer no longer
        opens or writes the Discovery Graph database itself.
        """
        if not profiles:
            return 0
        try:
            response = self.library_engine_client.command("refresh_reference_evidence", timeout=12.0, start_if_needed=False)
            graph = response.get("discovery_graph") or {}
            return int(graph.get("prioritized_paths") or 0)
        except (OSError, RuntimeError, ValueError, TypeError) as exc:
            self._write_ui_error_log("Reference evidence priority", exc)
            return 0

    @staticmethod
    def _reference_summary_text(summary: dict[str, int]) -> str:
        return (
            f"Documented human mashup relationships: {summary.get('documented_pairings', 0)}\n"
            f"Jazz relationships: {summary.get('jazz_relationships', 0)}\n"
            f"Imported candidate relationships: {summary.get('external_candidates', 0)}\n"
            f"Popularity/familiarity records: {summary.get('popularity_records', 0)}\n"
            f"Named evidence providers: {summary.get('providers', 0)}"
        )

    def show_reference_evidence_summary(self) -> None:
        catalog = self._reference_catalog()
        summary = catalog.summary()
        graph_text = ""
        try:
            status = self.library_engine_client.status(start_if_needed=False)
            graph_summary = status.get("discovery_graph") or {}
            graph_text = (
                f"\nPersistent discovery graph edges: {int(graph_summary.get('edges') or 0)}"
                f"\nDiscovery graph providers: {int(graph_summary.get('providers') or 0)}"
                f"\nPairs with local audition/save/choice feedback: {int(graph_summary.get('feedback_pairs') or 0)}"
            )
        except (OSError, RuntimeError, ValueError, TypeError) as exc:
            self._write_ui_error_log("Discovery graph summary", exc)
        import_folder = self.paths.reference_imports
        QMessageBox.information(
            self,
            "Reference Evidence",
            self._reference_summary_text(summary) + graph_text
            + "\n\nReference data helps discover and explain candidates. It never replaces the "
              "key, tempo, meter, structure, instrumentation, and role analysis of the actual MIDI files."
            + f"\n\nOptional authorized CSV/JSON imports are stored in:\n{import_folder}",
        )

    def import_reference_evidence(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "Import Reference Evidence",
            str(Path.home()),
            "Reference evidence (*.json *.csv *.xlsx *.xlsm);;JSON files (*.json);;CSV files (*.csv);;Excel workbooks (*.xlsx *.xlsm)",
        )
        if not selected:
            return
        source = Path(selected)
        before = self._reference_catalog().summary()
        import_folder = self.paths.reference_imports
        try:
            import_folder.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            if source.suffix.casefold() in {".csv", ".xlsx", ".xlsm"}:
                from tools.import_reference_evidence import normalize_rows, read_rows
                normalized = normalize_rows(read_rows(source), provider=source.stem)
                import_summary = normalized.get("import_summary", {})
                if not (import_summary.get("candidates") or import_summary.get("popularity")):
                    raise ValueError("The workbook has no supported candidate or popularity rows.")
                destination = import_folder / f"{source.stem}-normalized-{stamp}.json"
                atomic_write_json(destination, normalized)
            else:
                destination = import_folder / source.name
                if destination.exists():
                    destination = import_folder / f"{source.stem}-{stamp}{source.suffix.lower()}"
                shutil.copy2(source, destination)
            updated_catalog = self._reference_catalog()
            after = updated_catalog.summary()
            self.library_engine_client.command("refresh_reference_evidence", timeout=12.0, start_if_needed=False)
        except (OSError, RuntimeError, ValueError, TypeError, ImportError) as exc:
            self._write_ui_error_log("Import reference evidence", exc)
            QMessageBox.critical(
                self,
                "Reference Import Failed",
                "The selected reference file could not be imported. MIDI Remixer and the song library were not changed.",
            )
            return

        changed = sum(max(0, after.get(key, 0) - before.get(key, 0)) for key in (
            "documented_pairings", "jazz_relationships", "external_candidates", "popularity_records"
        ))
        if changed <= 0:
            try:
                destination.unlink(missing_ok=True)
            except OSError as exc:
                self._write_ui_error_log("Remove unusable reference import", exc)
            QMessageBox.warning(
                self,
                "No Usable Reference Records",
                "The file was readable, but it did not contain new records in the supported schema. "
                "See data/reference_imports/README.md and the sample CSV for field names.",
            )
            return

        self.statusBar().showMessage(f"Imported {changed} reference record(s) from {source.name}.", 7000)
        QMessageBox.information(
            self,
            "Reference Evidence Imported",
            f"Imported {changed} new metadata record(s).\n\n"
            + self._reference_summary_text(after)
            + "\n\nRecommendations will use this only as discovery/explanation evidence after the actual MIDI files pass musical checks.",
        )

    # ---------- library ----------
    def _initialize_library(self) -> None:
        """Hydrate compact catalogue summaries without decoding deep profiles.

        v0.27 treats the 96k quick catalogue as the always-resident search index.
        Heavy detailed profiles stay in LibraryIndex's bounded LRU until a source
        or bounded recommendation candidate actually needs one.
        """
        # Returning-user attachment is driven by committed catalogue state, not
        # by whether every configured source drive happens to be mounted today.
        # Contract 4 explicitly protects unavailable roots from being interpreted
        # as deletions, so Remixer can safely reopen the persistent catalogue.
        configured = self._normalised_library_paths()

        thread = getattr(self, "_library_load_thread", None)
        if thread is not None and thread.is_alive():
            return

        self._library_startup_loading = True
        self._library_quick_scan_completed = False
        self._library_policy_decision_pending = False
        self._library_startup_started_at = time.perf_counter()
        self._library_startup_phase = "Opening persistent catalogue…"
        self.library_status.setText("Loading saved MIDI library…")
        self._set_library_scan_panel_visible(True)
        self._set_library_scan_panel_text("MIDI Remixer is loading your saved library")
        self._set_library_scan_detail(
            "Reopening the persistent catalogue and search indexes. Your MIDI files are NOT being rescanned."
        )
        self._set_library_scan_panel_mode("startup")
        self._set_library_scan_progress(0, 0, "Loading saved library…")
        self.statusBar().showMessage(
            "Loading the saved 96K-ready catalogue in the background — no rescan is running."
        )
        self._start_library_startup_heartbeat()
        self._on_library_onboarding_state_changed()
        startup_started = self._library_startup_started_at

        def load_saved_catalogue() -> None:
            try:
                # v0.27 migration derives stable family IDs from stored metadata;
                # it never reopens source MIDIs and runs off the Qt thread.
                self._library_startup_phase = "Opening committed Library Engine results…"
                self._library_startup_phase = "Loading saved catalogue summaries…"
                compact_profile_loader = getattr(self.library_scan.database, "load_compact_profiles", None)
                if callable(compact_profile_loader):
                    combined = compact_profile_loader(configured or None)
                    # Rich QuickScanResult rows are loaded lazily for the handful of
                    # selected/deep sources that need catalogue enrichment.
                    quick_by_path = {}
                else:
                    quick_results = self.library_scan.cached_results(configured or None, validate_sources=False)
                    quick_by_path = {result.path: result for result in quick_results}
                    hydrated_profiles = quick_results_to_profiles(quick_results)
                    combined = {profile.path: profile for profile in hydrated_profiles}
                counts = self.library_scan.database.deep_status_counts()
                catalogue_counts = self.library_scan.database.catalogue_counts()
                family_by_path = self.library_scan.database.family_index()
                family_members: dict[str, list[str]] = {}
                for index, (path, family_id) in enumerate(family_by_path.items(), start=1):
                    family_members.setdefault(family_id, []).append(path)
                    if index % 4096 == 0:
                        time.sleep(0.001)
                family_members_frozen = {key: tuple(values) for key, values in family_members.items()}
                # Library Engine owns migrations and readiness writes. Remixer only
                # reads the committed compact readiness table.
                self._library_startup_phase = "Loading detailed-analysis readiness…"
                readiness_backfilled = 0
                readiness_summary = self.library_index.readiness_summary()
                deep_anchor_paths = self.library_index.assisted_ready_paths(limit=512)
                profile_tuple = tuple(combined.values())
                if len(profile_tuple) > 5000:
                    mode_cache = build_large_library_mode_cache(
                        profile_tuple,
                        distinct_songs=int(catalogue_counts.get("distinct_families", 0) or len(family_members_frozen)),
                        readiness_summary=readiness_summary,
                        deep_anchor_paths=deep_anchor_paths,
                    )
                else:
                    # Small libraries can afford the exact full diagnostic path.
                    deep_map = self.load_deep_profiles(list(combined))
                    mode_cache = build_library_mode_cache(deep_map.values())
                # Full-library compatibility indexing, discovery evidence, and
                # relationship preparation are owned by MIDI Library Engine.
                # Remixer hydrates only compact UI summaries here; it must not
                # duplicate six-figure Engine work during creative startup.
                self._library_startup_phase = "Finishing saved-library attachment…"
                payload = {
                    "configured": configured,
                    "quick_results": quick_by_path,
                    "profiles": combined,
                    "ready": int(counts.get("complete", 0)),
                    "pending": max(0, len(combined) - int(counts.get("complete", 0))),
                    "distinct": int(catalogue_counts.get("distinct_families", 0) or len(family_members_frozen)),
                    "mode_cache": mode_cache,
                    "readiness_summary": readiness_summary,
                    "family_by_path": family_by_path,
                    "family_members": family_members_frozen,
                    "readiness_backfilled": readiness_backfilled,
                    "startup_seconds": time.perf_counter() - startup_started,
                }
            except (OSError, sqlite3.Error, json.JSONDecodeError, TypeError, ValueError, RuntimeError) as exc:
                self.library_scan_bridge.startupFailed.emit(f"{type(exc).__name__}: {exc}")
            else:
                self.library_scan_bridge.startupComplete.emit(payload)

        self._library_load_thread = threading.Thread(
            target=load_saved_catalogue,
            name="MidiLibraryStartupLoad",
            daemon=False,
        )
        self._library_load_thread.start()

    def _finish_library_startup_load(self, payload: object) -> None:
        try:
            if self._shutdown_requested:
                return
            data = dict(payload) if isinstance(payload, dict) else {}
            quick_map = data.get("quick_results", {})
            profiles = data.get("profiles", {})
            self._quick_results = quick_map if isinstance(quick_map, dict) else {}
            self.profiles = profiles if isinstance(profiles, dict) else {}
            self._family_by_path = data.get("family_by_path", {}) if isinstance(data.get("family_by_path", {}), dict) else {}
            self._family_members = data.get("family_members", {}) if isinstance(data.get("family_members", {}), dict) else {}
            # Compatibility indexing is retained exclusively by Library Engine.
            self.library_controller.invalidate_candidate_index()
            self._library_quick_scan_completed = bool(self.profiles)
            self._library_policy_decision_pending = False
            self._library_startup_loading = False
            self._stop_library_startup_heartbeat()
            self._library_cached_ready_count = min(len(self.profiles), int(data.get("ready", 0) or 0))
            self._library_cached_pending_count = max(0, len(self.profiles) - self._library_cached_ready_count)
            readiness_summary = data.get("readiness_summary", {}) if isinstance(data.get("readiness_summary", {}), dict) else {}
            self._library_cached_guided_ready_count = int(readiness_summary.get("guided_ready") or 0)
            self._library_cached_assisted_ready_count = int(readiness_summary.get("assisted_ready") or 0)
            self._library_cached_distinct_count = int(data.get("distinct", 0) or 0)
            if self.profiles:
                self._library_catalogue_revision = max(1, int(getattr(self, "_library_catalogue_revision", 0) or 0))
            mode_cache = data.get("mode_cache", {})
            self._library_mode_cache = mode_cache if isinstance(mode_cache, dict) else {}
            record_performance(
                self.paths.logs / "performance.log", "library_startup",
                seconds=float(data.get("startup_seconds", 0.0) or 0.0),
                catalogue_count=len(self.profiles), detailed_ready=self._library_cached_ready_count,
                readiness_backfilled=int(data.get("readiness_backfilled", 0) or 0),
            )

            if self.profiles:
                self._update_library_status()
                self._configure_folder_watcher()
                elapsed = float(data.get("startup_seconds", 0.0) or 0.0)
                self._set_library_scan_panel_visible(True)
                self._set_library_scan_panel_text("Saved MIDI library is ready")
                self._set_library_scan_detail(
                    f"Loaded {len(self.profiles):,} catalogue summaries in {elapsed:.1f}s. "
                    f"{self._library_cached_ready_count:,} detailed profiles are available. No rescan was performed."
                )
                self._set_library_scan_progress(1, 1, "Library ready")
                self._set_library_scan_panel_mode("complete")
                schedule_owned(self, "hide-startup-library-panel", 3200, self._hide_library_scan_panel)
                self.statusBar().showMessage(
                    f"Loaded {len(self.profiles)} saved MIDI summaries · "
                    f"{self._library_cached_ready_count} detailed profiles available lazily · "
                    f"{self._library_cached_pending_count} pending. No rescan was performed."
                )
            else:
                self._update_library_status()
                self.library_status.setText("No committed Library catalogue")
                self.statusBar().showMessage(
                    "No committed MIDI Library catalogue is available. Open MIDI Library Manager to create or restore the authoritative Library."
                )
            self._update_workflow_state()
            self._on_library_onboarding_state_changed()
        finally:
            self._library_load_thread = None

    def _start_library_startup_heartbeat(self) -> None:
        timer = getattr(self, "_library_startup_heartbeat_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setInterval(750)
            timer.timeout.connect(self._update_library_startup_heartbeat)
            self._library_startup_heartbeat_timer = timer
        timer.start()

    def _stop_library_startup_heartbeat(self) -> None:
        timer = getattr(self, "_library_startup_heartbeat_timer", None)
        if timer is not None:
            timer.stop()

    def _update_library_startup_heartbeat(self) -> None:
        if not getattr(self, "_library_startup_loading", False):
            self._stop_library_startup_heartbeat()
            return
        started = float(getattr(self, "_library_startup_started_at", time.perf_counter()) or time.perf_counter())
        elapsed = max(0, int(time.perf_counter() - started))
        self._set_library_scan_panel_visible(True)
        self._set_library_scan_panel_text("MIDI Remixer is loading your saved library")
        phase = str(getattr(self, "_library_startup_phase", "Reopening persistent catalogue/search indexes…") or "Loading saved library…")
        self._set_library_scan_detail(
            f"{phase} · working for {elapsed}s · NO MIDI rescan."
        )
        self._set_library_scan_progress(0, 0, f"Loading… {elapsed}s")
        self.statusBar().showMessage(
            f"Loading saved MIDI library · {elapsed}s elapsed · no source MIDI rescan"
        )

    def load_deep_profiles(self, paths: list[str] | tuple[str, ...] | set[str]) -> dict[str, MidiProfile]:
        """Load only requested heavy profiles; everything else stays in SQLite."""
        values = [str(Path(path).resolve(strict=False)) for path in paths if path]
        deep = self.library_index.load_many(values, validate_sources=False)
        result: dict[str, MidiProfile] = {}
        for path in values:
            profile = deep.get(path)
            if profile is not None:
                quick_result = self._quick_results.get(path)
                if quick_result is None:
                    try:
                        quick_result = self.library_scan.database.load_result(path, validate_source=False)
                    except (OSError, sqlite3.Error, RuntimeError, ValueError, TypeError):
                        quick_result = None
                result[path] = enrich_deep_profile(profile, quick_result)
            elif path in self.profiles:
                result[path] = self.profiles[path]
        return result

    def deep_profile_for_path(self, path: str) -> MidiProfile | None:
        resolved = str(Path(path).resolve(strict=False))
        profile = self.library_index.load_one(resolved, validate_source=False)
        if profile is not None:
            quick_result = self._quick_results.get(resolved)
            if quick_result is None:
                try:
                    quick_result = self.library_scan.database.load_result(resolved, validate_source=False)
                except (OSError, sqlite3.Error, RuntimeError, ValueError, TypeError):
                    quick_result = None
            return enrich_deep_profile(profile, quick_result)
        return self.profiles.get(resolved)

    def _fail_library_startup_load(self, detail: object) -> None:
        self._library_load_thread = None
        self._library_startup_loading = False
        self._stop_library_startup_heartbeat()
        self._library_quick_scan_completed = False
        self._library_policy_decision_pending = False
        if self._shutdown_requested:
            return
        self.library_status.setText("Saved library could not be loaded")
        self._set_library_scan_panel_visible(True)
        self._set_library_scan_panel_text("Saved MIDI library could not be loaded")
        self._set_library_scan_detail(
            "No source MIDI files were changed. Open Help → Runtime Diagnostics for the startup error details."
        )
        self._set_library_scan_panel_mode("complete")
        self.statusBar().showMessage("Saved MIDI catalogue could not be loaded. No source files were changed.")
        append_bounded(
            self.paths.logs / "ui-errors.log",
            f"Startup library hydration failed: {detail}\n",
        )
        self._update_workflow_state()
        self._on_library_onboarding_state_changed()

    def _on_library_onboarding_state_changed(self) -> None:
        """Hook for alternate main-window layouts to update setup/mode gating."""
        return


    def authoritative_family_identity_ready(self) -> tuple[bool, str]:
        """Return whether Contract-4 family reconciliation is safe for remix discovery.

        Library v0.39.3 owns durable composition/version-family truth. Guided and
        Version Fusion must never consume prepared relationships while that truth
        is still being reconciled, because old family IDs/relationship rows may
        otherwise leak same-song versions into Mash or hide useful Fusion pairs.
        """
        if not bool(getattr(self.library_scan, "available", False)):
            return False, "MIDI Library Engine is offline."
        try:
            status = self.library_engine_client.status(start_if_needed=False)
        except (OSError, RuntimeError, ValueError, TypeError):
            return False, "MIDI Library Engine status is unavailable."
        ready, detail, state, current, total = family_reconciliation_readiness(status)
        self._family_reconciliation_state = state
        self._family_reconciliation_current = current
        self._family_reconciliation_total = total
        return ready, detail

    def require_authoritative_family_identity(self, parent: QWidget, workflow_name: str) -> bool:
        ready, detail = self.authoritative_family_identity_ready()
        if ready:
            return True
        QMessageBox.information(
            parent, f"{workflow_name} is waiting for MIDI Library Manager",
            detail + "\n\nGuided Mash and Version Fusion wait for the Library's authoritative family classification so same-song versions cannot be mistaken for different songs. No rescan is started by MIDI Remixer.",
        )
        return False

    def guided_library_summary_cached(self) -> dict[str, int]:
        """Return Contract-4 Mash Ready counts; Remixer does not reclassify Library truth."""
        ready_paths = self._authoritative_eligible_paths(EligibilityState.MASH_READY)
        resident = ready_paths.intersection(self.profiles)
        family_lookup = getattr(self, "_family_by_path", {}) or {}
        ready_families = {str(family_lookup.get(path) or f"path:{path}") for path in resident}
        all_families = {str(family_lookup.get(path) or f"path:{path}") for path in self.profiles}
        distinct = len(all_families)
        ready = len(resident)
        return {
            "total": len(self.profiles), "distinct_songs": distinct,
            "duplicate_variants": max(0, len(self.profiles) - distinct),
            "guided_ready": ready, "guided_ready_songs": len(ready_families),
            "restricted": 0, "hidden": max(0, len(self.profiles) - ready),
        }

    def guided_library_diagnostics_cached(self) -> dict[str, object]:
        cache = getattr(self, "_library_mode_cache", {}) or {}
        diagnostics = cache.get("guided_diagnostics", {}) if isinstance(cache, dict) else {}
        if isinstance(diagnostics, dict) and int(diagnostics.get("total_files", -1)) == len(self.profiles):
            return dict(diagnostics)
        summary = self.guided_library_summary_cached()
        return {
            "total_files": summary["total"], "distinct_songs": summary["distinct_songs"],
            "duplicate_variants": summary["duplicate_variants"],
            "guided_ready_files": summary["guided_ready"], "guided_ready_songs": summary["guided_ready_songs"],
            "hidden_files": summary["hidden"], "exclusions": {}, "sample_rows": (),
        }

    def assisted_library_summary_cached(self) -> dict[str, int]:
        """Assisted cross-song discovery is bounded by persisted Mash Ready rows."""
        ready_paths = self._authoritative_eligible_paths(EligibilityState.MASH_READY)
        ready = len(ready_paths.intersection(self.profiles))
        return {
            "total": len(self.profiles), "eligible": ready, "studio_ready": ready,
            "adaptable": 0, "whole_section": 0, "hidden": max(0, len(self.profiles) - ready),
        }

    def assisted_anchor_paths_cached(self) -> tuple[str, ...]:
        ready = self._authoritative_eligible_paths(EligibilityState.MASH_READY)
        cache = getattr(self, "_library_mode_cache", {}) or {}
        values = cache.get("assisted_anchor_paths", ()) if isinstance(cache, dict) else ()
        bounded = [str(path) for path in values if str(path) in self.profiles and str(path) in ready]
        if bounded:
            return tuple(bounded[:512])
        # Small/legacy readiness cache fallback remains bounded and eligibility-first.
        return tuple(sorted((path for path in ready if path in self.profiles), key=str.casefold)[:512])

    def _normalised_library_paths(self) -> list[str]:
        """Return Engine-owned library roots without sharing writable UI settings."""
        values = []
        try:
            status = self.library_engine_client.status(start_if_needed=False)
            values = list(status.get("roots") or [])
        except (OSError, RuntimeError, ValueError, TypeError):
            # Compatibility fallback for a temporarily unavailable Engine. The
            # legacy Remixer setting is read-only here and is never authoritative
            # once the Engine is reachable.
            raw = self.settings_store.get("library_paths", [])
            if isinstance(raw, (str, Path)):
                values = [raw]
            elif isinstance(raw, (list, tuple, set)):
                values = list(raw)
        paths: list[str] = []
        seen: set[str] = set()
        for value in values:
            try:
                text = str(Path(value).expanduser().resolve(strict=False)).strip()
            except (OSError, TypeError, ValueError):
                continue
            key = text.casefold()
            if text and key not in seen:
                seen.add(key)
                paths.append(text)
        return paths

    def _initial_midi_picker_directory(self) -> str:
        candidates = self._normalised_library_paths()
        last = self.settings_store.get("last_midi_directory")
        if isinstance(last, (str, Path)) and str(last).strip():
            candidates.insert(0, str(last))
        for candidate in candidates:
            try:
                path = Path(candidate).expanduser()
                if path.is_file():
                    path = path.parent
                if path.is_dir():
                    return str(path)
            except (OSError, TypeError, ValueError):
                continue
        return str(Path.home())

    def _choose_midi_files_dialog(self, parent: QWidget | None = None) -> list[str]:
        """Show a multi-file picker with the same native-to-Qt fallback."""
        dialog_parent = parent.window() if isinstance(parent, QWidget) else self
        start = self._initial_midi_picker_directory()
        errors: list[BaseException] = []
        for use_qt_dialog in (False, True):
            dialog: QFileDialog | None = None
            try:
                dialog = QFileDialog(dialog_parent, "Open MIDI Files for Audition", start, "MIDI files (*.mid *.midi *.kar)")
                dialog.setFileMode(QFileDialog.FileMode.ExistingFiles)
                dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptOpen)
                dialog.setOption(QFileDialog.Option.DontUseNativeDialog, use_qt_dialog)
                if not dialog.exec():
                    return []
                return [str(Path(path).expanduser()) for path in dialog.selectedFiles()]
            except Exception as exc:
                errors.append(exc)
                self._write_ui_error_log(
                    "Open MIDI Files for Audition (Qt fallback)" if use_qt_dialog else "Open MIDI Files for Audition (native)", exc
                )
            finally:
                if dialog is not None:
                    try:
                        dialog.deleteLater()
                    except RuntimeError as exc:
                        self._write_ui_error_log("Dispose MIDI file dialog", exc)
        detail = f"{type(errors[-1]).__name__}: {errors[-1]}" if errors else "Unknown file-picker error"
        QMessageBox.critical(
            dialog_parent,
            "MIDI Files Could Not Be Browsed",
            "MIDI Remixer could not open the file browser. No library data or MIDI files were changed.\n\n"
            f"Technical detail: {detail}\n\nA diagnostic was saved to logs/ui-errors.log.",
        )
        return []

    def open_or_setup_library(self, _checked: bool = False) -> None:
        """Library roots and scans are owned by MIDI Library Manager."""
        self._open_library_manager()

    def show_library_setup_prompt(self, _checked: bool = False, first_run: bool = False) -> None:
        message = QMessageBox(self)
        message.setWindowTitle("MIDI Library Required")
        message.setIcon(QMessageBox.Icon.Information)
        message.setText("MIDI Remixer uses the authoritative MIDI Library Manager catalogue.")
        message.setInformativeText(
            "Create, scan, classify, repair, or change Library folders in MIDI Library Manager. "
            "MIDI Remixer only reads committed Contract-4 Library results and will reconnect automatically."
        )
        open_manager = message.addButton("Open MIDI Library Manager…", QMessageBox.ButtonRole.AcceptRole)
        later = message.addButton("Do This Later", QMessageBox.ButtonRole.RejectRole)
        message.exec()
        if message.clickedButton() is open_manager:
            self._open_library_manager()
        elif message.clickedButton() is later and first_run:
            self.settings_store.update_many({"library_setup_completed": False, "library_setup_prompt_dismissed": True})
            self.statusBar().showMessage("Library setup postponed. Open MIDI Library Manager when you are ready.")

    def _choose_library_folder(self, replace: bool = False, *, progress_parent=None, status_callback=None) -> bool:
        """Compatibility entry point: folder ownership moved to Library Manager."""
        if status_callback is not None:
            status_callback(0, 0, "Opening MIDI Library Manager…")
        return self._open_library_manager()

    def add_files(self, *, progress_parent=None, status_callback=None) -> bool:
        """Compatibility entry point: Library Manager owns file admission/scanning."""
        if status_callback is not None:
            status_callback(0, 0, "Opening MIDI Library Manager…")
        return self._open_library_manager()

    def _library_path_admission(self, path: str | Path, *, mode: str | None = None) -> tuple[bool, str]:
        """Fail closed against the Library's persisted lifecycle/eligibility truth."""
        resolved = str(Path(path).expanduser().resolve(strict=False))
        try:
            state = self.library_scan.database.lifecycle_for_path(resolved)
        except (OSError, sqlite3.Error, RuntimeError, ValueError, TypeError):
            return False, "Library lifecycle state is unavailable"
        if not state:
            return False, "not present in the authoritative Library"
        if str(state.get("lifecycle_state") or "") != "blessed":
            value = str(state.get("eligibility_state") or EligibilityState.UNRESOLVED.value)
            return False, eligibility_label(value)
        eligibility = str(state.get("eligibility_state") or EligibilityState.UNRESOLVED.value)
        active_mode = str(mode or getattr(self, "guidance_mode", "guided") or "guided").casefold()
        allowed = {EligibilityState.MASH_READY.value}
        if active_mode == "fusion":
            allowed.add(EligibilityState.FUSION_CANDIDATE.value)
        elif active_mode in {"workbench", "custom", "advanced"}:
            allowed.update({EligibilityState.FUSION_CANDIDATE.value, EligibilityState.USABLE_NOT_MASH.value})
        if eligibility not in allowed:
            return False, eligibility_label(eligibility)
        return True, eligibility_label(eligibility)

    def _authoritative_eligible_paths(self, eligibility: EligibilityState) -> set[str]:
        try:
            return set(self.library_scan.database.blessed_paths(eligibility.value))
        except (OSError, sqlite3.Error, RuntimeError, ValueError, TypeError):
            return set()

    def open_midi_files_into_mash(self) -> None:
        """Audition selected files, but admit only authoritative Library-approved rows."""
        try:
            paths = self._choose_midi_files_dialog(self)
            if not paths:
                return
            resolved = [str(Path(path).resolve(strict=False)) for path in paths]
            self.settings_store.set("last_midi_directory", str(Path(resolved[0]).parent))
            first = resolved[0]
            if Path(first).is_file():
                self._play_path(first, f"Opened MIDI: {Path(first).stem}", f"open-{Path(first).stem}")

            accepted: list[str] = []
            rejected: list[tuple[str, str]] = []
            for path in resolved:
                allowed, reason = self._library_path_admission(path)
                if allowed:
                    accepted.append(path)
                else:
                    rejected.append((path, reason))
            self._pending_open_midi_paths.update(accepted)
            self._continue_pending_open_midi_paths()
            if rejected:
                examples = "\n".join(f"• {Path(path).name}: {reason}" for path, reason in rejected[:5])
                more = f"\n…and {len(rejected)-5} more" if len(rejected) > 5 else ""
                QMessageBox.information(
                    self,
                    "Library Approval Required",
                    "MIDI Remixer did not add the following file(s) because the authoritative Library does not currently approve them for this remix mode:\n\n"
                    + examples + more
                    + "\n\nUse MIDI Library Manager to scan/review the files or choose a different Library-approved source. Raw audition did not change the Library.",
                )
        except (OSError, RuntimeError, ValueError, TypeError, sqlite3.Error) as exc:
            self._write_ui_error_log("Open Library-approved MIDI files", exc)
            QMessageBox.warning(
                self, "Open MIDI File",
                "The selected MIDI could not be opened into the mash. The source file and authoritative Library were not changed. Details were saved to logs/ui-errors.log.",
            )

    def _continue_pending_open_midi_paths(self) -> None:
        pending = list(getattr(self, "_pending_open_midi_paths", set()))
        if not pending:
            return
        ready: list[str] = []
        waiting: list[str] = []
        refused: list[str] = []
        for path in pending:
            allowed, _reason = self._library_path_admission(path)
            if not allowed:
                refused.append(path)
                continue
            if path not in self.profiles:
                waiting.append(path)
                continue
            profile = self.deep_profile_for_path(path)
            if profile is not None and not is_provisional_profile(profile):
                ready.append(path)
            else:
                waiting.append(path)
                self.analyze_library_song_on_demand(path)
        for path in refused:
            self._pending_open_midi_paths.discard(path)
        if ready:
            for path in ready:
                self._pending_open_midi_paths.discard(path)
            self._add_source_paths(ready, replace=False)
            self.statusBar().showMessage(
                f"Opened {len(ready)} Library-approved MIDI file{'s' if len(ready) != 1 else ''} into the current mash."
            )
        if waiting:
            names = ", ".join(Path(path).name for path in waiting[:2])
            suffix = f" and {len(waiting) - 2} more" if len(waiting) > 2 else ""
            self.statusBar().showMessage(
                f"Waiting for Library detailed analysis of {names}{suffix}; Remixer will add only after the committed eligibility remains valid."
            )

    def review_suspicious_midi_files(self) -> None:
        self.statusBar().showMessage("Opening MIDI Library Manager for health review…")
        self._open_library_manager()

    def refresh_library(self) -> None:
        """Refresh the read-only committed view; never trigger a Library scan."""
        self.statusBar().showMessage("Refreshing committed Library view…")
        try:
            self._poll_library_engine_state()
            self._initialize_library()
        except (OSError, RuntimeError, ValueError, TypeError, sqlite3.Error) as exc:
            self._write_ui_error_log("Refresh committed Library view", exc)
            QMessageBox.warning(self, "Refresh Committed Library View", f"{type(exc).__name__}: {exc}")

    def _set_library_scan_panel_text(self, text: str) -> None:
        label = getattr(self, "library_scan_panel_label", None)
        if label is not None:
            try:
                label.setText(text)
            except RuntimeError as exc:
                self._write_ui_error_log("Closed library scan panel label", exc)

    def _set_library_scan_panel_visible(self, visible: bool) -> None:
        panel = getattr(self, "library_scan_panel", None)
        if panel is not None:
            try:
                panel.setVisible(bool(visible))
            except RuntimeError as exc:
                self._write_ui_error_log("Closed library scan panel", exc)

    def _set_library_scan_cancel_enabled(self, enabled: bool) -> None:
        button = getattr(self, "library_scan_cancel_button", None)
        if button is not None:
            try:
                button.setEnabled(bool(enabled))
            except RuntimeError as exc:
                self._write_ui_error_log("Closed library scan cancel button", exc)

    def _hide_library_scan_panel(self) -> None:
        if not self._shutdown_requested:
            self._set_library_scan_panel_visible(False)

    def _set_library_scan_detail(self, text: str) -> None:
        label = getattr(self, "library_scan_detail_label", None)
        if label is not None:
            try:
                label.setText(text)
            except RuntimeError as exc:
                self._write_ui_error_log("Closed library scan detail label", exc)

    def _set_library_scan_progress(self, current: int, total: int, text: str) -> None:
        progress = getattr(self, "library_scan_progress", None)
        if progress is None:
            return
        try:
            if total > 0:
                progress.setRange(0, max(1, total))
                progress.setValue(max(0, min(current, total)))
            else:
                progress.setRange(0, 0)
            progress.setFormat(text)
        except RuntimeError as exc:
            self._write_ui_error_log("Closed library scan progress bar", exc)


    def _set_library_scan_panel_mode(self, mode: str) -> None:
        self._library_scan_panel_mode = str(mode)
        progress = getattr(self, "library_scan_progress", None)
        if progress is not None:
            try:
                progress.setVisible(mode != "hidden")
            except RuntimeError as exc:
                self._write_ui_error_log("Closed Library status progress", exc)



    def deep_analysis_progress(self) -> dict[str, int | str]:
        """Public UI snapshot used by the main screen and temporary wizards."""
        try:
            return self.library_scan.deep_progress()
        except Exception as exc:
            self._write_ui_error_log("Read detailed-analysis progress", exc)
            return {
                "total": 0, "pending": 0, "paused": 0, "analyzing": 0,
                "complete": 0, "failed": 0, "finished": 0, "remaining": 0,
                "policy": "later",
            }

    def _update_deep_analysis_panel(self, current_path: str = "", state: str = "") -> None:
        snapshot = self.deep_analysis_progress()
        total = int(snapshot.get("total", 0) or 0)
        complete_count = int(snapshot.get("complete", 0) or 0)
        failed = int(snapshot.get("failed", 0) or 0)
        finished = int(snapshot.get("finished", complete_count + failed) or 0)
        remaining = int(snapshot.get("remaining", 0) or 0)
        policy = str(snapshot.get("policy", "later"))
        name = Path(current_path).name if current_path else ""

        self._set_library_scan_panel_visible(True)
        if total <= 0:
            self._set_library_scan_panel_text("No MIDI files are waiting for detailed analysis")
            self._set_library_scan_detail("The committed Library catalogue remains available.")
            self._set_library_scan_progress(0, 1, "Nothing pending")
            self._set_library_scan_panel_mode("complete")
            return

        if remaining == 0:
            self._set_library_scan_panel_text("Detailed musical analysis complete")
            detail = f"{complete_count} of {total} files analyzed in detail."
            if failed:
                detail += f" {failed} file{'s' if failed != 1 else ''} need attention and can be retried later."
            self._set_library_scan_detail(detail)
            self._set_library_scan_progress(total, total, "Detailed analysis complete")
            self._set_library_scan_panel_mode("complete")
            self.statusBar().showMessage("Detailed MIDI library analysis complete.", 6000)
            schedule_owned(self, "hide-library-panel", 6500, self._hide_library_scan_panel)
            return

        if policy == "later":
            self._set_library_scan_panel_text("Detailed musical analysis paused")
            self._set_library_scan_detail(
                f"{finished} of {total} finished. {remaining} remain. Fast-scan results are still available for remixing."
            )
            self._set_library_scan_progress(finished, total, f"Paused · {finished} / {total}")
            self._set_library_scan_panel_mode("deep_paused")
            return

        self._set_library_scan_panel_text(f"Detailed analysis is running in the background: {finished} of {total}")
        detail = "Analyzing song structure, musical roles, harmony, and remix readiness."
        if name:
            verb = "Analyzing" if state == "analyzing" else "Finished" if state == "complete" else "Checking"
            detail = f"{verb}: {name}"
        if failed:
            detail += f" · {failed} currently need attention"
        self._set_library_scan_detail(detail)
        self._set_library_scan_progress(finished, total, f"{finished} / {total} analyzed")
        self._set_library_scan_panel_mode("deep_running")


    def _deep_analyze_library_path(self, path: Path) -> None:
        """Compatibility shim: priority analysis is owned by Library Engine."""
        self.library_scan.analyze_selected(path)

    def _stop_deep_analysis_subprocess(self) -> None:
        # Remixer never owns the Library Engine process. Pausing is explicit and
        # does not terminate the engine or discard its committed work.
        self._deep_analysis_process = None

    def _on_deep_analysis_state(self, path: str, state: str) -> None:
        if self._shutdown_requested:
            return
        resolved = str(Path(path).resolve(strict=False))
        if resolved in self._retired_library_paths:
            return
        name = Path(path).name
        if state == "complete":
            # v0.27 deliberately does NOT inflate the 96k resident quick-summary
            # map with a heavy MidiProfile. LibraryIndex keeps a bounded LRU and
            # selected/search candidates load detailed profiles on demand.
            self.library_index.invalidate(resolved)
            if resolved not in self._deep_completed_session:
                self._deep_completed_session.add(resolved)
                self._library_cached_ready_count = min(
                    len(self.profiles), int(getattr(self, "_library_cached_ready_count", 0) or 0) + 1
                )
                self._library_cached_pending_count = max(0, len(self.profiles) - self._library_cached_ready_count)
                readiness = self.library_index.readiness_for_path(resolved)
                if readiness and readiness.get("guided_eligible"):
                    cache = getattr(self, "_library_mode_cache", {}) or {}
                    summary = cache.get("guided_summary") if isinstance(cache, dict) else None
                    diagnostics = cache.get("guided_diagnostics") if isinstance(cache, dict) else None
                    if isinstance(summary, dict):
                        summary["guided_ready"] = min(len(self.profiles), int(summary.get("guided_ready", 0) or 0) + 1)
                        summary["guided_ready_songs"] = min(
                            int(summary.get("distinct_songs", len(self.profiles)) or len(self.profiles)),
                            int(summary.get("guided_ready_songs", 0) or 0) + 1,
                        )
                        summary["hidden"] = max(0, len(self.profiles) - int(summary["guided_ready"]))
                    if isinstance(diagnostics, dict):
                        diagnostics["guided_ready_files"] = int(summary.get("guided_ready", 0) if isinstance(summary, dict) else 0)
                        diagnostics["guided_ready_songs"] = int(summary.get("guided_ready_songs", 0) if isinstance(summary, dict) else 0)
                        diagnostics["hidden_files"] = max(0, len(self.profiles) - int(diagnostics["guided_ready_files"]))
            self._update_library_status()
            self.statusBar().showMessage(f"Detailed analysis complete: {name}")
            if resolved in getattr(self, "_pending_open_midi_paths", set()):
                self._continue_pending_open_midi_paths()
        elif state == "failed":
            self._update_library_status()
            self.statusBar().showMessage(f"Detailed analysis needs attention: {name}")
        else:
            self.library_status.setText(f"{len(self.profiles)} MIDI files · analyzing {name}")
            self.statusBar().showMessage(f"Detailed musical analysis: {name}")
        self._update_deep_analysis_panel(path, state)

    def analyze_library_song_on_demand(self, path: str) -> None:
        profile = self.profiles.get(path)
        if profile is None or not is_provisional_profile(profile):
            return
        self.library_scan.analyze_selected(path)
        self._update_deep_analysis_panel(path, "analyzing")









    def _classify_paths(
        self, paths, replace: bool = False, retire_existing: bool = False, progress_parent=None,
        status_callback=None, ask_policy: bool = False,
    ) -> None:
        """Compatibility entry point: Remixer never scans or classifies Library sources."""
        if status_callback is not None:
            status_callback(0, 0, "Open MIDI Library Manager to add or rescan sources…")
        self._open_library_manager()




    def _update_library_status(self) -> None:
        suffix = " · changes detected" if self._library_dirty else ""
        total = len(self.profiles)
        ready = max(0, min(int(getattr(self, "_library_cached_ready_count", 0) or 0), total))
        pending = max(0, total - ready)
        self._library_cached_pending_count = pending
        detail = f" · {ready} ready · {pending} pending" if total else ""
        family_state = str(getattr(self, "_family_reconciliation_state", "") or "").casefold()
        if family_state == "running":
            current = int(getattr(self, "_family_reconciliation_current", 0) or 0)
            family_total = int(getattr(self, "_family_reconciliation_total", 0) or 0)
            family_detail = f" · reconciling song families {current:,}/{family_total:,}" if family_total else " · reconciling song families"
        elif family_state == "cancelled":
            family_detail = " · song-family reconciliation needs Library Manager"
        else:
            family_detail = ""
        self.library_status.setText(f"{total} MIDI files{detail}{family_detail}{suffix}")

    def _configure_folder_watcher(self) -> None:
        # Recursive watching belongs to the independent Library Engine.
        return

    def _watched_library_changed(self, _path: str) -> None:
        return

    def _run_incremental_watch_refresh(self) -> None:
        return

    def _periodic_library_reconcile(self) -> None:
        self._poll_library_engine_state()

    def _apply_relationship_assessments(self, assessments: list[PairAssessment]) -> None:
        self._relationship_rows = list(assessments)
        self.relationship_table.setSortingEnabled(False)
        self.relationship_table.setRowCount(len(assessments))
        for row, item in enumerate(assessments):
            values = [
                (item.title_b, item.title_b.casefold()),
                (item.pairing_category, item.pairing_category),
                (f"{item.preparation_score}%", item.preparation_score),
                (f"{item.harmonic_score}%", item.harmonic_score),
                (f"{item.melody_a_over_b_score}%", item.melody_a_over_b_score),
                (f"{item.melody_b_over_a_score}%", item.melody_b_over_a_score),
                (f"{item.key_score}%", item.key_score),
                (f"{item.tempo_score}%", item.tempo_score),
                (f"{item.meter_score}%", item.meter_score),
            ]
            for col, (text, sort) in enumerate(values):
                cell = SortableItem(str(text), sort)
                cell.setData(Qt.ItemDataRole.UserRole, item.source_b)
                cell.setToolTip("\n".join(item.observations))
                self.relationship_table.setItem(row, col, cell)
        self.relationship_table.setSortingEnabled(True)
        if assessments:
            self.relationship_table.selectRow(0)
            selected_profiles = self._effective_profiles()
            felt_values = [profile.felt_bpm for profile in selected_profiles if profile.felt_bpm]
            if felt_values:
                self.target_bpm.setValue(int(round(statistics.median(felt_values))))
        else:
            self.relationship_detail.setPlainText("No other classified MIDI files are available. Add more files in the Library.")
        refresh_conflicts = getattr(self, "_refresh_conflicts_table", None)
        if callable(refresh_conflicts):
            refresh_conflicts()
        self._update_workflow_state()

    def _refresh_relationships(self) -> None:
        self._relationship_rows = []
        if len(self.source_specs) < 1:
            self.relationship_table.setSortingEnabled(False)
            self.relationship_table.setRowCount(0)
            self.relationship_detail.setPlainText("Choose a main song first.")
            self.relationship_table.setSortingEnabled(True)
            self._update_workflow_state()
            return

        anchor = self._effective_profile(self.source_specs[0])
        active_paths = {spec.path for spec in self.source_specs}
        explicit = [
            self._effective_profile(spec)
            for spec in self.source_specs[1:]
            if spec.path in self.profiles
        ]
        limit = max(10, int(self.settings_store.get("remix_candidate_limit", 50)))

        if len(self.profiles) <= 2500:
            candidates = list(explicit)
            candidates.extend(
                profile for profile in self.profiles.values()
                if profile.path != anchor.path and profile.path not in active_paths
            )
            assessments = [assess_pair(anchor, candidate) for candidate in candidates]
            assessments.sort(
                key=lambda item: (item.harmonic_score, max(item.melody_a_over_b_score, item.melody_b_over_a_score), item.key_score, item.preparation_score),
                reverse=True,
            )
            self._apply_relationship_assessments(assessments[:limit])
            return

        # Large libraries must never perform a 96k pair walk on the Qt thread.
        # Build the metadata index and assess only a bounded candidate set on an
        # owned worker. The table remains responsive while this completes.
        generation = int(getattr(self, "_relationship_generation", 0) or 0) + 1
        self._relationship_generation = generation
        for name in self.operation_manager.active_names():
            if name.startswith("workbench-relationships-"):
                self.operation_manager.cancel(name)
        operation_name = f"workbench-relationships-{generation}"
        profile_lookup = self.profiles
        weak_self = weakref.ref(self)
        self.relationship_table.setSortingEnabled(False)
        self.relationship_table.setRowCount(0)
        self.relationship_table.setSortingEnabled(True)
        self.relationship_detail.setPlainText(
            f"Finding the strongest related songs for {anchor.title} in the background…"
        )
        progress_bar = getattr(self, "relationship_progress", None)
        if progress_bar is not None:
            progress_bar.setVisible(True)
            progress_bar.setRange(0, 100)
            progress_bar.setValue(0)
            progress_bar.setFormat("Searching related songs… %p%")
        self._update_workflow_state()

        def work(cancel_event, progress):
            progress("Asking MIDI Library Engine for related candidates…", 10)
            if cancel_event.is_set():
                raise InterruptedError("Relationship search cancelled")
            response = self.library_engine_client.command(
                "related_candidates", timeout=4.0, start_if_needed=False, anchor_path=anchor.path, limit=max(96, limit * 3)
            )
            progress("Loading only the bounded related-song profiles…", 45)
            candidate_paths = tuple(str(path) for path in response.get("paths") or ())
            requested = [path for path in candidate_paths if path not in active_paths][:max(96, limit * 3)]
            loaded = self.load_deep_profiles(requested)
            candidates: list[MidiProfile] = list(explicit)
            seen = {profile.path for profile in candidates}
            for path in requested:
                if cancel_event.is_set():
                    raise InterruptedError("Relationship search cancelled")
                if path in seen:
                    continue
                profile = loaded.get(path) or profile_lookup.get(path)
                if profile is not None:
                    candidates.append(profile)
                    seen.add(path)
            progress("Comparing musical relationships…", 75)
            assessments = [assess_pair(anchor, candidate) for candidate in candidates]
            assessments.sort(
                key=lambda item: (item.harmonic_score, max(item.melody_a_over_b_score, item.melody_b_over_a_score), item.key_score, item.preparation_score),
                reverse=True,
            )
            progress("Related-song list ready.", 100)
            return assessments[:limit]

        def success(result) -> None:
            owner = weak_self()
            if owner is None or generation != getattr(owner, "_relationship_generation", -1):
                return
            progress_bar = getattr(owner, "relationship_progress", None)
            if progress_bar is not None:
                progress_bar.setValue(100)
                progress_bar.setFormat("Related-song search complete")
                schedule_owned(owner, "hide-relationship-progress", 1800, lambda: progress_bar.setVisible(False))
            owner._apply_relationship_assessments(list(result))

        def failure(exc: BaseException) -> None:
            owner = weak_self()
            if owner is None or generation != getattr(owner, "_relationship_generation", -1):
                return
            progress_bar = getattr(owner, "relationship_progress", None)
            if progress_bar is not None:
                progress_bar.setVisible(False)
            if "cancel" in str(exc).casefold():
                return
            owner.relationship_detail.setPlainText(
                f"Related-song search stopped safely: {type(exc).__name__}: {exc}"
            )
            owner._write_ui_error_log("Workbench related-song search", exc)
            owner._update_workflow_state()

        def progress_update(message: str, percent: int | None) -> None:
            owner = weak_self()
            if owner is None or generation != getattr(owner, "_relationship_generation", -1):
                return
            owner.statusBar().showMessage(message)
            progress_bar = getattr(owner, "relationship_progress", None)
            if progress_bar is not None:
                progress_bar.setVisible(True)
                if percent is None:
                    progress_bar.setRange(0, 0)
                    progress_bar.setFormat(message)
                else:
                    progress_bar.setRange(0, 100)
                    progress_bar.setValue(max(0, min(100, int(percent))))
                    progress_bar.setFormat(f"{message} · %p%")

        self.operation_manager.start(
            operation_name, work, on_success=success, on_failure=failure,
            on_progress=progress_update,
        )

    def recommendation_pair_ignored(self, first: MidiProfile, second: MidiProfile) -> bool:
        return pair_is_ignored(self.library_scan.database, first, second)

    def recommendation_pair_neither(self, first: MidiProfile, second: MidiProfile) -> bool:
        store = getattr(self, "pair_dispositions", None)
        return bool(store is not None and store.is_neither(first, second))

    def recommendation_pair_suppressed(self, first: MidiProfile, second: MidiProfile) -> bool:
        """True when a pair is intentionally retired from creative suggestions."""
        return self.recommendation_pair_ignored(first, second) or self.recommendation_pair_neither(first, second)

    def mark_pair_neither(self, first: MidiProfile, second: MidiProfile, *, reason: str) -> None:
        self.pair_dispositions.classify_neither(first, second, reason=reason)
        self.statusBar().showMessage(
            f"Suppressed redundant pair: {first.title} + {second.title}. It will no longer appear as a Mash or Version Fusion candidate.",
            8000,
        )

    def record_mash_feedback(self, profiles: tuple[MidiProfile, ...] | list[MidiProfile], action: str) -> None:
        items = tuple(profiles)
        for index, first in enumerate(items):
            for second in items[index + 1:]:
                identity_a, identity_b = pair_identities(first, second)
                if identity_a == identity_b:
                    continue
                try:
                    self.library_engine_client.command(
                        "record_feedback", timeout=3.0, start_if_needed=False, identity_a=identity_a, identity_b=identity_b, action=action
                    )
                except (OSError, RuntimeError, ValueError, TypeError) as exc:
                    self._write_ui_error_log("Discovery feedback", exc)

    def ignore_mash_pair(self, first: MidiProfile, second: MidiProfile) -> None:
        ignore_profile_pair(self.library_scan.database, first, second)
        self.record_mash_feedback((first, second), "ignore")
        self.statusBar().showMessage(
            f"Ignored mash relationship: {first.title} + {second.title}. Alternate filenames/versions will also be suppressed when identity evidence matches.",
            7000,
        )

    def manage_ignored_mash_ideas(self) -> None:
        ignored = self.library_scan.database.ignored_pairs()
        neither = list(self.pair_dispositions.neither_pairs())
        if not ignored and not neither:
            QMessageBox.information(
                self, "Suppressed Mash / Fusion Ideas",
                "No mash relationships or redundant Mash/Fusion pairs are currently suppressed.",
            )
            return
        entries: list[tuple[str, str, object]] = []
        for item in ignored:
            label = f"Ignored mash · {item['label_a']} + {item['label_b']} · {item['created_utc']}"
            entries.append((label, "ignored", item))
        for item in neither:
            reason = item.reason.replace("_", " ") if item.reason else "not enough distinct musical material"
            label = f"Neither mash nor Fusion · {item.label_a} + {item.label_b} · {reason}"
            entries.append((label, "neither", item))
        labels = [item[0] for item in entries]
        choice, ok = QInputDialog.getItem(
            self, "Manage Suppressed Mash / Fusion Ideas",
            "Choose one relationship to restore to recommendation eligibility:", labels, 0, False
        )
        if not ok or not choice:
            return
        _label, kind, item = entries[labels.index(choice)]
        if kind == "ignored":
            self.library_scan.database.restore_pair(item["identity_a"], item["identity_b"])
            restored = f"{item['label_a']} + {item['label_b']}"
        else:
            self.pair_dispositions.restore(item.identity_a, item.identity_b)
            restored = f"{item.label_a} + {item.label_b}"
        self.statusBar().showMessage(f"Restored recommendation eligibility: {restored}", 6000)

    def _write_ui_error_log(self, area: str, exc: BaseException) -> Path | None:
        """Record a recoverable UI failure without touching source MIDI files."""
        return write_diagnostic(self.paths.logs / "ui-errors.log", area, exc)

    def _operation_error_text(
        self, area: str, exc: BaseException, *, path: str | Path | None = None, corrective_action: str = ""
    ) -> str:
        return operation_failure(
            area, exc, path=path,
            corrective_action=corrective_action or
            "Try the action again. If it repeats, open Help → Runtime Diagnostics and review logs/ui-errors.log.",
        )

    def open_library_browser(self, *, purpose: str = "sources", selected_callback=None) -> None:
        """Open Library Manager for full browsing, or a bounded Remixer selector.

        Product ownership is intentionally one-way: Remixer depends on the
        authoritative MIDI Library Manager, while Library Manager does not
        depend on Remixer.  A browse-only request therefore opens the complete
        Library Manager application with all of its playback, volume, scanning,
        filtering, lifecycle, quarantine, and maintenance functionality.

        Remixer keeps only narrow selection dialogs for choosing mash sources or
        anchors.  Those selectors are read-only against the authoritative
        catalogue and never pretend to be a second Library Manager.
        """
        if purpose == "browse":
            self._open_library_manager()
            return

        database_path = self.paths.library / "library_scan.sqlite3"
        if not database_path.is_file():
            QMessageBox.information(
                self, "MIDI Library",
                "No saved MIDI catalogue is available yet. Open MIDI Library Manager to create, recover, or scan the authoritative Library first.",
            )
            return
        try:
            dialog = PersistentLibraryBrowserDialog(database_path, self, purpose=purpose)
            self.library_selector_dialog = dialog
            dialog.auditionRequested.connect(
                lambda path, label: self._play_path(path, f"Song: {label}", f"song-{Path(path).stem}")
            )
            dialog.pauseRequested.connect(self.pause_or_resume_current)
            dialog.stopRequested.connect(self.stop_playback)
            dialog.volumeChanged.connect(self.volume_slider.setValue)
            dialog.libraryManagerRequested.connect(self._open_library_manager)
            if purpose in {"anchor", "fusion_anchor"}:
                if callable(selected_callback):
                    dialog.anchorSelected.connect(selected_callback)
            elif purpose == "sources":
                dialog.sourcesSelected.connect(self._add_source_paths)
                if callable(selected_callback):
                    dialog.sourcesSelected.connect(selected_callback)
            dialog.exec()
        except Exception as exc:
            self._write_ui_error_log("Open persistent MIDI Library", exc)
            QMessageBox.critical(
                self, "MIDI Library Could Not Open",
                "The saved Library browser encountered an error, but the Remixer and MIDI files remain safe.\n\n"
                f"{type(exc).__name__}: {exc}",
            )
        finally:
            self.library_selector_dialog = None

    def open_detailed_library_browser(self) -> None:
        """Compatibility entry point: the full Library lives in Library Manager.

        Older Remixer builds exposed a second analysis-rich Library window.  The
        product is now deliberately split: full Library browsing and management
        always launch MIDI Library Manager.  Remixer-specific source/anchor
        selection continues through :meth:`open_library_browser` with an
        explicit selection purpose.
        """
        self._open_library_manager()
        return

