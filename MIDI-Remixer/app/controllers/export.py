from __future__ import annotations

"""Take export and render coordination extracted from the main window."""

import os
from dataclasses import replace
from pathlib import Path
import subprocess
import sys
from typing import TYPE_CHECKING, Iterable

from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from app.export_dialog import ExportDialog
from core.atomic_io import safe_unlink
from core.export_delivery import ExportSelection, export_midi
from core.operation_diagnostics import operation_failure, write_diagnostic
from core.remix_engine import export_take, take_identity_key
from core.remix_version import APP_NAME
from core.take_rendering import audio_gain_for_volume_percent

if TYPE_CHECKING:
    from app.studio_window import MidiRemixerStudio


class ExportController:
    OPERATION_NAME = "export-delivery"

    def __init__(self, studio: "MidiRemixerStudio") -> None:
        self.studio = studio
        self._active_selection: ExportSelection | None = None

    def _message_parent(self):
        return QApplication.activeModalWidget() or self.studio

    def choose_output_directory(self) -> None:
        studio = self.studio
        start = (
            studio.settings_store.get("last_export_directory")
            or studio.project.output_directory
            or studio.settings_store.get("last_output_directory")
            or str(Path.home())
        )
        folder = QFileDialog.getExistingDirectory(self._message_parent(), "Choose working output folder", start)
        if folder:
            studio.project.output_directory = folder
            studio.settings_store.update_many({"last_output_directory": folder, "last_export_directory": folder})
            studio.output_label.setText(f"Working output: {folder}")

    def _record_export(self, selected_take, target: str | Path) -> None:
        studio = self.studio
        target_text = str(target)
        studio.session_workspace.register_export(target_text)
        selected_key = take_identity_key(selected_take)
        studio.takes = [
            replace(take, exported_paths=tuple(dict.fromkeys((*take.exported_paths, target_text))))
            if take_identity_key(take) == selected_key else take
            for take in studio.takes
        ]
        studio.selected_take = next((take for take in studio.takes if take_identity_key(take) == selected_key), studio.selected_take)
        studio.project.set_takes(studio.takes)
        studio._session_dirty = True

    def _record_exports(self, selected_take, targets: Iterable[str | Path]) -> None:
        for target in targets:
            self._record_export(selected_take, target)

    def _set_export_running(self, running: bool, message: str = "") -> None:
        studio = self.studio
        for name in ("export_package_button", "export_midi_button", "render_wav_button", "render_mp3_button"):
            button = getattr(studio, name, None)
            if button is not None:
                button.setEnabled(not running)
        cancel = getattr(studio, "cancel_export_button", None)
        if cancel is not None:
            cancel.setEnabled(running)
            cancel.setVisible(running)
            cancel.setText("Cancel Export" if running else "Cancel Export")
        if message:
            studio.statusBar().showMessage(message)

    def cancel_export(self) -> bool:
        studio = self.studio
        if not studio.operation_manager.is_active(self.OPERATION_NAME):
            self._set_export_running(False)
            return False
        studio.operation_manager.cancel(self.OPERATION_NAME)
        cancel = getattr(studio, "cancel_export_button", None)
        if cancel is not None:
            cancel.setEnabled(False)
            cancel.setText("Cancelling…")
        studio.statusBar().showMessage("Cancelling export and removing incomplete delivery files…")
        return True

    def export_selected(self) -> bool:
        studio = self.studio
        take = studio.selected_take
        if not take:
            QMessageBox.information(self._message_parent(), APP_NAME, "Select a Take first.")
            return False
        if studio.operation_manager.is_active(self.OPERATION_NAME):
            QMessageBox.information(self._message_parent(), APP_NAME, "An export is already running.")
            return False

        soundfont = studio.project.soundfont_path or studio.settings_store.get("last_soundfont_path")
        audio_available = bool(
            soundfont and Path(str(soundfont)).is_file() and studio.resolved_fluidsynth_path()
        )
        mp3_available = bool(audio_available and studio.player.find_ffmpeg())
        default_directory = (
            studio.settings_store.get("last_export_directory")
            or studio.settings_store.get("last_output_directory")
            or Path.home()
        )
        dialog = ExportDialog(
            take.name,
            default_directory,
            audio_available=audio_available,
            mp3_available=mp3_available,
            parent=self._message_parent(),
        )
        remembered = set(studio.settings_store.get("export_last_formats", ["midi"]) or ["midi"])
        dialog.midi.setChecked("midi" in remembered)
        dialog.wav.setChecked(dialog.wav.isEnabled() and "wav" in remembered)
        dialog.mp3.setChecked(dialog.mp3.isEnabled() and "mp3" in remembered)
        bitrate_index = dialog.bitrate.findData(int(studio.settings_store.get("export_mp3_bitrate", 192) or 192))
        if bitrate_index >= 0:
            dialog.bitrate.setCurrentIndex(bitrate_index)
        dialog.bake_volume.setChecked(bool(studio.settings_store.get("export_bake_master_volume", False)))
        dialog.open_folder.setChecked(bool(studio.settings_store.get("export_open_folder_after", True)))
        dialog._update_state()
        if dialog.exec() != dialog.DialogCode.Accepted:
            return False
        selection = dialog.selection()
        return self._start_delivery_export(take, selection, soundfont=str(soundfont or ""))

    def _start_delivery_export(self, take, selection: ExportSelection, *, soundfont: str) -> bool:
        studio = self.studio
        item = selection.normalized()
        targets = item.targets()
        target_root = Path(item.output_directory)
        take_key = take_identity_key(take)
        take_path = str(take.path)
        take_id = str(take.take_id)
        gain = audio_gain_for_volume_percent(studio.volume_slider.value())
        master_volume = studio.volume_slider.value()
        self._active_selection = item

        def operation(cancel_event, progress):
            created: list[Path] = []
            try:
                target_root.mkdir(parents=True, exist_ok=True)
                total = len(item.formats)
                for index, kind in enumerate(item.formats, start=1):
                    if cancel_event.is_set():
                        raise RuntimeError("Export was cancelled.")
                    label = {"midi": "MIDI", "wav": "WAV", "mp3": "MP3"}[kind]
                    progress(f"Creating {label}…", int(round((index - 1) * 100 / max(1, total))))
                    target = targets[kind]
                    if target.exists():
                        raise FileExistsError(f"Export target already exists: {target}")
                    if kind == "midi":
                        result = export_midi(
                            take_path,
                            target,
                            bake_master_volume=item.bake_master_volume_into_midi,
                            master_volume_percent=master_volume,
                        )
                    elif kind == "wav":
                        if not soundfont:
                            raise RuntimeError("A SoundFont is required for WAV export.")
                        result = studio.player.render_wav(
                            take_path, soundfont, target, gain, cancel_event=cancel_event
                        )
                    else:
                        if not soundfont:
                            raise RuntimeError("A SoundFont is required for MP3 export.")
                        result = studio.player.render_mp3(
                            take_path,
                            soundfont,
                            target,
                            gain,
                            cancel_event=cancel_event,
                            bitrate_kbps=item.mp3_bitrate_kbps,
                        )
                    created.append(Path(result))
                    progress(f"Created {label}", int(round(index * 100 / max(1, total))))
                return tuple(str(path) for path in created)
            except (OSError, ValueError, RuntimeError, TypeError, EOFError):
                # A delivery request behaves atomically: do not leave a half-set
                # of files after cancellation or a later-format failure.
                for path in created:
                    safe_unlink(path)
                raise

        def progress(message: str, percent: int) -> None:
            studio.statusBar().showMessage(f"{message} ({max(0, min(100, percent))}%)")

        def success(result: object) -> None:
            self._set_export_running(False)
            paths = tuple(str(path) for path in (result if isinstance(result, (tuple, list)) else (result,)))
            current_take = next((candidate for candidate in studio.takes if take_identity_key(candidate) == take_key), take)
            self._record_exports(current_take, paths)
            studio.settings_store.update_many({
                "last_export_directory": str(target_root),
                "export_last_formats": list(item.formats),
                "export_mp3_bitrate": item.mp3_bitrate_kbps,
                "export_bake_master_volume": item.bake_master_volume_into_midi,
                "export_open_folder_after": item.open_folder_after,
            })
            studio.statusBar().showMessage(f"Export complete: {len(paths)} file(s) in {target_root}")
            QMessageBox.information(
                self._message_parent(),
                "Export complete",
                "Created:\n\n" + "\n".join(paths),
            )
            if item.open_folder_after:
                self._open_directory(target_root)
            self._active_selection = None

        def failure(exc: BaseException) -> None:
            self._set_export_running(False)
            self._active_selection = None
            cancelled = "cancel" in str(exc).casefold()
            write_diagnostic(
                studio.paths.logs / "ui-errors.log",
                "Export selected Take",
                exc,
                path=target_root,
                take_id=take_id,
                formats=",".join(item.formats),
            )
            if cancelled:
                studio.statusBar().showMessage("Export cancelled; incomplete files were removed.", 6000)
                return
            QMessageBox.critical(
                self._message_parent(),
                "Export failed",
                operation_failure(
                    "Export selected Take",
                    exc,
                    path=target_root,
                    corrective_action="Confirm the Take still plays. For audio, run Settings → Audio Test and verify FFmpeg for MP3.",
                ),
            )

        started = studio.operation_manager.start(
            self.OPERATION_NAME,
            operation,
            on_success=success,
            on_failure=failure,
            on_progress=progress,
        )
        if started:
            self._set_export_running(True, f"Starting export of {len(item.formats)} format(s)…")
        return started

    def export_selected_midi(self) -> str | None:
        studio = self.studio
        if studio.operation_manager.is_active(self.OPERATION_NAME):
            QMessageBox.information(self._message_parent(), APP_NAME, "Wait for the current export to finish or cancel it first.")
            return None
        take = studio.selected_take
        if not take:
            QMessageBox.information(self._message_parent(), APP_NAME, "Select a Take first.")
            return None
        path, _ = QFileDialog.getSaveFileName(
            self._message_parent(),
            "Export remixed MIDI",
            str(Path.home() / f"{Path(take.path).stem}.mid"),
            "MIDI files (*.mid)",
        )
        if not path:
            return None
        try:
            target = export_take(take, path)
            self._record_export(take, target)
        except (OSError, ValueError, RuntimeError) as exc:
            write_diagnostic(studio.paths.logs / "ui-errors.log", "Export MIDI Take", exc, path=path, take_id=take.take_id)
            QMessageBox.critical(self._message_parent(), "MIDI export failed", operation_failure("Export MIDI Take", exc, path=path))
            return None
        studio.statusBar().showMessage(f"Exported MIDI: {target}")
        return str(target)

    def render_selected_audio(self, kind: str) -> bool:
        studio = self.studio
        if studio.operation_manager.is_active(self.OPERATION_NAME):
            QMessageBox.information(self._message_parent(), APP_NAME, "Wait for the current export to finish or cancel it first.")
            return False
        take = studio.selected_take
        if not take:
            QMessageBox.information(self._message_parent(), APP_NAME, "Select a Take first.")
            return False
        soundfont = studio.project.soundfont_path or studio.settings_store.get("last_soundfont_path")
        if not soundfont or not Path(soundfont).exists():
            QMessageBox.warning(self._message_parent(), "Instrument library required", "Choose an instrument library before rendering audio.")
            return False
        if not studio._ensure_fluidsynth_ready():
            return False
        suffix = ".wav" if kind == "wav" else ".mp3"
        label = "WAV audio" if kind == "wav" else "MP3 audio"
        target, _ = QFileDialog.getSaveFileName(
            self._message_parent(),
            f"Render {label}",
            str(Path.home() / f"{Path(take.path).stem}{suffix}"),
            f"{label} (*{suffix})",
        )
        if not target:
            return False

        operation_name = f"render-{kind}"
        if studio.operation_manager.is_active(operation_name):
            QMessageBox.information(self._message_parent(), APP_NAME, f"A {label} render is already running.")
            return False
        take_key = take_identity_key(take)
        take_path = str(take.path)
        take_id = str(take.take_id)
        soundfont_path = str(soundfont)
        target_path = str(target)
        gain = audio_gain_for_volume_percent(studio.volume_slider.value())

        def operation(cancel_event, _progress):
            if cancel_event.is_set():
                raise RuntimeError("Audio render was cancelled before it started.")
            result = (
                studio.player.render_wav(take_path, soundfont_path, target_path, gain, cancel_event=cancel_event)
                if kind == "wav"
                else studio.player.render_mp3(take_path, soundfont_path, target_path, gain, cancel_event=cancel_event)
            )
            if cancel_event.is_set():
                raise RuntimeError("Audio render was cancelled.")
            return str(result)

        def success(result: object) -> None:
            current_take = next((item for item in studio.takes if take_identity_key(item) == take_key), take)
            self._record_export(current_take, str(result))
            studio.statusBar().showMessage(f"Rendered {label}: {result}")
            QMessageBox.information(self._message_parent(), f"{label} complete", f"Rendered successfully.\n\n{result}")

        def failure(exc: BaseException) -> None:
            write_diagnostic(
                studio.paths.logs / "ui-errors.log",
                f"Render {label}",
                exc,
                path=target_path,
                take_id=take_id,
                soundfont=soundfont_path,
            )
            QMessageBox.critical(
                self._message_parent(),
                f"{label} failed",
                operation_failure(
                    f"Render {label}",
                    exc,
                    path=target_path,
                    corrective_action="Run Settings → Audio Test. For MP3, also confirm FFmpeg is available.",
                ),
            )

        started = studio.operation_manager.start(
            operation_name,
            operation,
            on_success=success,
            on_failure=failure,
        )
        if started:
            studio.statusBar().showMessage(f"Rendering {label} in the background…")
        return started

    def _open_directory(self, path: Path) -> bool:
        try:
            if os.name == "nt":
                os.startfile(str(path))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
            return True
        except (OSError, subprocess.SubprocessError) as exc:
            write_diagnostic(self.studio.paths.logs / "ui-errors.log", "Open export folder", exc, path=path)
            QMessageBox.warning(self._message_parent(), "Could not open folder", operation_failure("Open export folder", exc, path=path))
            return False

    def open_working_folder(self) -> bool:
        studio = self.studio
        path = Path(studio.selected_take.path).parent if studio.selected_take else (
            Path(studio.project.output_directory) if studio.project.output_directory else None
        )
        if not path or not path.exists():
            QMessageBox.information(self._message_parent(), APP_NAME, "No working output folder exists yet.")
            return False
        return self._open_directory(path)
