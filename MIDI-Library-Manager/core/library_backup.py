from __future__ import annotations

"""Backup/restore persistent MIDI Library intelligence safely.

Library Intelligence is intentionally independent of source MIDI files and of an
extracted application build.  Backups contain only persistent, reusable library
state: quick catalogue, detailed analysis, prepared relationships, discovery
graph, Engine-owned settings, and imported reference evidence.

Restore is designed to run only after the Library Engine has stopped and released
its SQLite handles.  The Manager enforces that lifecycle before calling restore.
"""

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
    with sqlite3.connect(path, timeout=30) as conn:
        conn.execute("PRAGMA busy_timeout=30000")
        integrity = conn.execute("PRAGMA integrity_check").fetchone()
        if not integrity or str(integrity[0]).casefold() != "ok":
            raise RuntimeError(f"SQLite integrity check failed for {path.name}: {integrity!r}")
        user_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    return True, user_version


def _sqlite_integrity_image(image: bytes, *, label: str) -> int:
    """Validate one SQLite image without reopening it through the filesystem.

    Target-Windows qualification proved that a valid backup member could be
    written successfully and then fail when SQLite reopened a second on-disk
    verification copy with WinError 267.  Python 3.12's SQLite binding exposes
    ``Connection.deserialize``; loading the exact ZIP member into an in-memory
    SQLite connection removes that unrelated filesystem/temporary-path boundary
    while still running SQLite's own full ``PRAGMA integrity_check`` against the
    archived bytes.
    """
    if not image:
        raise RuntimeError(f"SQLite backup member is empty: {label}")
    if not hasattr(sqlite3.Connection, "deserialize"):
        raise RuntimeError(
            "This Python SQLite runtime does not support in-memory backup verification "
            f"for {label}; Python 3.12 with sqlite3.Connection.deserialize is required."
        )
    # Online backup images can legitimately retain WAL read/write format bytes
    # (header offsets 18/19 == 2) even after their WAL has been fully checkpointed
    # into the self-contained main database. SQLite cannot deserialize a WAL-mode
    # header into an in-memory database because there is no WAL sidecar to open.
    # For verification only, normalize those two *journal-mode* header bytes to
    # rollback format. The archived bytes themselves are never modified.
    verify_image = image
    if image.startswith(b"SQLite format 3\x00") and len(image) >= 20 and (image[18] == 2 or image[19] == 2):
        normalized = bytearray(image)
        normalized[18] = 1
        normalized[19] = 1
        verify_image = bytes(normalized)
    try:
        with sqlite3.connect(":memory:", timeout=30) as conn:
            conn.deserialize(verify_image)
            integrity = conn.execute("PRAGMA integrity_check").fetchone()
            if not integrity or str(integrity[0]).casefold() != "ok":
                raise RuntimeError(f"SQLite integrity check failed for {label}: {integrity!r}")
            return int(conn.execute("PRAGMA user_version").fetchone()[0])
    except sqlite3.DatabaseError as exc:
        raise RuntimeError(f"Invalid SQLite database image in Library backup: {label}") from exc


def _backup_sqlite_image(source: Path) -> tuple[bool, int, bytes]:
    """Create one self-contained SQLite snapshot entirely in memory.

    Target-Windows qualification REG-101 proved that even a correctly created
    temporary *backup staging* SQLite file can fail with ``ERROR_DIRECTORY``
    when SQLite opens it under the process temp directory.  The backup producer
    therefore has no temporary SQLite filesystem boundary at all: the live
    database is copied with SQLite's online-backup API into an in-memory
    connection, integrity-checked there, then serialized directly into ZIP bytes.
    """
    if not source.is_file():
        return False, 0, b""
    if not hasattr(sqlite3.Connection, "serialize"):
        raise RuntimeError(
            "This Python SQLite runtime does not support in-memory backup creation; "
            f"Python 3.12 with sqlite3.Connection.serialize is required for {source.name}."
        )
    try:
        with sqlite3.connect(source, timeout=30) as src, sqlite3.connect(":memory:", timeout=30) as dst:
            src.execute("PRAGMA busy_timeout=30000")
            src.backup(dst, pages=2048, sleep=0.01)
            integrity = dst.execute("PRAGMA integrity_check").fetchone()
            if not integrity or str(integrity[0]).casefold() != "ok":
                raise RuntimeError(f"SQLite integrity check failed while backing up {source.name}: {integrity!r}")
            user_version = int(dst.execute("PRAGMA user_version").fetchone()[0])
            image = bytes(dst.serialize())
    except sqlite3.DatabaseError as exc:
        raise RuntimeError(f"Could not create a consistent SQLite backup image for {source.name}.") from exc
    # Independently verify the serialized bytes before they are admitted to the
    # archive.  This also proves that the producer and verifier agree on the
    # exact image that restore will later consume.
    verified_version = _sqlite_integrity_image(image, label=f"library/{source.name}")
    if verified_version != user_version:
        raise RuntimeError(
            f"SQLite backup schema changed while serializing {source.name}: "
            f"snapshot {user_version}, serialized {verified_version}."
        )
    return True, user_version, image


def _backup_sqlite(source: Path, destination: Path) -> tuple[bool, int]:
    """Compatibility helper for tests/tools that explicitly request a file.

    Production backup creation does not call this function.  It intentionally
    serializes an in-memory snapshot via :func:`_backup_sqlite_image` and writes
    the bytes directly to the ZIP.
    """
    copied, version, image = _backup_sqlite_image(source)
    if not copied:
        return False, 0
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(image)
    return True, version


def create_library_intelligence_backup(paths: AppPaths, destination: str | Path) -> LibraryBackupResult:
    """Create one verified ZIP containing persistent Library Intelligence.

    SQLite members never transit through a temporary SQLite database on disk.
    Each live database is copied into one in-memory SQLite snapshot, verified,
    serialized directly into the ZIP, then released before the next database is
    processed.  This removes the Windows temp-SQLite failure boundary without
    retaining the full multi-database Library in RAM at once.
    """
    target = Path(destination).expanduser()
    if target.suffix.casefold() != ".zip":
        target = target.with_suffix(".zip")
    target.parent.mkdir(parents=True, exist_ok=True)

    included: list[str] = []
    sqlite_versions: dict[str, int] = {}
    engine_settings = paths.config / _ENGINE_SETTINGS
    legacy_app_settings = paths.config / "app_settings.json"
    reference_files: list[tuple[Path, str]] = []
    if paths.reference_imports.is_dir():
        for source in sorted(paths.reference_imports.rglob("*")):
            if not source.is_file() or source.is_symlink():
                continue
            relative = source.relative_to(paths.reference_imports)
            reference_files.append((source, (Path("reference_imports") / relative).as_posix()))

    partial = target.with_name(f".{target.name}.partial")
    partial.unlink(missing_ok=True)
    try:
        with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            # Process one SQLite image at a time.  Do not accumulate all Library
            # databases in memory; a 96K-file Library can make those images large.
            for filename in _SQLITE_FILES:
                copied, version, image = _backup_sqlite_image(paths.library / filename)
                if not copied:
                    continue
                archive_name = f"library/{filename}"
                archive.writestr(archive_name, image)
                del image
                included.append(archive_name)
                sqlite_versions[archive_name] = int(version)

            if engine_settings.is_file():
                archive_name = f"config/{engine_settings.name}"
                archive.write(engine_settings, archive_name)
                included.append(archive_name)
            if legacy_app_settings.is_file():
                archive_name = f"config/{legacy_app_settings.name}"
                archive.write(legacy_app_settings, archive_name)
                included.append(archive_name)
            for source, archive_name in reference_files:
                archive.write(source, archive_name)
                included.append(archive_name)

            manifest = {
                "format": _FORMAT_V2,
                "app_version": APP_VERSION,
                "created_utc": datetime.now(timezone.utc).isoformat(),
                "source_midi_files_included": False,
                "engine_settings_included": f"config/{_ENGINE_SETTINGS}" in included,
                "sqlite_user_versions": sqlite_versions,
                "included": sorted(included),
            }
            archive.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True))
            included.append("manifest.json")

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


def _validated_archive_members(archive: zipfile.ZipFile) -> tuple[dict[str, object], tuple[zipfile.ZipInfo, ...]]:
    """Validate archive structure before any restore/verification extraction.

    Windows is deliberately treated as the strict path model even when this
    code is running elsewhere: case-insensitive duplicate names and a file that
    shadows a directory prefix are rejected because either can make restore
    results platform-dependent.
    """
    bad = archive.testzip()
    if bad:
        raise RuntimeError(f"CRC failure in {bad}")
    try:
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
    except (KeyError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Not a valid MIDI Library Intelligence backup.") from exc
    if not isinstance(manifest, dict) or manifest.get("format") not in _SUPPORTED_FORMATS:
        raise RuntimeError("Unsupported Library backup format.")

    infos: list[zipfile.ZipInfo] = []
    files: dict[str, PurePosixPath] = {}
    directories: set[str] = set()
    for info in archive.infolist():
        relative = _safe_archive_name(info.filename)
        normalized = relative.as_posix().rstrip("/")
        key = normalized.casefold()
        if not normalized:
            raise RuntimeError(f"Unsafe empty path in Library backup: {info.filename!r}")
        if key in files:
            raise RuntimeError(f"Duplicate/colliding path in Library backup: {info.filename!r}")
        if not info.is_dir() and key in directories:
            raise RuntimeError(f"File/directory path collision in Library backup: {info.filename!r}")
        parent_parts = relative.parts[:-1]
        for depth in range(1, len(parent_parts) + 1):
            parent_key = PurePosixPath(*parent_parts[:depth]).as_posix().casefold()
            if parent_key in files:
                raise RuntimeError(
                    f"File/directory path collision in Library backup: {info.filename!r}"
                )
            directories.add(parent_key)
        if info.is_dir():
            directories.add(key)
            continue
        # A file may not replace a directory namespace already required by a
        # previously seen nested member (for example ``library`` versus
        # ``library/discovery_graph.sqlite3``).
        if key in directories:
            raise RuntimeError(f"File/directory path collision in Library backup: {info.filename!r}")
        files[key] = relative
        infos.append(info)
    return manifest, tuple(infos)


def _verify_open_archive(archive: zipfile.ZipFile) -> tuple[dict[str, object], tuple[zipfile.ZipInfo, ...]]:
    """Verify manifest/CRC and SQLite images without a verification filesystem.

    REG-098 removed nested archive-path extraction, but target Windows then
    proved that even a flat temporary SQLite verification file could fail on
    reopen with ``ERROR_DIRECTORY`` after the exact archived bytes had already
    been written successfully.  Verification therefore has no on-disk SQLite
    stage at all: each ZIP member is loaded directly into an in-memory SQLite
    connection and receives the same ``PRAGMA integrity_check``.  Restore still
    extracts only *after* this verification succeeds.
    """
    manifest, infos = _validated_archive_members(archive)
    info_by_name = {_safe_archive_name(info.filename).as_posix().casefold(): info for info in infos}
    actual_versions: dict[str, int] = {}
    for filename in _SQLITE_FILES:
        archive_name = f"library/{filename}"
        info = info_by_name.get(archive_name.casefold())
        if info is None:
            continue
        image = archive.read(info)
        actual_versions[archive_name] = _sqlite_integrity_image(image, label=archive_name)

    declared = manifest.get("sqlite_user_versions") or {}
    if isinstance(declared, dict):
        for name, version in declared.items():
            if name in actual_versions and int(version) != actual_versions[name]:
                raise RuntimeError(
                    f"Library backup schema metadata mismatch for {name}: "
                    f"manifest {version}, database {actual_versions[name]}."
                )
    return manifest, infos


def _extract_verified_archive(source: Path, destination: Path) -> dict[str, object]:
    """Verify the archive first, then extract the exact verified members."""
    with zipfile.ZipFile(source, "r") as archive:
        manifest, infos = _verify_open_archive(archive)
        for info in infos:
            relative = _safe_archive_name(info.filename)
            target = destination.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info, "r") as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)
    return manifest


def verify_library_intelligence_backup(path: str | Path) -> dict[str, object]:
    """Validate ZIP, manifest, safe paths, and SQLite integrity without restoring."""
    source = Path(path).expanduser()
    if not source.is_file():
        raise RuntimeError(f"Library backup does not exist: {source}")
    with zipfile.ZipFile(source, "r") as archive:
        manifest, _infos = _verify_open_archive(archive)
        return manifest


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
