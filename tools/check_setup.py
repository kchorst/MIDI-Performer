from __future__ import annotations

from pathlib import Path
import importlib.util
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.app_settings import AppSettings  # noqa: E402
from core.dependency_setup import check_dependencies  # noqa: E402


def main() -> int:
    print("MIDI Performer setup check")
    print(f"Project: {ROOT}")

    ok = True

    pyfs_available = importlib.util.find_spec("fluidsynth") is not None
    if pyfs_available:
        print("[OK] pyFluidSynth Python module found for low-latency playback")
    else:
        print("[RECOMMENDED] pyFluidSynth module not found")
        print("              Run: python -m pip install -r requirements.txt")

    settings = AppSettings(ROOT)
    report = check_dependencies(ROOT, settings.get("audiveris_path"), settings.get("fluidsynth_path"), settings.get("last_soundfont_path"), settings.get("last_soundfont_directory"))

    fs = report["fluidsynth"]
    if fs.found and fs.confirmed:
        print(f"[OK] FluidSynth confirmed: {fs.path}")
        print(f"     {fs.version}")
    elif fs.found:
        print(f"[ATTENTION] FluidSynth found but version check did not confirm: {fs.path}")
        print(f"            {fs.detail}")
        ok = False
    else:
        print("[MISSING] FluidSynth not found")
        print("          In the app, choose Install FluidSynth or Use Existing")
        ok = False

    sf = report["soundfont"]
    if sf.found and sf.confirmed:
        print(f"[OK] Instrument library confirmed: {sf.path}")
    elif sf.found:
        print(f"[ATTENTION] Instrument library found but validation did not confirm: {sf.path}")
        print(f"            {sf.detail}")
        ok = False
    else:
        print("[MISSING] No instrument library found")
        print("          In the app, choose Install GeneralUser GS or Choose Existing")
        ok = False

    ffmpeg = report["ffmpeg"]
    if ffmpeg.found and ffmpeg.confirmed:
        print(f"[OK] FFmpeg confirmed for MP3 export: {ffmpeg.path}")
        print(f"     {ffmpeg.version}")
    elif ffmpeg.found:
        print(f"[ATTENTION] FFmpeg found but version check did not confirm: {ffmpeg.path}")
        print(f"            {ffmpeg.detail}")
    else:
        print("[OPTIONAL] FFmpeg not found. MP3 export will be disabled/fail until installed.")
        print("           MIDI and WAV export do not need FFmpeg.")

    audiveris = report["audiveris"]
    if audiveris.found and audiveris.confirmed:
        print(f"[OK] Audiveris confirmed for scanned PDF OMR: {audiveris.path}")
        print(f"     {audiveris.version}")
    elif audiveris.found:
        print(f"[ATTENTION] Audiveris found but version check did not confirm: {audiveris.path}")
        print(f"            {audiveris.detail}")
    else:
        print("[OPTIONAL] Audiveris not found. Scanned/image PDF score analysis needs Audiveris.")
        print("           In the app, use the optional Score Analysis controls in File Locations.")

    print("\nReady for playback/export." if ok else "\nSetup is not complete yet.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
