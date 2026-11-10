from __future__ import annotations

"""Remixer-only attachment policy over the qualified Contract-4 Engine surface.

The shared ``library_engine`` package remains byte-identical to the qualified
Library Manager contract. This wrapper adds one product rule: Remixer may attach
or restart an Engine only for an already-established Library; it never creates
the first authoritative Library on a fresh user profile.
"""

from pathlib import Path
from threading import Event
from typing import Iterable
import gc
import os
import sqlite3

from library_engine.attachment import EngineDatabaseFacade, LibraryEngineAttachment
from library_engine.client import LibraryEngineClient, LibraryEngineUnavailable
from midi_library_scan.models import CatalogMatch, DeepAnalysisPolicy, DuplicateKind, QuickScanResult
from core.remix_profile import KEY_TO_PC, MidiProfile


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def load_compact_catalogue_results(database_path: str | Path, roots=None) -> list[QuickScanResult]:
    """Hydrate Remixer's compact startup rows without decoding Library-only blobs.

    The authoritative schema remains owned by Library Manager/Engine. Remixer only
    needs a bounded set of scalar catalogue fields to build its resident lightweight
    MidiProfile summaries. Detailed profiles and full Library metadata remain lazy.
    """
    path = Path(database_path).expanduser().resolve(strict=False)
    if not path.is_file():
        return []
    where = ""
    params: list[object] = []
    if roots == []:
        return []
    if roots:
        clauses: list[str] = []
        for raw in roots:
            candidate = Path(raw).expanduser()
            try:
                resolved = candidate.resolve(strict=False)
            except OSError:
                continue
            if resolved.suffix.casefold() in {".mid", ".midi", ".kar"}:
                clauses.append("path=?")
                params.append(str(resolved))
            else:
                prefix = str(resolved).rstrip("\\/") + os.sep
                clauses.append("path LIKE ? ESCAPE '\\'")
                params.append(_escape_like(prefix) + "%")
        if clauses:
            where = " WHERE " + " OR ".join(f"({clause})" for clause in clauses)
        else:
            return []
    uri = path.as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=2.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1500")
    sql = (
        "SELECT path,filename,size_bytes,modified_ns,sha256,midi_type,ticks_per_beat,track_count,"
        "active_channel_count,note_count,duration_seconds,bpm,bpm_min,bpm_max,tempo_change_count,"
        "detected_key,mode,key_confidence,key_source,meter,arrangement_type,composition_id,catalog_artist,"
        "catalog_title,catalog_genres,catalog_primary_genre,catalog_confidence,catalog_status,family_id,"
        "duplicate_group,composition_group,content_duplicate_group,duplicate_kind,musical_fingerprint,"
        "quick_status,error FROM library_catalog_files_v11" + where + " ORDER BY path"
    )
    try:
        rows = conn.execute(sql, tuple(params)).fetchall()
    finally:
        conn.close()
    gc_was_enabled = gc.isenabled()
    if gc_was_enabled:
        gc.disable()
    try:
        results: list[QuickScanResult] = []
        append = results.append
        for row in rows:
            try:
                duplicate_kind = DuplicateKind(str(row["duplicate_kind"] or "none"))
            except ValueError:
                duplicate_kind = DuplicateKind.NONE
            append(QuickScanResult(
                path=str(row["path"]), filename=str(row["filename"]), size_bytes=int(row["size_bytes"] or 0),
                modified_ns=int(row["modified_ns"] or 0), sha256=str(row["sha256"] or ""),
                midi_type=row["midi_type"], ticks_per_beat=row["ticks_per_beat"],
                track_count=int(row["track_count"] or 0), active_channel_count=int(row["active_channel_count"] or 0),
                note_count=int(row["note_count"] or 0), duration_seconds=row["duration_seconds"], bpm=row["bpm"],
                bpm_min=row["bpm_min"], bpm_max=row["bpm_max"], tempo_change_count=int(row["tempo_change_count"] or 0),
                key=row["detected_key"], mode=row["mode"], key_confidence=float(row["key_confidence"] or 0.0),
                key_source=str(row["key_source"] or "unavailable"), meter=row["meter"],
                arrangement_type=str(row["arrangement_type"] or "Unknown arrangement"),
                program_families=[], embedded_text=[],
                catalog_match=CatalogMatch(
                    composition_id=row["composition_id"], artist=row["catalog_artist"], title=row["catalog_title"],
                    genres=row["catalog_genres"], primary_genre=row["catalog_primary_genre"],
                    confidence=float(row["catalog_confidence"] or 0.0), status=str(row["catalog_status"] or "unmatched"),
                    evidence=[],
                ),
                family_id=row["family_id"], duplicate_group=row["duplicate_group"],
                composition_group=row["composition_group"], content_duplicate_group=row["content_duplicate_group"],
                duplicate_kind=duplicate_kind, musical_fingerprint=row["musical_fingerprint"],
                status=str(row["quick_status"] or "catalogued"), error=row["error"],
            ))
        return results
    finally:
        if gc_was_enabled:
            gc.enable()


def _compact_meter_parts(value: str | None) -> tuple[int, int]:
    try:
        numerator, denominator = str(value or "4/4").split("/", 1)
        return max(1, int(numerator)), max(1, int(denominator))
    except (TypeError, ValueError):
        return 4, 4


def load_compact_catalogue_profiles(database_path: str | Path, roots=None) -> dict[str, MidiProfile]:
    """Build Remixer's resident lightweight profile map directly from committed SQL.

    Avoids allocating a second 96K QuickScanResult population during returning-user
    startup. Full catalogue metadata and detailed profiles remain lazy/read-only.
    """
    path = Path(database_path).expanduser().resolve(strict=False)
    if not path.is_file() or roots == []:
        return {}
    where = ""
    params: list[object] = []
    if roots:
        clauses: list[str] = []
        for raw in roots:
            candidate = Path(raw).expanduser()
            try:
                resolved = candidate.resolve(strict=False)
            except OSError:
                continue
            if resolved.suffix.casefold() in {".mid", ".midi", ".kar"}:
                clauses.append("path=?")
                params.append(str(resolved))
            else:
                prefix = str(resolved).rstrip("\\/") + os.sep
                clauses.append("path LIKE ? ESCAPE '\\'")
                params.append(_escape_like(prefix) + "%")
        if not clauses:
            return {}
        where = " WHERE " + " OR ".join(f"({clause})" for clause in clauses)
    uri = path.as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=2.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1500")
    sql = (
        "SELECT path,filename,sha256,midi_type,ticks_per_beat,track_count,note_count,duration_seconds,bpm,"
        "tempo_change_count,detected_key,mode,key_confidence,meter,arrangement_type,composition_id,"
        "catalog_artist,catalog_title,catalog_genres,catalog_primary_genre,catalog_confidence,catalog_status,"
        "duplicate_group,composition_group,musical_fingerprint,error FROM library_catalog_files_v11" + where
    )
    gc_was_enabled = gc.isenabled()
    if gc_was_enabled:
        gc.disable()
    try:
        result: dict[str, MidiProfile] = {}
        Profile = MidiProfile
        key_lookup = KEY_TO_PC.get
        meter_parts = _compact_meter_parts
        cursor = conn.execute(sql, tuple(params))
        for row in cursor:
            (
                path_text, filename, sha256, midi_type, ticks_raw, track_count, note_count,
                duration_raw, bpm_raw, tempo_change_count, detected_key, mode_raw, key_confidence,
                meter_raw, arrangement_raw, composition_id, catalog_artist, catalog_title,
                catalog_genres, catalog_primary_genre, catalog_confidence, catalog_status,
                duplicate_group_raw, composition_group, musical_fingerprint, error_raw,
            ) = row
            path_text = str(path_text or "")
            filename = str(filename or "")
            sha256 = str(sha256 or "")
            bpm = float(bpm_raw) if bpm_raw is not None else None
            duration = float(duration_raw or 0.0)
            ticks_per_beat = int(ticks_raw or 480)
            numerator, denominator = meter_parts(meter_raw)
            total_ticks = int(round(duration * (bpm or 120.0) * ticks_per_beat / 60.0)) if duration else 0
            quarter_beats_per_bar = numerator * (4.0 / denominator)
            bars = total_ticks / max(1.0, ticks_per_beat * quarter_beats_per_bar)
            key = str(detected_key or "") or None
            mode = str(mode_raw or "major")
            key_name = key[:-1] if key and key.endswith("m") else key
            tonic = key_lookup(key_name) if key_name else None
            title = str(catalog_title).strip() if catalog_title else ""
            if not title:
                stem = filename.rsplit(".", 1)[0] if "." in filename else filename
                title = stem.replace("_", " ").replace("-", " ").strip() or filename
            composer = str(catalog_artist).strip() if catalog_artist else "Unknown"
            primary_genre = str(catalog_primary_genre).strip() if catalog_primary_genre else ""
            style = primary_genre or "Unclassified"
            status = str(catalog_status) if catalog_status else "unmatched"
            warnings = ["Deep musical analysis pending"]
            if status != "unmatched" or catalog_confidence:
                confidence = float(catalog_confidence or 0.0)
                warnings.append(f"Catalog identity: {status} ({int(round(confidence * 100))}%)")
            duplicate_group = str(duplicate_group_raw or "")
            if duplicate_group:
                warnings.append(f"Duplicate/version group: {duplicate_group}")
            error = str(error_raw or "")
            if error:
                warnings.append(error)
            note_count = int(note_count or 0)
            arrangement = str(arrangement_raw or "Unknown arrangement")
            readiness = 35 if arrangement == "Multi-instrument" else 18 if note_count else 0
            genres = str(catalog_genres).strip() if catalog_genres else ""
            tags = [tag for tag in (primary_genre, genres) if tag]
            result[path_text] = Profile(
                path=path_text, fingerprint=sha256[:24], title=title, composer=composer, style=style,
                midi_type=int(midi_type or 0), ticks_per_beat=ticks_per_beat,
                track_count=int(track_count or 0), note_count=note_count, duration_seconds=duration,
                total_ticks=total_ticks, tempo_bpm=bpm, felt_bpm=bpm,
                tempo_change_count=int(tempo_change_count or 0), numerator=numerator, denominator=denominator,
                meter=str(meter_raw or f"{numerator}/{denominator}"), meter_family="catalogued", bars=bars,
                initial_key_signature=(f"{key}m" if key and mode == "minor" and not key.endswith("m") else key),
                key_changes=[], tonic_pc=tonic, mode=mode, note_tracks=[], melody_track_index=None,
                accompaniment_track_index=None, warnings=warnings, declared_tonic_pc=tonic, declared_mode=mode,
                analyzed_tonic_pc=tonic, analyzed_mode=mode,
                key_confidence=int(round(float(key_confidence or 0.0) * 100)), key_agreement="quick estimate",
                arrangement_type=arrangement, orchestration_readiness=readiness, melody_extraction_confidence=0,
                progression_family="Pending deep analysis", reference_tags=tags,
                content_fingerprint=sha256, song_identity=str(composition_id or composition_group or ""),
                musical_fingerprint=str(musical_fingerprint or ""),
            )
        return result
    finally:
        conn.close()
        if gc_was_enabled:
            gc.enable()


class RemixerEngineDatabaseFacade(EngineDatabaseFacade):
    """Read facade with a Remixer-only compact six-figure startup path."""

    def load_compact_results(self, roots=None):
        return load_compact_catalogue_results(self.path, roots)

    def load_compact_profiles(self, roots=None):
        return load_compact_catalogue_profiles(self.path, roots)


class _OfflineEngineDatabaseFacade:
    def close(self) -> None: return None
    def load_result(self, *args, **kwargs): return None
    def load_results(self, *args, **kwargs): return []
    def load_compact_results(self, *args, **kwargs): return []
    def load_compact_profiles(self, *args, **kwargs): return {}
    def deep_status_counts(self, *args, **kwargs): return {}
    def deep_status(self, *args, **kwargs): return None
    def catalogue_counts(self, *args, **kwargs): return {}
    def lifecycle_counts(self): return {}
    def lifecycle_for_path(self, *args, **kwargs): return None
    def blessed_paths(self, *args, **kwargs): return set()
    def deep_health_stats(self, *args, **kwargs): return {}
    def family_index(self, *args, **kwargs): return {}
    def family_id_for_path(self, *args, **kwargs): return None
    def family_paths(self, *args, **kwargs): return []
    def ignored_pairs(self): return []
    def is_pair_ignored(self, *args, **kwargs): return False
    def get_policy(self): return DeepAnalysisPolicy.LATER.value
    def checkpoint(self, *args, **kwargs) -> None: return None

    @staticmethod
    def _unavailable() -> None:
        raise LibraryEngineUnavailable(
            "No authoritative MIDI Library exists yet. Open MIDI Library Manager to create or recover it."
        )

    def ignore_pair(self, *args, **kwargs) -> None: self._unavailable()
    def restore_pair(self, *args, **kwargs) -> None: self._unavailable()
    def prioritize_paths(self, *args, **kwargs) -> int: self._unavailable(); return 0
    def delete_paths(self, *args, **kwargs) -> int: self._unavailable(); return 0
    def clear(self) -> None: self._unavailable()


class _RemoteDeepQueue:
    def __init__(self, attachment: "RemixerLibraryAttachment") -> None:
        self.attachment = attachment

    def stop(self, timeout: float = 0.0) -> bool:
        # Closing Remixer never pauses/stops the shared Library analysis queue.
        return True

    def is_alive(self) -> bool:
        try:
            return bool(self.attachment.client.status(start_if_needed=False).get("deep", {}).get("queue_alive"))
        except (LibraryEngineUnavailable, RuntimeError):
            return False


class RemixerLibraryAttachment(LibraryEngineAttachment):
    """Contract-4 attachment that cannot establish a first Library."""

    def __init__(self, app_root: str | Path, database_path: str | Path | None = None) -> None:
        self.app_root = Path(app_root).resolve()
        self.client = LibraryEngineClient(
            self.app_root,
            required_capabilities={
                "catalogue_deltas", "prepared_recommendations", "related_candidates",
                "read_committed_library", "storage_contract", "persisted_lifecycle_eligibility",
            },
        )
        self.cancel_event = Event()
        self.available = False
        self.offline_reason = ""
        self.library_root = self.client.paths.library
        self.database_path = self.library_root / "library_scan.sqlite3"
        self.library_index_path = self.library_root / "library_index.sqlite3"
        self.relationship_database_path = self.library_root / "library_relationships.sqlite3"
        self.storage_contract: dict[str, object] = {}
        self.database = _OfflineEngineDatabaseFacade()
        self.deep_queue = _RemoteDeepQueue(self)
        self._database_path_override = Path(database_path).expanduser().resolve() if database_path is not None else None
        self._connect_existing()

    def _connect_existing(self) -> bool:
        if self.available:
            return True
        existing = self.client.ping(timeout=0.35)
        established = all(path.is_file() for path in (
            self.database_path, self.library_index_path, self.relationship_database_path
        ))
        try:
            if existing is not None:
                # Validate only: Remixer must never shut down/replace an
                # incompatible Engine merely because it launched second.
                self.client._validate_running_engine(existing)
                status = self.client.status(start_if_needed=False)
            elif established:
                # Returning-user recovery is allowed: databases already prove
                # that Library Manager established the authoritative Library.
                self.client.ensure_running(timeout=20.0)
                status = self.client.status(start_if_needed=False)
            else:
                self.offline_reason = (
                    "No authoritative MIDI Library exists yet. Open MIDI Library Manager to create or recover it."
                )
                return False
        except (LibraryEngineUnavailable, OSError, RuntimeError, ValueError, TypeError) as exc:
            self.offline_reason = str(exc)
            return False

        storage = status.get("storage") if isinstance(status, dict) else None
        if not isinstance(storage, dict):
            self.offline_reason = "The running MIDI Library Engine did not publish its database storage contract."
            return False
        try:
            library_root = Path(str(storage.get("library_root") or "")).expanduser().resolve()
            quick_database = Path(str(storage.get("quick_database") or "")).expanduser().resolve()
            detail_database = Path(str(storage.get("detail_database") or "")).expanduser().resolve()
            relationship_database = Path(str(storage.get("relationship_database") or "")).expanduser().resolve()
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            self.offline_reason = f"Library Engine published invalid database paths: {exc}"
            return False
        expected = (
            ("catalogue", quick_database), ("detailed analysis", detail_database),
            ("recommendations", relationship_database),
        )
        if not library_root.is_dir() or any(path.parent != library_root or not path.is_file() for _label, path in expected):
            self.offline_reason = "Library Engine storage contract is incomplete or points outside its authoritative Library root."
            return False
        if self._database_path_override is not None and self._database_path_override != quick_database:
            self.offline_reason = (
                "MIDI Remixer and MIDI Library Engine resolved different catalogue databases. "
                f"Remixer expected {self._database_path_override}, Engine owns {quick_database}."
            )
            return False
        self.database.close()
        self.library_root = library_root
        self.database_path = quick_database
        self.library_index_path = detail_database
        self.relationship_database_path = relationship_database
        self.storage_contract = dict(storage)
        self.database = RemixerEngineDatabaseFacade(self.database_path, self.client)
        self.available = True
        self.offline_reason = ""
        return True

    def reconnect_existing(self) -> bool:
        if self.available:
            running = self.client.ping(timeout=0.35)
            if running is not None:
                try:
                    self.client._validate_running_engine(running)
                    return True
                except LibraryEngineUnavailable as exc:
                    self.offline_reason = str(exc)
                    return False
            self.database.close()
            self.database = _OfflineEngineDatabaseFacade()
            self.available = False
        return self._connect_existing()

    def _require_existing_engine(self) -> None:
        if not self.available and not self.reconnect_existing():
            raise LibraryEngineUnavailable(self.offline_reason or "The authoritative MIDI Library Engine is offline.")

    def analyze_selected(self, path: str | Path) -> None:
        self._require_existing_engine()
        self.client.command("prioritize", paths=[str(path)], priority=0, start_if_needed=False)

    def retry_failed_deep_analysis(self) -> int:
        self._require_existing_engine()
        return int(self.client.command("retry_failed", start_if_needed=False).get("retried") or 0)

    def pause_deep_analysis(self) -> None:
        raise RuntimeError("Detailed-analysis controls belong to MIDI Library Manager.")

    def suspend_deep_analysis(self) -> bool:
        return False

    def resume_suspended_deep_analysis(self, only_paths=None) -> bool:
        return False

    def resume_deep_analysis(self, only_paths=None) -> None:
        raise RuntimeError("Detailed-analysis controls belong to MIDI Library Manager.")

    def apply_deep_policy(self, policy: DeepAnalysisPolicy, selected_path=None, only_paths=None) -> None:
        raise RuntimeError("Detailed-analysis policy belongs to MIDI Library Manager.")

    def cancel_scan(self) -> None:
        # Remixer never owns a Library scan, so Close/Cancel cannot stop one.
        self.cancel_event.set()

    def close(self) -> None:
        self.database.close()
