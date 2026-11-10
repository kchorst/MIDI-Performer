from __future__ import annotations

"""Bounded installation of optional audio dependencies.

Automatic network installation is available only for immutable artifacts pinned
by this release with an HTTPS URL and SHA-256 digest. If no artifact is pinned,
the user must choose an existing FluidSynth executable or SoundFont.
"""

from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
import threading
import urllib.error
import urllib.request
import zipfile
from typing import Callable

from core.app_paths import AppPaths
from core.atomic_io import atomic_write_text, safe_unlink
from core.remix_version import APP_VERSION

MAX_DOWNLOAD_BYTES = 250 * 1024 * 1024
MAX_ARCHIVE_FILES = 4000
MAX_ARCHIVE_EXPANDED_BYTES = 700 * 1024 * 1024


@dataclass(frozen=True)
class AudioSetupResult:
    ok: bool
    fluidsynth_path: str | None = None
    soundfont_path: str | None = None
    detail: str = ""


@dataclass(frozen=True)
class ApprovedArtifact:
    name: str
    version: str
    url: str
    sha256: str
    max_bytes: int
    license_url: str = ""
    source: str = "release manifest"


def _request(url: str):
    return urllib.request.Request(
        url,
        headers={
            "User-Agent": f"MIDI-Remixer/{APP_VERSION} setup",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )


def _artifact_manifest(app_root: Path) -> dict[str, object]:
    path = app_root / "data" / "approved_audio_artifacts.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _valid_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value.casefold())


def _approved_artifact(app_root: Path, key: str) -> ApprovedArtifact | None:
    raw = _artifact_manifest(app_root).get(key)
    if not isinstance(raw, dict):
        return None
    try:
        artifact = ApprovedArtifact(
            name=str(raw["name"]),
            version=str(raw["version"]),
            url=str(raw["url"]),
            sha256=str(raw["sha256"]).casefold(),
            max_bytes=min(MAX_DOWNLOAD_BYTES, int(raw.get("max_bytes") or MAX_DOWNLOAD_BYTES)),
            license_url=str(raw.get("license_url") or ""),
        )
    except (KeyError, TypeError, ValueError):
        return None
    if not artifact.url.startswith("https://") or not _valid_sha256(artifact.sha256):
        return None
    return artifact


def automatic_install_available(app_root: str | Path, key: str = "fluidsynth_windows_x64") -> bool:
    """Return whether the application can offer one-click setup.

    One-click setup is offered only when this exact release pins an immutable
    artifact URL and SHA-256 digest in ``approved_audio_artifacts.json``.
    """

    return _approved_artifact(Path(app_root).resolve(), key) is not None


def _check_cancelled(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise RuntimeError("Audio dependency setup was cancelled.")


def _download_artifact(
    artifact: ApprovedArtifact,
    target: Path,
    progress: Callable[[str, int], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> str:
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".partial")
    safe_unlink(partial)
    digest = hashlib.sha256()
    received = 0
    try:
        _check_cancelled(cancel_event)
        with urllib.request.urlopen(_request(artifact.url), timeout=60) as response:  # noqa: S310 - release-pinned HTTPS
            declared = int(response.headers.get("Content-Length", "0") or 0)
            if declared and declared > artifact.max_bytes:
                raise RuntimeError(f"Download exceeds the approved {artifact.max_bytes // (1024 * 1024)} MiB limit.")
            with partial.open("wb") as out:
                while True:
                    _check_cancelled(cancel_event)
                    chunk = response.read(512 * 1024)
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > artifact.max_bytes:
                        raise RuntimeError("Download exceeded its approved size limit.")
                    digest.update(chunk)
                    out.write(chunk)
                    if progress:
                        pct = int(received * 100 / declared) if declared else 0
                        progress(artifact.name, max(0, min(99, pct)))
                out.flush()
                os.fsync(out.fileno())
        _check_cancelled(cancel_event)
        actual = digest.hexdigest().casefold()
        if actual != artifact.sha256:
            raise RuntimeError(
                "Downloaded artifact identity did not match its release-pinned SHA-256 digest "
                f"(expected {artifact.sha256}, got {actual})."
            )
        os.replace(partial, target)
        return actual
    finally:
        safe_unlink(partial)


def _download_verified(
    artifact: ApprovedArtifact,
    target: Path,
    progress: Callable[[str, int], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> str:
    """Backward-compatible name for the bounded artifact downloader."""

    return _download_artifact(artifact, target, progress, cancel_event)


def _safe_extract(
    zip_path: Path,
    destination: Path,
    cancel_event: threading.Event | None = None,
) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with zipfile.ZipFile(zip_path) as archive:
        members = archive.infolist()
        if len(members) > MAX_ARCHIVE_FILES:
            raise RuntimeError("Downloaded archive contains too many files.")
        expanded = sum(max(0, int(member.file_size)) for member in members)
        if expanded > MAX_ARCHIVE_EXPANDED_BYTES:
            raise RuntimeError("Downloaded archive exceeds the approved expanded-size limit.")
        for member in members:
            _check_cancelled(cancel_event)
            target = (destination / member.filename).resolve()
            if root != target and root not in target.parents:
                raise RuntimeError("Unsafe path found in downloaded archive.")
            mode = member.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise RuntimeError("Symbolic links are not allowed in downloaded archives.")
        for member in members:
            _check_cancelled(cancel_event)
            archive.extract(member, destination)


def _select_fluidsynth_executable(root: Path) -> Path | None:
    executables = sorted(root.glob("**/fluidsynth.exe"), key=lambda path: ("bin" not in path.parts, len(path.parts)))
    return executables[0] if executables else None


def _validate_fluidsynth(executable: Path) -> str:
    if not executable.is_file():
        raise RuntimeError("FluidSynth setup did not produce fluidsynth.exe.")
    if os.name != "nt":
        return "executable present"
    kwargs: dict[str, object] = {
        "cwd": str(executable.parent),
        "capture_output": True,
        "text": True,
        "timeout": 12,
        "check": False,
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
    }
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    kwargs["startupinfo"] = startupinfo
    try:
        result = subprocess.run([str(executable), "--version"], **kwargs)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"Downloaded fluidsynth.exe could not start: {exc}") from exc
    output = "\n".join(part.strip() for part in (result.stdout or "", result.stderr or "") if part and part.strip())
    if result.returncode != 0 or "fluidsynth" not in output.casefold():
        raise RuntimeError(
            "Downloaded fluidsynth.exe did not pass its version check. "
            + (output[-600:] if output else f"Exit code: {result.returncode}")
        )
    return output.splitlines()[0] if output.splitlines() else "FluidSynth"


def install_fluidsynth(
    app_root: str | Path,
    progress: Callable[[str, int], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> AudioSetupResult:
    root = Path(app_root).resolve()
    paths = AppPaths.for_app(root).ensure()
    fs_root = paths.dependencies / "fluidsynth"
    existing = _select_fluidsynth_executable(fs_root) if fs_root.exists() else None
    if existing is not None:
        return AudioSetupResult(True, fluidsynth_path=str(existing), detail="Managed FluidSynth is ready.")

    artifact = _approved_artifact(root, "fluidsynth_windows_x64")
    try:
        if artifact is None:
            return AudioSetupResult(
                False,
                detail=(
                    "Automatic FluidSynth setup is disabled for this release because no immutable, SHA-256-pinned "
                    "Windows artifact is approved. Choose an existing fluidsynth.exe in Audio Setup."
                ),
            )
        if progress:
            progress(f"Downloading {artifact.name} {artifact.version}", 1)
        with tempfile.TemporaryDirectory(prefix="midi-remixer-fluidsynth-") as temp_name:
            temp = Path(temp_name)
            archive_path = temp / artifact.name
            actual_hash = _download_artifact(artifact, archive_path, progress, cancel_event)
            unpacked = temp / "unpacked"
            _safe_extract(archive_path, unpacked, cancel_event)
            unpacked_executable = _select_fluidsynth_executable(unpacked)
            if unpacked_executable is None:
                raise RuntimeError("The FluidSynth package did not contain fluidsynth.exe.")
            version_line = _validate_fluidsynth(unpacked_executable)

            staging = paths.dependencies / ".fluidsynth-staging"
            backup = paths.dependencies / ".fluidsynth-backup"
            shutil.rmtree(staging, ignore_errors=True)
            shutil.rmtree(backup, ignore_errors=True)
            shutil.copytree(unpacked, staging)
            staged_executable = _select_fluidsynth_executable(staging)
            if staged_executable is None:
                raise RuntimeError("FluidSynth staging did not contain fluidsynth.exe.")
            _validate_fluidsynth(staged_executable)
            if fs_root.exists():
                os.replace(fs_root, backup)
            try:
                os.replace(staging, fs_root)
            except Exception:
                if backup.exists() and not fs_root.exists():
                    os.replace(backup, fs_root)
                raise
            finally:
                shutil.rmtree(staging, ignore_errors=True)
                shutil.rmtree(backup, ignore_errors=True)

        existing = _select_fluidsynth_executable(fs_root)
        if existing is None:
            raise RuntimeError("FluidSynth setup completed without a usable executable.")
        atomic_write_text(
            fs_root / "MIDI-REMIXER-INSTALL-SOURCE.txt",
            "\n".join(
                (
                    f"Source: {artifact.source}",
                    f"Version: {artifact.version}",
                    f"Asset: {artifact.name}",
                    f"URL: {artifact.url}",
                    f"SHA-256: {actual_hash}",
                    f"Published digest verified: {'yes' if artifact.sha256 else 'not supplied by API'}",
                    f"Executable check: {version_line}",
                    "",
                )
            ),
        )
        if progress:
            progress("FluidSynth ready", 100)
        return AudioSetupResult(True, fluidsynth_path=str(existing), detail=f"FluidSynth {artifact.version} is ready.")
    except (
        OSError,
        RuntimeError,
        urllib.error.URLError,
        zipfile.BadZipFile,
        subprocess.SubprocessError,
    ) as exc:
        return AudioSetupResult(False, detail=str(exc))


def install_recommended_soundfont(
    app_root: str | Path,
    progress: Callable[[str, int], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> AudioSetupResult:
    root = Path(app_root).resolve()
    paths = AppPaths.for_app(root).ensure()
    existing = next(iter(sorted(paths.soundfonts.glob("*.sf2"))), None)
    if existing is not None:
        return AudioSetupResult(True, soundfont_path=str(existing), detail="Managed SoundFont is ready.")
    artifact = _approved_artifact(root, "recommended_soundfont")
    if artifact is None:
        return AudioSetupResult(
            False,
            detail="Automatic SoundFont download is disabled until this release pins an immutable URL and SHA-256 digest.",
        )
    try:
        target = paths.soundfonts / artifact.name
        _download_artifact(artifact, target, progress, cancel_event)
        if target.stat().st_size < 1024:
            raise RuntimeError("Verified SoundFont is unexpectedly small.")
        atomic_write_text(
            paths.soundfonts / f"{target.stem}-SOURCE.txt",
            f"Name: {artifact.name}\nVersion: {artifact.version}\nURL: {artifact.url}\nSHA-256: {artifact.sha256}\n",
        )
        if progress:
            progress("Instrument library ready", 100)
        return AudioSetupResult(True, soundfont_path=str(target), detail=f"{artifact.name} is ready.")
    except (OSError, RuntimeError, urllib.error.URLError) as exc:
        return AudioSetupResult(False, detail=str(exc))


def install_audio_dependencies(
    app_root: str | Path,
    progress: Callable[[str, int], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> AudioSetupResult:
    fs = install_fluidsynth(app_root, progress, cancel_event)
    if not fs.ok:
        return fs
    sf = install_recommended_soundfont(app_root, progress, cancel_event)
    if not sf.ok:
        return AudioSetupResult(False, fluidsynth_path=fs.fluidsynth_path, detail=sf.detail)
    return AudioSetupResult(True, fs.fluidsynth_path, sf.soundfont_path, "Verified audio dependencies are ready.")
