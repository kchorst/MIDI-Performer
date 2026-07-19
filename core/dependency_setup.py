from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import shutil
import subprocess
from typing import Callable, Iterable, Sequence

from core.omr_bridge import locate_omr_engine

AUDIVERIS_WINGET_ID = "audiveris.org.Audiveris"


@dataclass(frozen=True)
class ToolCheck:
    name: str
    found: bool
    path: str | None = None
    confirmed: bool = False
    version: str = ""
    detail: str = ""


@dataclass(frozen=True)
class InstallResult:
    ok: bool
    status: str
    detail: str = ""
    command: tuple[str, ...] = ()
    return_code: int | None = None


def _windows_program_roots() -> list[Path]:
    roots: list[Path] = []
    for name in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA", "ProgramData"):
        value = os.environ.get(name)
        if value:
            roots.append(Path(value))
    return roots


def find_fluidsynth(app_root: str | Path, configured_path: str | Path | None = None) -> str | None:
    if configured_path:
        configured = Path(configured_path).expanduser()
        if configured.is_file():
            return str(configured)
    root = Path(app_root)
    vendor_exe = root / "vendor" / "fluidsynth" / "bin" / "fluidsynth.exe"
    if vendor_exe.exists():
        return str(vendor_exe)
    vendor_root = root / "vendor" / "fluidsynth"
    if vendor_root.exists():
        matches = sorted(vendor_root.glob("**/fluidsynth.exe"))
        if matches:
            return str(matches[0])
    found = shutil.which("fluidsynth") or shutil.which("fluidsynth.exe")
    if found:
        return str(found)
    for base in _windows_program_roots():
        for rel in (
            Path("FluidSynth") / "bin" / "fluidsynth.exe",
            Path("FluidSynth") / "fluidsynth.exe",
            Path("fluidsynth") / "bin" / "fluidsynth.exe",
            Path("Programs") / "FluidSynth" / "bin" / "fluidsynth.exe",
            Path("Programs") / "FluidSynth" / "fluidsynth.exe",
        ):
            candidate = base / rel
            if candidate.is_file():
                return str(candidate)
    return None


def find_soundfont(
    app_root: str | Path,
    configured_path: str | Path | None = None,
    configured_directory: str | Path | None = None,
) -> str | None:
    """Resolve a usable .sf2/.sf3 file without requiring the user to move it."""
    candidates: list[Path] = []
    if configured_path:
        candidates.append(Path(configured_path).expanduser())
    if configured_directory:
        directory = Path(configured_directory).expanduser()
        if directory.is_dir():
            candidates.extend(sorted(directory.glob("*.sf2")))
            candidates.extend(sorted(directory.glob("*.sf3")))
    root = Path(app_root)
    for directory in (
        root / "assets" / "soundfonts",
        root / "soundfonts",
        root / "vendor" / "soundfonts",
    ):
        if directory.is_dir():
            candidates.extend(sorted(directory.glob("*.sf2")))
            candidates.extend(sorted(directory.glob("*.sf3")))
    env_sf = os.environ.get("MIDI_PERFORMER_SOUNDFONT") or os.environ.get("SOUNDFONT")
    if env_sf:
        candidates.insert(0, Path(env_sf).expanduser())
    for candidate in candidates:
        if candidate.is_file() and candidate.suffix.lower() in {".sf2", ".sf3"}:
            return str(candidate)
    return None


def check_soundfont(
    app_root: str | Path,
    configured_path: str | Path | None = None,
    configured_directory: str | Path | None = None,
) -> ToolCheck:
    path = find_soundfont(app_root, configured_path, configured_directory)
    if not path:
        return ToolCheck(
            name="SoundFont",
            found=False,
            detail="No .sf2 or .sf3 SoundFont was found. Assign an existing file or folder in Settings > External Tools and Resources.",
        )
    candidate = Path(path)
    try:
        size = candidate.stat().st_size
    except OSError as exc:
        return ToolCheck(name="SoundFont", found=False, path=str(candidate), detail=str(exc))
    if size < 1024:
        return ToolCheck(name="SoundFont", found=True, path=str(candidate), confirmed=False, detail="The selected SoundFont is unusually small and may be invalid.")
    return ToolCheck(name="SoundFont", found=True, path=str(candidate), confirmed=True, detail=f"Usable SoundFont: {candidate.name}")


def find_ffmpeg(app_root: str | Path) -> str | None:
    root = Path(app_root)
    vendor_exe = root / "vendor" / "ffmpeg" / "bin" / "ffmpeg.exe"
    if vendor_exe.exists():
        return str(vendor_exe)
    vendor_root = root / "vendor" / "ffmpeg"
    if vendor_root.exists():
        matches = sorted(vendor_root.glob("**/ffmpeg.exe"))
        if matches:
            return str(matches[0])
    found = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
    return str(found) if found else None


def _run_version(
    command: Sequence[str],
    *,
    timeout_seconds: int = 7,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> tuple[bool, str, str]:
    try:
        kwargs = {
            "capture_output": True,
            "text": True,
            "timeout": max(2, int(timeout_seconds)),
            "check": False,
        }
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            kwargs["startupinfo"] = startupinfo
        result = runner(list(command), **kwargs)
    except Exception as exc:  # noqa: BLE001
        return False, "", str(exc)
    output = "\n".join(part.strip() for part in [result.stdout or "", result.stderr or ""] if part and part.strip())
    first_line = output.splitlines()[0].strip() if output.splitlines() else "version command ran"
    return result.returncode == 0, first_line, output[-800:]


def check_fluidsynth(app_root: str | Path, configured_path: str | Path | None = None, *, runner: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> ToolCheck:
    path = find_fluidsynth(app_root, configured_path)
    if not path:
        return ToolCheck(
            name="FluidSynth",
            found=False,
            detail="Not found. Choose fluidsynth.exe in Settings > File Locations, put it in vendor\\fluidsynth\\bin, or install it on PATH.",
        )
    confirmed, version, detail = _run_version([str(path), "--version"], runner=runner)
    return ToolCheck(
        name="FluidSynth",
        found=True,
        path=str(path),
        confirmed=confirmed,
        version=version,
        detail=detail if not confirmed else f"Confirmed: {version}",
    )


def check_ffmpeg(app_root: str | Path, *, runner: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> ToolCheck:
    path = find_ffmpeg(app_root)
    if not path:
        return ToolCheck(
            name="FFmpeg",
            found=False,
            detail="Not found. Put ffmpeg.exe in vendor\\ffmpeg\\bin or install it on PATH.",
        )
    confirmed, version, detail = _run_version([str(path), "-version"], runner=runner)
    return ToolCheck(
        name="FFmpeg",
        found=True,
        path=str(path),
        confirmed=confirmed,
        version=version,
        detail=detail if not confirmed else f"Confirmed: {version}",
    )


def check_audiveris(
    configured_path: str | Path | None = None,
    *,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> ToolCheck:
    engine = locate_omr_engine(configured_path)
    if not engine.available:
        return ToolCheck(
            name="Audiveris",
            found=False,
            detail=engine.detail,
        )
    # Newer Audiveris installs support -version. If it opens a GUI or returns a
    # non-zero code, the engine can still be usable for OMR; mark found but not
    # fully confirmed so the UI can explain the distinction.
    confirmed, version, detail = _run_version([*engine.command, "-version"], runner=runner)
    return ToolCheck(
        name="Audiveris",
        found=True,
        path=" ".join(engine.command),
        confirmed=confirmed,
        version=version,
        detail=(f"Confirmed: {version}" if confirmed else f"Found, but version check did not confirm cleanly. {detail or engine.detail}"),
    )


def check_dependencies(
    app_root: str | Path,
    configured_audiveris_path: str | Path | None = None,
    configured_fluidsynth_path: str | Path | None = None,
    configured_soundfont_path: str | Path | None = None,
    configured_soundfont_directory: str | Path | None = None,
    *,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> dict[str, ToolCheck]:
    return {
        "fluidsynth": check_fluidsynth(app_root, configured_fluidsynth_path, runner=runner),
        "soundfont": check_soundfont(app_root, configured_soundfont_path, configured_soundfont_directory),
        "ffmpeg": check_ffmpeg(app_root, runner=runner),
        "audiveris": check_audiveris(configured_audiveris_path, runner=runner),
    }


def winget_path() -> str | None:
    found = shutil.which("winget") or shutil.which("winget.exe")
    return str(found) if found else None


def audiveris_winget_install_command() -> tuple[str, ...]:
    winget = winget_path() or "winget"
    return (
        winget,
        "install",
        "-e",
        "--id",
        AUDIVERIS_WINGET_ID,
        "--accept-source-agreements",
        "--accept-package-agreements",
    )


def install_audiveris_with_winget(
    *,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    timeout_seconds: int = 1200,
) -> InstallResult:
    if os.name != "nt":
        return InstallResult(
            False,
            "Manual install needed",
            "Automatic Audiveris install is currently Windows/winget only. Use the official GitHub releases or your OS package manager, then choose the executable in MIDI Performer.",
        )
    winget = winget_path()
    if not winget:
        return InstallResult(
            False,
            "winget not found",
            "Windows Package Manager was not found. Install Audiveris from the official GitHub release, then choose Audiveris.exe in MIDI Performer.",
        )
    command = audiveris_winget_install_command()
    try:
        completed = runner(
            list(command),
            capture_output=True,
            text=True,
            timeout=max(60, int(timeout_seconds)),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return InstallResult(False, "Install timed out", "Audiveris installation did not finish in time. It may still be running in Windows; check the installer window.", command=command)
    except Exception as exc:  # noqa: BLE001
        return InstallResult(False, "Install failed to start", str(exc), command=command)
    output = "\n".join(part.strip() for part in [completed.stdout or "", completed.stderr or ""] if part and part.strip())
    ok = completed.returncode == 0
    return InstallResult(
        ok,
        "Audiveris installed" if ok else "Audiveris install failed",
        output[-1600:] or ("winget returned success" if ok else "winget returned an error"),
        command=command,
        return_code=completed.returncode,
    )
