from __future__ import annotations

import os
from pathlib import Path
import sys
import threading
import time
import traceback


def _report_startup_failure(exc: BaseException) -> None:
    """Make pre-window startup failures visible even when launched with pythonw.

    Include executable/source provenance in both the dialog and log.  This is
    deliberately computed without importing the main application so a broken
    first-party dependency cannot hide which extracted build/shortcut Windows
    actually launched.
    """
    source_root = Path(__file__).resolve().parent
    version = "unknown"
    version_file = source_root / "core" / "remix_version.py"
    try:
        version_text = version_file.read_text(encoding="utf-8")
        import re
        match = re.search(r"^APP_VERSION\s*=\s*['\"]([^'\"]+)", version_text, flags=re.MULTILINE)
        if match:
            version = match.group(1)
    except (OSError, UnicodeError):
        version = "unknown"
    provenance = (
        f"Version: {version}\n"
        f"Launch folder: {source_root}\n"
        f"Python: {sys.executable}\n"
        f"Working directory: {Path.cwd()}"
    )
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "MIDI Remixer" / "logs"
    try:
        base.mkdir(parents=True, exist_ok=True)
        log_path = base / "startup-error.log"
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write("\n=== MIDI Remixer startup failure ===\n")
            handle.write(provenance + "\n")
            handle.write("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
    except OSError:
        log_path = None
    message = f"MIDI Remixer could not start.\n\n{type(exc).__name__}: {exc}\n\n{provenance}"
    if log_path is not None:
        message += f"\n\nDetails were written to:\n{log_path}"
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, message, "MIDI Remixer Startup Error", 0x10)
            return
        except (AttributeError, OSError) as dialog_exc:
            message += f"\n\nWindows startup dialog was unavailable: {type(dialog_exc).__name__}."
    print(message)


def _ensure_library_engine_started() -> None:
    """Warm-start an *existing* authoritative Library without creating the first one.

    Contract 4 makes MIDI Library Manager the owner of first-Library creation and
    migration. Remixer may restart the Engine only when the authoritative schema
    database set already exists. If no Library exists yet, startup continues to
    the normal Remixer window in disconnected mode so the user can open Library
    Manager; no Engine process and no empty Library database are manufactured.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QProgressDialog

    from library_engine.client import LibraryEngineClient, LibraryEngineUnavailable

    root = Path(__file__).resolve().parent
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("MIDI Remixer")
    app.setOrganizationName("MIDI Remixer")
    # Closing the transient startup surface must not terminate the QApplication
    # before the real Remixer window has been constructed.
    app.setQuitOnLastWindowClosed(False)
    client = LibraryEngineClient(
        root,
        required_capabilities={
            "catalogue_deltas", "prepared_recommendations", "related_candidates",
            "read_committed_library", "storage_contract", "persisted_lifecycle_eligibility",
        },
    )

    # A live Engine is never replaced by the Remixer bootstrap. The
    # Remixer-only attachment validates compatibility without issuing shutdown;
    # this prevents an older/newer independent install from being killed merely
    # because Remixer launched second.
    if client.ping(timeout=0.35) is not None:
        return

    library_root = client.paths.library
    established = all(
        (library_root / filename).is_file()
        for filename in (
            "library_scan.sqlite3",
            "library_index.sqlite3",
            "library_relationships.sqlite3",
        )
    )
    if not established:
        return

    state: dict[str, object] = {}

    def connect() -> None:
        try:
            # First establish the lightweight heartbeat. The Engine deliberately
            # publishes IPC before opening/migrating a large persistent library,
            # so a 96K catalogue cannot be mistaken for a dead process.
            state["phase"] = "Starting or attaching to the Library Engine…"
            client.ensure_running(timeout=30.0, require_service_ready=False)
            state["phase"] = "Opening the saved MIDI library…"
            deadline = time.monotonic() + 180.0
            last_phase = "opening persistent library"
            while time.monotonic() < deadline:
                ping = client.ping(timeout=0.75)
                if ping is not None and bool(ping.get("service_ready", True)):
                    state["result"] = ping
                    return
                if ping is not None:
                    try:
                        status = client.status(start_if_needed=False, timeout=2.0)
                    except (LibraryEngineUnavailable, OSError, RuntimeError):
                        status = {}
                    startup = status.get("startup") if isinstance(status, dict) else None
                    if isinstance(startup, dict):
                        last_phase = str(startup.get("state") or last_phase)
                    state["phase"] = f"Library Engine is {last_phase}…"
                else:
                    state["phase"] = "Waiting for the Library Engine heartbeat…"
                time.sleep(0.15)
            raise LibraryEngineUnavailable(
                "MIDI Library Engine is alive but did not finish opening the saved library within 180 seconds. "
                f"Last startup phase: {last_phase}."
            )
        except (LibraryEngineUnavailable, OSError, RuntimeError) as exc:
            # Startup connection failures are transferred back to the GUI thread
            # where the normal startup reporter can make them visible.
            state["error"] = exc

    worker = threading.Thread(target=connect, name="MidiRemixerEngineBootstrap", daemon=False)
    worker.start()
    started = time.monotonic()
    dialog: QProgressDialog | None = None
    while worker.is_alive():
        elapsed = time.monotonic() - started
        if dialog is None and elapsed >= 0.35:
            dialog = QProgressDialog(
                "Starting MIDI Library Engine…\n\n"
                "Connecting to your saved MIDI library. Source MIDI files are not being rescanned.",
                "", 0, 0, None,
            )
            dialog.setWindowTitle("MIDI Remixer")
            dialog.setCancelButton(None)
            dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
            dialog.setMinimumDuration(0)
            dialog.setMinimumWidth(500)
            dialog.show()
        if dialog is not None:
            phase = str(state.get("phase") or "Connecting to your saved MIDI library…")
            dialog.setLabelText(
                "Starting MIDI Library Engine…\n\n"
                + phase
                + "\nSource MIDI files are not being rescanned just to open Remixer."
            )
        app.processEvents()
        time.sleep(0.025)
    worker.join(timeout=0.1)
    app.processEvents()
    if dialog is not None:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()
    error = state.get("error")
    if isinstance(error, BaseException):
        raise error


def _run() -> int:
    try:
        _ensure_library_engine_started()
        from app.decision_workflow import main
        return int(main())
    except SystemExit as exc:
        return int(exc.code or 0)
    except BaseException as exc:
        _report_startup_failure(exc)
        return 1


raise SystemExit(_run())
