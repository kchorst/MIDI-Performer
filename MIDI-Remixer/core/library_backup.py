from __future__ import annotations

"""Backup/restore persistent MIDI Library intelligence safely.

Library Intelligence is intentionally independent of source MIDI files and of an
extracted application build.  Backups contain only persistent, reusable library
state: quick catalogue, detailed analysis, prepared relationships, discovery
graph, Engine-owned settings, and imported reference evidence.

Restore is designed to run only after the Library Engine has stopped and released
its SQLite handles.  The Manager enforces that lifecycle before calling restore.
"""

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import tempfile
import zipfile

from core.app_paths import AppPaths
from core.remix_version import APP_VERSION


@dataclass(frozen=True, slots=True)
class LibraryBackupResult:
    path: str
    files: tuple[str, ...]
    bytes: int


@dataclass(frozen=True, slots=True)
class LibraryRestoreResult:
    source: str
    restored_files: tuple[str, ...]
    manifest: dict[str, object]


_SQLITE_FILES = (
    "library_scan.sqlite3",
    "library_index.sqlite3",
    "library_relationships.sqlite3",
    "discovery_graph.sqlite3",
)
_ENGINE_SETTINGS = "library_engine_settings.json"
_FORMAT_V1 = "midi-remixer-library-intelligence-backup-v1"
_FORMAT_V2 = "midi-remixer-library-intelligence-backup-v2"
_SUPPORTED_FORMATS = {_FORMAT_V1, _FORMAT_V2}


def _sqlite_integrity(path: Path) -> tuple[bool, int]:
    if not path.is_file():
        return False, 0
    with closing(sqlite3.connect(path, timeout=30)) as conn:
        conn.execute("PRAGMA busy_timeout=30000")
        integrity = conn.execute("PRAGMA integrity_check").fetchone()
        if not integrity or str(integrity[0]).casefold() != "ok":
            raise RuntimeError(f"SQLite integrity check failed for {path.name}: {integrity!r}")
        user_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    return True, user_version


def _backup_sqlite(source: Path, destination: Path) -> tuple[bool, int]:
    if not source.is_file():
        return False, 0
    destination.parent.mkdir(parents=True, exist_ok=True)
    # A normal read connection includes committed WAL state. The online backup
    # API produces one self-contained destination DB instead of copying -wal/-shm.
    with closing(sqlite3.connect(source, timeout=30)) as src, closing(sqlite3.connect(destination, timeout=30)) as dst:
        src.execute("PRAGMA busy_timeout=30000")
        src.backup(dst, pages=2048, sleep=0.01)
        dst.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
    return _sqlite_integrity(destination)


def create_library_intelligence_backup(paths: AppPaths, destination: str | Path) -> LibraryBackupResult:
    """Create one verified ZIP containing persistent Library Intelligence."""
    target = Path(destination).expanduser()
    if target.suffix.casefold() != ".zip":
        target = target.with_suffix(".zip")
    target.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="midi-library-backup-") as temp_name:
        temp = Path(temp_name)
        included: list[str] = []
        sqlite_versions: dict[str, int] = {}
        for filename in _SQLITE_FILES:
            copied, version = _backup_sqlite(paths.library / filename, temp / "library" / filename)
            if copied:
                archive_name = f"library/{filename}"
                included.append(archive_name)
                sqlite_versions[archive_name] = int(version)

        # Engine-owned settings are part of the Library contract.  Earlier v1
        # backups accidentally copied Remixer app_settings.json instead, which
        # could omit the actual configured Library roots. v2 fixes ownership.
        engine_settings = paths.config / _ENGINE_SETTINGS
        if engine_settings.is_file():
            out = temp / "config" / engine_settings.name
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(engine_settings, out)
            included.append(f"config/{engine_settings.name}")

        legacy_app_settings = paths.config / "app_settings.json"
        if legacy_app_settings.is_file():
            out = temp / "config" / legacy_app_settings.name
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(legacy_app_settings, out)
            included.append(f"config/{legacy_app_settings.name}")

        if paths.reference_imports.is_dir():
            for source in sorted(paths.reference_imports.rglob("*")):
                if not source.is_file() or source.is_symlink():
                    continue
                relative = source.relative_to(paths.reference_imports)
                out = temp / "reference_imports" / relative
                out.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, out, follow_symlinks=False)
                included.append((Path("reference_imports") / relative).as_posix())

        manifest = {
            "format": _FORMAT_V2,
            "app_version": APP_VERSION,
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "source_midi_files_included": False,
            "engine_settings_included": f"config/{_ENGINE_SETTINGS}" in included,
            "sqlite_user_versions": sqlite_versions,
            "included": sorted(included),
        }
        manifest_path = temp / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        included.append("manifest.json")

        partial = target.with_name(f".{target.name}.partial")
        partial.unlink(missing_ok=True)
        try:
            with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
                for file_path in sorted((p for p in temp.rglob("*") if p.is_file()), key=lambda p: p.as_posix().casefold()):
                    archive.write(file_path, file_path.relative_to(temp).as_posix())
            verify_library_intelligence_backup(partial)
            partial.replace(target)
        finally:
            partial.unlink(missing_ok=True)

    return LibraryBackupResult(str(target), tuple(sorted(included)), target.stat().st_size)


def _safe_archive_name(name: str) -> PurePosixPath:
    value = PurePosixPath(name)
    if value.is_absolute() or ".." in value.parts or not value.parts:
        raise RuntimeError(f"Unsafe path in Library backup: {name!r}")
    return value


def _extract_verified_archive(source: Path, destination: Path) -> dict[str, object]:
    with zipfile.ZipFile(source, "r") as archive:
        bad = archive.testzip()
        if bad:
            raise RuntimeError(f"CRC failure in {bad}")
        try:
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        except (KeyError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("Not a valid MIDI Library Intelligence backup.") from exc
        if not isinstance(manifest, dict) or manifest.get("format") not in _SUPPORTED_FORMATS:
            raise RuntimeError("Unsupported Library backup format.")
        for info in archive.infolist():
            relative = _safe_archive_name(info.filename)
            if info.is_dir():
                continue
            target = destination.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info, "r") as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)

    # A CRC-valid ZIP is not enough. Verify every backed-up SQLite image before
    # the user is told the backup is usable or before restore touches live data.
    actual_versions: dict[str, int] = {}
    for filename in _SQLITE_FILES:
        candidate = destination / "library" / filename
        if candidate.is_file():
            _ok, version = _sqlite_integrity(candidate)
            actual_versions[f"library/{filename}"] = version
    declared = manifest.get("sqlite_user_versions") or {}
    if isinstance(declared, dict):
        for name, version in declared.items():
            if name in actual_versions and int(version) != actual_versions[name]:
                raise RuntimeError(
                    f"Library backup schema metadata mismatch for {name}: "
                    f"manifest {version}, database {actual_versions[name]}."
                )
    return manifest


def verify_library_intelligence_backup(path: str | Path) -> dict[str, object]:
    """Validate ZIP, manifest, safe paths, and SQLite integrity without restoring."""
    source = Path(path).expanduser()
    if not source.is_file():
        raise RuntimeError(f"Library backup does not exist: {source}")
    with tempfile.TemporaryDirectory(prefix="midi-library-verify-") as temp_name:
        return _extract_verified_archive(source, Path(temp_name))


def _replace_file_with_rollback(
    staged: Path | None, target: Path, rollback_root: Path,
    changed: list[tuple[Path, Path | None]],
) -> None:
    """Record an undo operation *before* installing replacement data.

    A missing staged path means remove an obsolete SQLite WAL/SHM sidecar,
    retaining the previous sidecar until the entire transaction commits.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    rollback: Path | None = None
    if target.exists():
        # Preserve unique paths, including files sharing a basename in different roots.
        rollback = rollback_root / f"item-{len(changed):04d}"
        os.replace(target, rollback)
    # Crucially, record the moved original before the operation that can fail.
    changed.append((target, rollback))
    if staged is not None:
        os.replace(staged, target)


def _rollback_replacements(changed: list[tuple[Path, Path | None]]) -> list[str]:
    errors: list[str] = []
    for target, rollback in reversed(changed):
        try:
            if rollback is not None:
                if not rollback.exists():
                    raise RuntimeError(f"Missing rollback image for {target}")
                # Do not remove the replacement until the original is known to exist.
                os.replace(rollback, target)
            elif target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink(missing_ok=True)
        except (OSError, RuntimeError) as exc:
            errors.append(f"{target}: {exc}")
    return errors


def restore_library_intelligence_backup(paths: AppPaths, source: str | Path) -> LibraryRestoreResult:
    """Restore a verified backup transactionally after the Engine has stopped.

    Stage and retain rollback data on the application's data volume. If recovery
    itself fails, keep the recovery directory so original data is not destroyed.
    """
    backup = Path(source).expanduser()
    paths.ensure()
    work_root = Path(tempfile.mkdtemp(prefix="midi-library-restore-", dir=paths.data_root))
    preserve_recovery = False
    try:
        staged_root = work_root / "staged"
        manifest = _extract_verified_archive(backup, staged_root)
        rollback_root = work_root / "rollback"
        rollback_root.mkdir()
        changed: list[tuple[Path, Path | None]] = []
        restored: list[str] = []
        try:
            for filename in _SQLITE_FILES:
                staged = staged_root / "library" / filename
                if not staged.is_file():
                    continue
                target = paths.library / filename
                # Sidecars must not replay into the restored image. Keep them in
                # the undo log so an interrupted restore can recover the old DB.
                for suffix in ("-wal", "-shm"):
                    _replace_file_with_rollback(
                        None, Path(str(target) + suffix), rollback_root, changed
                    )
                _replace_file_with_rollback(staged, target, rollback_root, changed)
                restored.append(f"library/{filename}")

            for settings_name in (_ENGINE_SETTINGS, "app_settings.json"):
                staged_settings = staged_root / "config" / settings_name
                if staged_settings.is_file():
                    payload = json.loads(staged_settings.read_text(encoding="utf-8"))
                    if not isinstance(payload, dict):
                        raise RuntimeError(f"Backed-up {settings_name} is not a JSON object.")
                    _replace_file_with_rollback(
                        staged_settings, paths.config / settings_name, rollback_root, changed
                    )
                    restored.append(f"config/{settings_name}")

            staged_refs = staged_root / "reference_imports"
            if staged_refs.is_dir():
                _replace_file_with_rollback(
                    staged_refs, paths.reference_imports, rollback_root, changed
                )
                restored.extend(
                    (Path("reference_imports") / item.relative_to(paths.reference_imports)).as_posix()
                    for item in paths.reference_imports.rglob("*") if item.is_file()
                )
        except Exception as exc:
            rollback_errors = _rollback_replacements(changed)
            if rollback_errors:
                preserve_recovery = True
                raise RuntimeError(
                    f"Library restore failed ({exc}); rollback also failed: "
                    + "; ".join(rollback_errors)
                    + f". Recovery files retained at: {work_root}"
                ) from exc
            raise
        return LibraryRestoreResult(str(backup), tuple(sorted(set(restored))), manifest)
    finally:
        if not preserve_recovery:
            shutil.rmtree(work_root)
