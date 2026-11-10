from __future__ import annotations

"""Recoverable session lifecycle extracted from the main studio window."""

from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QMessageBox

from core.operation_diagnostics import operation_failure, write_diagnostic
from core.remix_project import MidiRemixerProject
from core.remix_version import APP_NAME
from core.session_workspace import SessionWorkspace

if TYPE_CHECKING:
    from app.studio_window import MidiRemixerStudio


class SessionController:
    def __init__(self, studio: "MidiRemixerStudio") -> None:
        self.studio = studio

    def _snapshot(self) -> dict:
        studio = self.studio
        studio.project.set_source_specs(studio.source_specs)
        studio.project.set_takes(studio.takes)
        studio.project.guidance_mode = studio.guidance_mode
        studio.project.session_id = studio.session_workspace.session_id
        studio.project.trials = list(studio.session_workspace.manifest.trials)
        studio.project.active_trial_id = studio.current_trial_id
        studio.project.listening_reviews = list(studio.session_workspace.manifest.listening_reviews)
        return asdict(studio.project)

    def save_for_later(self) -> str:
        studio = self.studio
        path = studio.session_workspace.autosave(self._snapshot())
        studio.session_workspace.mark_for_later()
        studio._session_dirty = False
        studio.statusBar().showMessage(f"Recoverable session saved: {path}")
        return str(path)

    def autosave(self) -> bool:
        studio = self.studio
        if not bool(studio.settings_store.get("session_autosave", True)) or not studio._session_dirty:
            return False
        try:
            studio.session_workspace.autosave(self._snapshot())
            return True
        except (OSError, ValueError, TypeError) as exc:
            write_diagnostic(studio.paths.logs / "ui-errors.log", "Autosave active session", exc, session_id=studio.session_workspace.session_id)
            # Autosave must not interrupt listening or close the application.
            studio.statusBar().showMessage(f"Autosave could not complete: {type(exc).__name__}: {exc}", 5000)
            return False

    def offer_recoverable(self) -> None:
        studio = self.studio
        candidates = [
            item for item in studio._recoverable_sessions
            if str(item.get("session_id")) != studio.session_workspace.session_id
            and Path(str(item.get("root", "")), "autosave.miremix").is_file()
        ]
        if not candidates:
            return
        item = candidates[0]
        session_root = Path(str(item.get("root")))
        autosave = session_root / "autosave.miremix"
        trials = len(item.get("trials") or [])
        takes = len(item.get("take_paths") or {})
        sources = len(item.get("source_paths") or [])
        updated = item.get("updated_at") or item.get("created_at") or "unknown time"
        message = QMessageBox(studio)
        message.setWindowTitle("Recover an unfinished remix?")
        message.setIcon(QMessageBox.Icon.Question)
        message.setText("MIDI Remixer found an unfinished or saved-for-later session.")
        message.setInformativeText(
            f"Last updated: {updated}\nSongs: {sources} · Trials: {trials} · Takes: {takes}\n\n"
            "Recover opens the autosaved work. Reject and Clean Up permanently removes only that session's generated working files; original song MIDI files and exported copies remain protected."
        )
        recover = message.addButton("Recover", QMessageBox.ButtonRole.AcceptRole)
        reject = message.addButton("Reject and Clean Up", QMessageBox.ButtonRole.DestructiveRole)
        later = message.addButton("Not Now", QMessageBox.ButtonRole.RejectRole)
        message.exec()
        clicked = message.clickedButton()
        if clicked is recover:
            try:
                studio.session_workspace.reject()
            except OSError as exc:
                write_diagnostic(studio.paths.logs / "ui-errors.log", "Discard empty launch session", exc)
            studio._load_project_path(str(autosave), recovered=True)
        elif clicked is reject:
            try:
                old = SessionWorkspace(studio.session_base, str(item.get("session_id")))
                old.reject()
                studio.statusBar().showMessage("Rejected unfinished session cleaned. Original song MIDI files were preserved.")
            except (OSError, ValueError) as exc:
                write_diagnostic(studio.paths.logs / "ui-errors.log", "Reject recoverable session", exc, session_id=item.get("session_id"))
                QMessageBox.warning(studio, APP_NAME, operation_failure("Reject recoverable session", exc, path=session_root))
        elif clicked is later:
            return

    def reject_current(self) -> bool:
        studio = self.studio
        preview = studio.session_workspace.rejection_preview()
        trial_count = int(preview.get("trial_count") or 0)
        take_count = int(preview.get("take_count") or 0)
        file_count = int(preview.get("file_count") or 0)
        usage_mb = int(preview.get("bytes") or 0) / (1024 * 1024)
        scan_complete = bool(preview.get("scan_complete", True))
        estimate_prefix = "" if scan_complete else "at least "
        export_count = len(preview.get("exports_preserved") or [])
        conflicts = list(preview.get("protected_export_conflicts") or [])
        if conflicts:
            QMessageBox.critical(
                studio,
                "Move exported copies before cleanup",
                "Cleanup was blocked because a registered export is inside this temporary session folder. Move it to Documents, Music, or another folder outside the session, then try Reject Session again.\n\n"
                + "\n".join(conflicts),
            )
            return False
        answer = QMessageBox.warning(
            studio,
            "Reject this session?",
            f"This permanently deletes {trial_count} Trial{'s' if trial_count != 1 else ''}, {take_count} Take{'s' if take_count != 1 else ''}, "
            f"{estimate_prefix}{file_count} session-created file{'s' if file_count != 1 else ''}, and {estimate_prefix}approximately {usage_mb:.1f} MB.\n\n"
            f"Protected and preserved: original song MIDI files, the SoundFont, shared library analysis, saved projects, and {export_count} registered exported cop{'ies' if export_count != 1 else 'y'}.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return False
        studio.stop_playback()
        if studio.mixer_dialog is not None:
            studio.mixer_dialog.close()
            studio.mixer_dialog = None
        try:
            studio.session_workspace.reject()
        except (OSError, ValueError) as exc:
            write_diagnostic(studio.paths.logs / "ui-errors.log", "Reject current session", exc, session_id=studio.session_workspace.session_id)
            QMessageBox.critical(studio, "Session cleanup failed", operation_failure("Reject current session", exc, path=studio.session_workspace.root))
            return False
        studio.session_workspace = SessionWorkspace(studio.paths.sessions)
        studio.session_workspace.set_guidance_mode(studio.guidance_mode)
        studio.project = MidiRemixerProject(guidance_mode=studio.guidance_mode, session_id=studio.session_workspace.session_id)
        studio.source_specs = []
        studio.takes = []
        studio.selected_take = None
        studio.current_trial_id = None
        studio.assisted_section_source_order = ()
        studio.assisted_section_labels = ()
        studio.assisted_section_decisions = ()
        studio._session_dirty = False
        studio._session_saved = False
        studio._refresh_source_set()
        studio._refresh_takes()
        studio.statusBar().showMessage("Rejected session cleaned completely. Original library songs were preserved.")
        return True
