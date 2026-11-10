from __future__ import annotations

"""Deterministic, retryable application shutdown coordination.

The coordinator is deliberately separate from the main window.  Each phase is
recorded only after it succeeds, so a partial shutdown can be called again and
will retry the unfinished phases instead of falsely reporting completion.
"""

from dataclasses import dataclass
import gc
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable

from app.deferred_actions import cancel_owned
from core.logging_config import append_bounded
from core.runtime_cleanup import CleanupReport, runtime_supervisor

if TYPE_CHECKING:
    from app.studio_window import MidiRemixerStudio


@dataclass(frozen=True)
class ShutdownResult:
    complete: bool
    completed_phases: tuple[str, ...]
    failed_phases: tuple[str, ...]
    cleanup_report: CleanupReport | None


class ShutdownCoordinator:
    """Run owned-resource cleanup in explicit, retryable phases."""

    _ORDER = (
        "callbacks",
        "library_loader",
        "dialogs",
        "settings",
        "detailed_analysis",
        "library_index",
        "session",
        "runtime_supervisor",
        "filesystem_cleanup",
        "garbage_collection",
    )

    def __init__(self, studio: "MidiRemixerStudio") -> None:
        self.studio = studio
        self.state = "not_started"
        self.completed: set[str] = set()
        self.failures: dict[str, str] = {}
        self.last_report: CleanupReport | None = None

    def run(self) -> ShutdownResult:
        if self.state == "complete":
            return self.result()
        if self.state == "in_progress":
            return self.result(extra_failure="shutdown already in progress")

        self.state = "in_progress"
        runtime_supervisor.request_shutdown()
        phase_functions: dict[str, Callable[[], None]] = {
            "callbacks": self._stop_callbacks,
            "library_loader": self._stop_library_loader,
            "dialogs": self._close_dialogs,
            "settings": self._flush_settings,
            "detailed_analysis": self._stop_detailed_analysis,
            "library_index": self._close_library_index,
            "session": self._close_session,
            "runtime_supervisor": self._cleanup_runtime,
            "filesystem_cleanup": self._cleanup_filesystem,
            "garbage_collection": self._collect_garbage,
        }

        for name in self._ORDER:
            if name in self.completed:
                continue
            try:
                phase_functions[name]()
            except Exception as exc:
                self.failures[name] = f"{type(exc).__name__}: {exc}"
                self.studio._write_ui_error_log(f"Shutdown phase: {name}", exc)
            else:
                self.completed.add(name)
                self.failures.pop(name, None)

        complete = len(self.completed) == len(self._ORDER) and not self.failures
        self.state = "complete" if complete else "partial"
        self._write_report(complete)
        return self.result()

    def result(self, *, extra_failure: str | None = None) -> ShutdownResult:
        failed = dict(self.failures)
        if extra_failure:
            failed["coordinator"] = extra_failure
        return ShutdownResult(
            complete=self.state == "complete" and not failed,
            completed_phases=tuple(name for name in self._ORDER if name in self.completed),
            failed_phases=tuple(f"{name}: {detail}" for name, detail in sorted(failed.items())),
            cleanup_report=self.last_report,
        )

    def _stop_callbacks(self) -> None:
        studio = self.studio
        cancel_owned(studio)
        timers = [
            studio.poll_timer, studio.autosave_timer,
            studio.library_refresh_debounce, studio.library_reconcile_timer,
        ]
        startup_heartbeat = getattr(studio, "_library_startup_heartbeat_timer", None)
        if startup_heartbeat is not None:
            timers.append(startup_heartbeat)
        for timer in timers:
            timer.stop()
            timer.blockSignals(True)
        paths = studio.folder_watcher.directories() + studio.folder_watcher.files()
        if paths:
            studio.folder_watcher.removePaths(paths)
        studio.folder_watcher.blockSignals(True)

    def _stop_library_loader(self) -> None:
        studio = self.studio
        # Split architecture: Remixer never owns the Library Engine scan. Closing
        # the creative client must not cancel catalogue work that Library Manager
        # intentionally left running. Only Remixer-owned loader/bridge threads
        # are joined here; an explicit Stop/Cancel action is handled separately.
        remaining: list[str] = []
        for attribute in ("_library_load_thread",):
            thread = getattr(studio, attribute, None)
            if thread is None:
                continue
            if thread.is_alive():
                thread.join(timeout=8.0)
            if thread.is_alive():
                remaining.append(thread.name)
            else:
                setattr(studio, attribute, None)
        if remaining:
            raise RuntimeError("library worker thread(s) still alive: " + ", ".join(remaining))

    def _close_dialogs(self) -> None:
        studio = self.studio
        for attribute in (
            "library_selector_dialog", "mixer_dialog",
            "_guided_wizard", "_fusion_wizard", "_assisted_wizard",
        ):
            dialog = getattr(studio, attribute, None)
            if dialog is None:
                continue
            dialog.close()
            setattr(studio, attribute, None)

    def _flush_settings(self) -> None:
        studio = self.studio
        studio._save_ui_state()
        studio.settings_store.flush()

    def _stop_detailed_analysis(self) -> None:
        studio = self.studio
        studio._stop_deep_analysis_subprocess()
        studio.library_scan.close()

    def _close_library_index(self) -> None:
        self.studio.library_index.close()

    def _close_session(self) -> None:
        studio = self.studio
        # Dirty work should already have been resolved by closeEvent. If prompts
        # are disabled, preserve it as recoverable instead of silently abandoning
        # an active manifest.
        if studio._session_dirty:
            studio.session_controller.save_for_later()
            return
        studio.session_workspace.close_clean()

    def _cleanup_runtime(self) -> None:
        report = runtime_supervisor.cleanup()
        self.last_report = report
        if report.remaining_processes or report.callback_errors:
            details: list[str] = []
            if report.remaining_processes:
                details.append("remaining PIDs=" + ",".join(map(str, report.remaining_processes)))
            if report.callback_errors:
                details.append("; ".join(report.callback_errors))
            raise RuntimeError("runtime cleanup incomplete: " + " | ".join(details))
        if self.studio.playback_engine.scheduler_alive():
            raise RuntimeError("playback scheduler remained alive after runtime cleanup")

    def _cleanup_filesystem(self) -> None:
        report = self.studio.paths.cleanup_runtime_garbage(remove_empty_session_dirs=True)
        append_bounded(
            self.studio.paths.logs / "shutdown.log",
            f"filesystem-cleanup={report}\n",
        )

    @staticmethod
    def _collect_garbage() -> None:
        # v0.25 keeps Python's cyclic collector enabled, but deliberately avoids
        # using a forced collection as a substitute for deterministic cleanup.
        if not gc.isenabled():
            gc.enable()

    def _write_report(self, complete: bool) -> None:
        report = self.last_report
        append_bounded(
            self.studio.paths.logs / "shutdown.log",
            " | ".join(
                (
                    datetime.now(timezone.utc).isoformat(),
                    f"state={'complete' if complete else 'partial'}",
                    f"phases={','.join(sorted(self.completed))}",
                    f"failures={self.failures}",
                    f"children={report.requested_processes if report else 'not-run'}",
                    f"terminated={report.terminated_processes if report else 'not-run'}",
                    f"remaining={list(report.remaining_processes) if report else 'not-run'}",
                )
            )
            + "\n",
        )
