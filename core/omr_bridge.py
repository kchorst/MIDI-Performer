from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import shutil
import subprocess
import time
import zipfile
from typing import Iterable


@dataclass(frozen=True)
class OmrEngineStatus:
    """Detected OMR engine state.

    MIDI Performer keeps OMR as an implementation detail: the user attaches a PDF
    and clicks Analyze Score. Today the practical bridge is Audiveris, which can
    be invoked from the command line when installed or explicitly selected.
    """

    available: bool
    label: str = "Audiveris"
    command: tuple[str, ...] = ()
    detail: str = ""


@dataclass(frozen=True)
class OmrRunResult:
    ok: bool
    engine: str
    status: str
    summary: str
    detail: str = ""
    output_path: str | None = None
    return_code: int | None = None
    command: tuple[str, ...] = ()


AUDIVERIS_EXECUTABLE_NAMES = ("audiveris", "audiveris.exe", "Audiveris.exe", "audiveris.bat", "audiveris.cmd")


def locate_omr_engine(configured_path: str | Path | None = None) -> OmrEngineStatus:
    """Find an Audiveris command the app can run.

    The configured path may be an executable, a directory containing an
    executable, or an Audiveris .jar file. If nothing is configured, PATH is used.
    """

    configured = str(configured_path or "").strip()
    if configured:
        status = _command_from_configured_path(Path(configured).expanduser())
        if status.available:
            return status
        return status

    for name in AUDIVERIS_EXECUTABLE_NAMES:
        resolved = shutil.which(name)
        if resolved:
            return OmrEngineStatus(
                available=True,
                command=(resolved,),
                detail=f"Found Audiveris on PATH: {resolved}",
            )

    for candidate in _common_audiveris_paths():
        if candidate.exists() and candidate.is_file():
            return OmrEngineStatus(
                available=True,
                command=(str(candidate),),
                detail=f"Found Audiveris install: {candidate}",
            )

    return OmrEngineStatus(
        available=False,
        detail=(
            "Audiveris was not found. Install Audiveris or choose its executable/JAR "
            "in Settings → File Locations. Analyze Score will keep the PDF attached, "
            "but scanned/image-only scores need this OMR engine."
        ),
    )


def run_pdf_omr(
    pdf_path: str | Path,
    output_root: str | Path,
    *,
    configured_path: str | Path | None = None,
    timeout_seconds: int = 300,
) -> OmrRunResult:
    """Run the external OMR bridge and return the generated symbolic score file.

    Audiveris exports MusicXML, often as compressed .mxl. The generated file is
    internal to MIDI Performer; the user does not have to find or import XML.
    """

    pdf = Path(pdf_path)
    if not pdf.exists():
        return OmrRunResult(False, "Audiveris", "PDF missing", f"Could not find {pdf}")
    engine = locate_omr_engine(configured_path)
    if not engine.available:
        return OmrRunResult(False, engine.label, "OMR engine not configured", engine.detail, command=engine.command)

    out_root = Path(output_root)
    out_root.mkdir(parents=True, exist_ok=True)
    before = _score_outputs(out_root)
    command = (*engine.command, "-batch", "-transcribe", "-export", "-output", str(out_root), "--", str(pdf))
    try:
        completed = subprocess.run(
            list(command),
            cwd=str(pdf.parent),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=max(15, int(timeout_seconds)),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return OmrRunResult(
            False,
            engine.label,
            "OMR timed out",
            "The PDF is still attached, but Audiveris did not finish within the time limit.",
            command=command,
        )
    except Exception as exc:
        return OmrRunResult(False, engine.label, "OMR failed to start", str(exc), command=command)

    after = _score_outputs(out_root)
    candidates = [p for p in after if p not in before]
    if not candidates:
        # Some Audiveris configurations reuse the same output file path. Fall
        # back to all output candidates, prioritizing newest score files.
        candidates = after
    output = _best_output(candidates, pdf.stem)
    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()
    tail = "\n".join(part for part in [stdout[-800:], stderr[-800:]] if part)

    if output and completed.returncode == 0:
        return OmrRunResult(
            True,
            engine.label,
            "OMR complete",
            f"Audiveris created {output.name}.",
            output_path=str(output),
            return_code=completed.returncode,
            command=command,
        )
    if output:
        return OmrRunResult(
            True,
            engine.label,
            "OMR output found with warnings",
            f"Audiveris returned code {completed.returncode}, but a score output was found: {output.name}." + (f"\n{tail}" if tail else ""),
            output_path=str(output),
            return_code=completed.returncode,
            command=command,
        )
    return OmrRunResult(
        False,
        engine.label,
        "OMR produced no score output",
        f"Audiveris returned code {completed.returncode} but no .mxl/.musicxml/.xml output was found." + (f"\n{tail}" if tail else ""),
        return_code=completed.returncode,
        command=command,
    )


def _common_audiveris_paths() -> list[Path]:
    candidates: list[Path] = []
    for env_name in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = os.environ.get(env_name)
        if not base:
            continue
        root = Path(base)
        for rel in (
            Path("Audiveris") / "Audiveris.exe",
            Path("Audiveris") / "bin" / "Audiveris.exe",
            Path("Programs") / "Audiveris" / "Audiveris.exe",
        ):
            candidates.append(root / rel)
    return candidates


def _command_from_configured_path(path: Path) -> OmrEngineStatus:
    if path.suffix.lower() == ".jar":
        if not path.exists():
            return OmrEngineStatus(False, detail=f"Configured Audiveris JAR does not exist: {path}")
        java = shutil.which("java")
        if not java:
            return OmrEngineStatus(False, detail="Configured Audiveris JAR needs Java, but java was not found on PATH.")
        return OmrEngineStatus(True, command=(java, "-jar", str(path)), detail=f"Using Audiveris JAR: {path}")

    candidates: list[Path] = []
    if path.is_dir():
        for name in AUDIVERIS_EXECUTABLE_NAMES:
            candidates.append(path / name)
            candidates.append(path / "bin" / name)
    else:
        candidates.append(path)
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return OmrEngineStatus(True, command=(str(candidate),), detail=f"Using Audiveris: {candidate}")
    return OmrEngineStatus(False, detail=f"Configured Audiveris path was not usable: {path}")


def _score_outputs(root: Path) -> set[Path]:
    if not root.exists():
        return set()
    return {p.resolve() for p in root.rglob("*") if p.is_file() and _is_symbolic_score_output(p)}


def _is_symbolic_score_output(path: Path) -> bool:
    suffix = path.suffix.lower()
    if suffix in {".musicxml", ".mxl"}:
        return True
    if suffix == ".xml":
        return _looks_like_musicxml(path)
    return False


def _looks_like_musicxml(path: Path) -> bool:
    try:
        head = path.read_text(encoding="utf-8", errors="ignore")[:4096].lower()
        return "score-partwise" in head or "score-timewise" in head or "musicxml" in head
    except Exception:
        return False


def _best_output(candidates: Iterable[Path], source_stem: str) -> Path | None:
    scored: list[tuple[int, float, Path]] = []
    wanted = source_stem.lower()
    for path in candidates:
        try:
            stat = path.stat()
        except OSError:
            continue
        name = path.stem.lower()
        score = 0
        if path.suffix.lower() == ".mxl":
            score += 20
        if wanted and wanted in name:
            score += 10
        if _is_valid_mxl(path):
            score += 4
        scored.append((score, stat.st_mtime, path))
    if not scored:
        return None
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return scored[0][2]


def _is_valid_mxl(path: Path) -> bool:
    if path.suffix.lower() != ".mxl":
        return False
    try:
        with zipfile.ZipFile(path, "r") as zf:
            return any(name.lower().endswith((".musicxml", ".xml")) for name in zf.namelist())
    except Exception:
        return False
