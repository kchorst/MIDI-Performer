from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import shutil
import tempfile
import urllib.request
import zipfile
from typing import Callable

FLUIDSYNTH_RELEASE_API = "https://api.github.com/repos/FluidSynth/fluidsynth/releases/latest"
GENERALUSER_SF2_URL = "https://raw.githubusercontent.com/mrbumpy409/GeneralUser-GS/main/GeneralUser-GS.sf2"
GENERALUSER_LICENSE_URL = "https://raw.githubusercontent.com/mrbumpy409/GeneralUser-GS/main/documentation/LICENSE.txt"


@dataclass(frozen=True)
class AudioSetupResult:
    ok: bool
    fluidsynth_path: str | None = None
    soundfont_path: str | None = None
    detail: str = ""


def _request(url: str):
    return urllib.request.Request(url, headers={"User-Agent": "MIDI-Performer/0.7.0 setup"})


def _download(url: str, target: Path, progress: Callable[[str, int], None] | None = None) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(_request(url), timeout=120) as response:  # noqa: S310 - fixed HTTPS sources
        total = int(response.headers.get("Content-Length", "0") or 0)
        received = 0
        with target.open("wb") as out:
            while True:
                chunk = response.read(1024 * 512)
                if not chunk:
                    break
                out.write(chunk)
                received += len(chunk)
                if progress:
                    pct = int(received * 100 / total) if total else 0
                    progress(target.name, max(0, min(100, pct)))


def _release_asset(api_url: str, predicate) -> tuple[str, str]:
    with urllib.request.urlopen(_request(api_url), timeout=30) as response:  # noqa: S310
        payload = json.loads(response.read().decode("utf-8"))
    choices = []
    for asset in payload.get("assets") or []:
        name = str(asset.get("name") or "")
        url = str(asset.get("browser_download_url") or "")
        score = predicate(name.lower()) if url else None
        if score is not None:
            choices.append((int(score), name, url))
    if not choices:
        raise RuntimeError("The official release did not contain a supported download package.")
    choices.sort(reverse=True)
    _, name, url = choices[0]
    return name, url


def _latest_windows_asset() -> tuple[str, str]:
    def score(low: str):
        if not low.endswith(".zip") or "win" not in low or any(x in low for x in ("android", "source", "debug", "symbols")):
            return None
        value = 0
        if any(x in low for x in ("win64", "x64", "amd64")):
            value += 20
        if "mingw" in low:
            value += 2
        if "glib" not in low:
            value += 1
        return value
    return _release_asset(FLUIDSYNTH_RELEASE_API, score)


def _safe_extract(zip_path: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            target = (destination / member.filename).resolve()
            if root != target and root not in target.parents:
                raise RuntimeError("Unsafe path found in downloaded archive.")
        archive.extractall(destination)


def install_fluidsynth(app_root: str | Path, progress: Callable[[str, int], None] | None = None) -> AudioSetupResult:
    root = Path(app_root).resolve()
    fs_root = root / "vendor" / "fluidsynth"
    try:
        existing = next(iter(sorted(fs_root.glob("**/fluidsynth.exe"))), None) if fs_root.exists() else None
        if existing is None:
            asset_name, asset_url = _latest_windows_asset()
            if progress:
                progress("Finding FluidSynth", 0)
            with tempfile.TemporaryDirectory(prefix="midi-performer-fluidsynth-") as temp_name:
                temp = Path(temp_name)
                archive_path = temp / asset_name
                _download(asset_url, archive_path, progress)
                unpacked = temp / "unpacked"
                _safe_extract(archive_path, unpacked)
                if not list(unpacked.glob("**/fluidsynth.exe")):
                    raise RuntimeError("FluidSynth downloaded, but fluidsynth.exe was not found inside the package.")
                if fs_root.exists():
                    shutil.rmtree(fs_root)
                fs_root.mkdir(parents=True, exist_ok=True)
                for item in unpacked.iterdir():
                    shutil.move(str(item), str(fs_root / item.name))
                existing = next(iter(sorted(fs_root.glob("**/fluidsynth.exe"))), None)
        if existing is None:
            raise RuntimeError("FluidSynth setup completed without a usable executable.")
        if progress:
            progress("FluidSynth ready", 100)
        return AudioSetupResult(True, fluidsynth_path=str(existing), detail="FluidSynth is ready.")
    except Exception as exc:  # noqa: BLE001
        return AudioSetupResult(False, detail=str(exc))


def install_recommended_soundfont(app_root: str | Path, progress: Callable[[str, int], None] | None = None) -> AudioSetupResult:
    root = Path(app_root).resolve()
    sf_root = root / "assets" / "soundfonts"
    sf_root.mkdir(parents=True, exist_ok=True)
    try:
        existing = next(iter(sorted(sf_root.glob("GeneralUser*.sf2"))), None)
        if existing is None:
            if progress:
                progress("Downloading GeneralUser GS", 0)
            existing = sf_root / "GeneralUser-GS.sf2"
            _download(GENERALUSER_SF2_URL, existing, progress)
            try:
                _download(GENERALUSER_LICENSE_URL, sf_root / "GeneralUser-GS-LICENSE.txt", progress)
            except Exception:
                pass
            (sf_root / "GeneralUser-GS-SOURCE.txt").write_text(
                "GeneralUser GS downloaded by MIDI Performer from the official mrbumpy409/GeneralUser-GS GitHub repository.\n",
                encoding="utf-8",
            )
        if not existing.exists() or existing.stat().st_size < 1024:
            raise RuntimeError("Recommended instrument library setup completed without a usable SoundFont.")
        if progress:
            progress("GeneralUser GS ready", 100)
        return AudioSetupResult(True, soundfont_path=str(existing), detail="GeneralUser GS is ready.")
    except Exception as exc:  # noqa: BLE001
        return AudioSetupResult(False, detail=str(exc))


def install_audio_dependencies(app_root: str | Path, progress: Callable[[str, int], None] | None = None) -> AudioSetupResult:
    """Compatibility helper that installs both playback components."""
    fs = install_fluidsynth(app_root, progress)
    if not fs.ok:
        return fs
    sf = install_recommended_soundfont(app_root, progress)
    if not sf.ok:
        return AudioSetupResult(False, fluidsynth_path=fs.fluidsynth_path, detail=sf.detail)
    return AudioSetupResult(True, fs.fluidsynth_path, sf.soundfont_path, "FluidSynth and GeneralUser GS are ready.")
