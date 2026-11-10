from __future__ import annotations

"""Audio dependency setup and readiness orchestration.

This controller keeps blocking dependency probes, installation operations, and
SoundFont selection out of the main window implementation while preserving the
window's compatibility methods for existing Guided/Assisted callers.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox

from core.audio_bootstrap import automatic_install_available, install_fluidsynth
from core.dependency_setup import find_fluidsynth, find_soundfont, find_soundfonts
from core.logging_config import append_bounded
from core.operation_diagnostics import operation_failure
from core.take_rendering import audio_gain_for_volume_percent


class AudioSetupController:
    def __init__(self, studio: Any) -> None:
        self.studio = studio

    def resolved_fluidsynth_path(self) -> str | None:
        studio = self.studio
        return find_fluidsynth(studio.root, studio.settings_store.get("fluidsynth_path"))

    def resolved_soundfont_path(self) -> str | None:
        studio = self.studio
        configured = studio.project.soundfont_path or studio.settings_store.get("last_soundfont_path")
        return find_soundfont(
            studio.root,
            configured,
            studio.settings_store.get("last_soundfont_directory"),
        )

    def ready(self) -> bool:
        return bool(self.resolved_fluidsynth_path() and self.resolved_soundfont_path())

    def readiness_message(self) -> str:
        missing: list[str] = []
        if not self.resolved_fluidsynth_path():
            missing.append("FluidSynth audio engine")
        if not self.resolved_soundfont_path():
            missing.append("SoundFont instrument library")
        if not missing:
            return "Audio is ready for playback and rendering."
        if len(missing) == 2:
            return "Set up FluidSynth and choose a SoundFont before selecting a remix mode."
        return f"Set up the {missing[0]} before playback or remix-mode entry."

    def refresh_readiness_ui(self) -> None:
        studio = self.studio
        fluidsynth = self.resolved_fluidsynth_path()
        soundfont = self.resolved_soundfont_path()
        ready = bool(fluidsynth and soundfont)
        if hasattr(studio, "audio_status"):
            if ready:
                studio.audio_status.setText("Audio ready")
                studio.audio_status.setToolTip(f"FluidSynth: {fluidsynth}\nSoundFont: {soundfont}")
            elif not fluidsynth:
                studio.audio_status.setText("FluidSynth not found")
                studio.audio_status.setToolTip("FluidSynth is required for playback and audio rendering.")
            else:
                studio.audio_status.setText("SoundFont required")
                studio.audio_status.setToolTip("Choose an .sf2 or .sf3 instrument library.")
        if hasattr(studio, "start_fluidsynth_status"):
            studio.start_fluidsynth_status.setText(
                f"✓ FluidSynth: {Path(fluidsynth).name}" if fluidsynth else "✕ FluidSynth: not found"
            )
            studio.start_fluidsynth_status.setToolTip(
                fluidsynth or "Playback is unavailable until FluidSynth is installed or located."
            )
        if hasattr(studio, "start_soundfont_status"):
            studio.start_soundfont_status.setText(
                f"✓ Instrument Library: {Path(soundfont).name}"
                if soundfont
                else "✕ Instrument Library: no SoundFont selected"
            )
            studio.start_soundfont_status.setToolTip(soundfont or "Choose an .sf2 or .sf3 SoundFont.")
        if hasattr(studio, "start_audio_message"):
            studio.start_audio_message.setText(self.readiness_message())
            studio.start_audio_message.setObjectName("SuccessPanel" if ready else "WarningPanelText")
            studio.start_audio_message.style().unpolish(studio.start_audio_message)
            studio.start_audio_message.style().polish(studio.start_audio_message)
        if hasattr(studio, "start_fluidsynth_button"):
            studio.start_fluidsynth_button.setVisible(not bool(fluidsynth))
        if hasattr(studio, "start_soundfont_button"):
            studio.start_soundfont_button.setVisible(not bool(soundfont))
            # SoundFont selection is independent of the MIDI catalogue. Do not
            # leave this control in the disabled state it had at startup after
            # FluidSynth setup completes asynchronously.
            studio.start_soundfont_button.setEnabled(not bool(soundfont))
            studio.start_soundfont_button.setToolTip(
                "Choose an .sf2 or .sf3 instrument library." if not soundfont else str(soundfont)
            )
        if hasattr(studio, "audio_ready_panel"):
            studio.audio_ready_panel.setProperty("ready", ready)
            studio.audio_ready_panel.style().unpolish(studio.audio_ready_panel)
            studio.audio_ready_panel.style().polish(studio.audio_ready_panel)
        if hasattr(studio, "guidance_mode_buttons"):
            for button in studio.guidance_mode_buttons.values():
                button.setEnabled(ready)
                button.setToolTip("" if ready else self.readiness_message())
        if hasattr(studio, "start_library_button"):
            studio.start_library_button.setEnabled(True)
            studio.start_library_button.setToolTip(
                "MIDI Library Manager can catalogue and analyze without Remixer audio setup. "
                "Remixer playback and rendering require FluidSynth and a SoundFont."
            )
        # Decision Workflow owns additional onboarding controls. Synchronize
        # them immediately after audio setup instead of waiting for an unrelated
        # library event to refresh their enabled state.
        refresh_onboarding = getattr(studio, "_refresh_onboarding_controls", None)
        if callable(refresh_onboarding):
            refresh_onboarding()
        if hasattr(studio, "_update_navigation"):
            studio._update_navigation()

    def set_setup_visible(self, visible: bool) -> None:
        studio = self.studio
        studio.audio_status.setVisible(visible)
        studio.audio_setup_button.setVisible(visible)

    def apply_fluidsynth_path(self, path: str | Path) -> None:
        studio = self.studio
        resolved = str(Path(path).expanduser().resolve())
        studio.settings_store.update_many({"fluidsynth_path": resolved})
        studio.player.set_executable_path(resolved)
        studio.playback_engine.set_fluidsynth_path(resolved)
        studio.audio_status.setText(f"Audio: {Path(resolved).name}")
        studio.audio_status.setToolTip(resolved)
        self.set_setup_visible(False)
        self.refresh_readiness_ui()

    def auto_detect_fluidsynth(self, show_result: bool = True) -> bool:
        studio = self.studio
        found = self.resolved_fluidsynth_path()
        if found:
            self.apply_fluidsynth_path(found)
            if show_result:
                QMessageBox.information(studio, "Audio Setup", f"FluidSynth is ready.\n\n{found}")
            return True
        studio.audio_status.setText("Audio setup required")
        studio.audio_status.setToolTip("FluidSynth has not been located.")
        self.set_setup_visible(True)
        self.refresh_readiness_ui()
        if show_result:
            QMessageBox.information(
                studio,
                "Audio Setup",
                "FluidSynth was not found automatically. "
                "Choose an existing fluidsynth.exe or install it.",
            )
        return False

    def choose_fluidsynth_executable(self) -> bool:
        studio = self.studio
        path, _ = QFileDialog.getOpenFileName(
            studio,
            "Choose fluidsynth.exe",
            str(studio.root),
            "FluidSynth executable (fluidsynth.exe);;Executable files (*.exe);;All files (*.*)",
        )
        if not path:
            return False
        self.apply_fluidsynth_path(path)
        return True

    def install_fluidsynth(self) -> bool:
        """Run verified dependency setup outside the GUI thread."""

        studio = self.studio
        operation_name = "install-fluidsynth"
        if studio.operation_manager.is_active(operation_name):
            QMessageBox.information(studio, "Audio Setup", "FluidSynth setup is already running.")
            return False
        log_path = studio.paths.logs / "audio-setup.log"

        def operation(cancel_event, progress):
            if cancel_event.is_set():
                raise RuntimeError("FluidSynth setup was cancelled before it started.")
            return install_fluidsynth(studio.root, progress, cancel_event)

        def progress(message: str, percent: int) -> None:
            studio.audio_status.setText(f"Audio setup: {message} ({percent}%)")
            studio.statusBar().showMessage(f"Audio setup: {message} ({percent}%)")

        def success(result: object) -> None:
            if getattr(result, "ok", False) and getattr(result, "fluidsynth_path", None):
                self.apply_fluidsynth_path(str(result.fluidsynth_path))
                QMessageBox.information(studio, "Audio Setup", str(result.detail or "FluidSynth is ready."))
                return
            detail = str(getattr(result, "detail", "FluidSynth setup did not produce a usable executable."))
            append_bounded(
                log_path,
                f"{datetime.now(timezone.utc).isoformat()} install declined/failed: {detail}\n",
            )
            self.refresh_readiness_ui()
            QMessageBox.warning(
                studio,
                "FluidSynth setup did not complete",
                detail
                + "\n\nUse Choose fluidsynth.exe to select an existing trusted installation. "
                "MIDI analysis and MIDI export remain available.",
            )

        def failure(exc: BaseException) -> None:
            studio._write_ui_error_log("FluidSynth setup", exc)
            self.refresh_readiness_ui()
            QMessageBox.critical(
                studio,
                "FluidSynth setup error",
                operation_failure(
                    "FluidSynth setup",
                    exc,
                    corrective_action="Choose an existing trusted fluidsynth.exe in Audio Setup.",
                ),
            )

        started = studio.operation_manager.start(
            operation_name,
            operation,
            on_success=success,
            on_failure=failure,
            on_progress=progress,
        )
        if started:
            studio.audio_status.setText("Audio setup running…")
            studio.statusBar().showMessage("Verified FluidSynth setup running in the background…")
        return False  # readiness is updated asynchronously on success

    def test_audio_tone(self) -> bool:
        """Run the blocking audio-route test in an owned background operation."""

        studio = self.studio
        soundfont = studio.project.soundfont_path or studio.settings_store.get("last_soundfont_path")
        if not soundfont or not Path(soundfont).exists():
            QMessageBox.warning(studio, "Audio Test", "Choose an instrument library first.")
            return False
        if not self.ensure_fluidsynth_ready():
            return False
        operation_name = "audio-test-tone"
        if studio.operation_manager.is_active(operation_name):
            QMessageBox.information(studio, "Audio Test", "An audio test is already running.")
            return False
        gain = audio_gain_for_volume_percent(studio.volume_slider.value())
        driver = str(studio.settings_store.get("audio_driver", "default"))
        soundfont_path = str(Path(soundfont).resolve())

        def operation(cancel_event, progress):
            progress("Opening the selected audio route", 10)
            if cancel_event.is_set():
                raise RuntimeError("Audio test was cancelled.")
            result = studio.playback_engine.test_audio_tone(
                soundfont_path,
                volume_gain=gain,
                driver=driver,
            )
            progress("Audio route verified", 100)
            return result

        def success(result: object) -> None:
            data = result if isinstance(result, dict) else {}
            self.refresh_readiness_ui()
            QMessageBox.information(
                studio,
                "Audio Test",
                f"The test chord played successfully.\n\nDriver: {data.get('driver', driver)}",
            )

        def failure(exc: BaseException) -> None:
            studio._write_ui_error_log("Audio route test", exc)
            self.refresh_readiness_ui()
            QMessageBox.critical(
                studio,
                "Audio Test failed",
                studio._operation_error_text(
                    "Audio Test",
                    exc,
                    path=soundfont_path,
                    corrective_action=(
                        "Choose another audio driver or verify FluidSynth and the SoundFont in Audio Setup."
                    ),
                ),
            )

        started = studio.operation_manager.start(
            operation_name,
            operation,
            on_success=success,
            on_failure=failure,
            on_progress=lambda text, percent: studio.statusBar().showMessage(
                f"Audio test: {text} ({percent}%)"
            ),
        )
        if started:
            studio.statusBar().showMessage("Audio test is running in the background…")
        return started

    def show_setup(self) -> bool:
        studio = self.studio
        detected = self.resolved_fluidsynth_path()
        message = QMessageBox(studio)
        message.setWindowTitle("Audio Setup")
        message.setIcon(QMessageBox.Icon.Information)
        message.setText(
            "FluidSynth is available."
            if detected
            else "FluidSynth is required for playback and audio rendering."
        )
        auto_install = automatic_install_available(studio.root)
        message.setInformativeText(
            str(detected)
            if detected
            else (
                "MIDI export works without it. Auto-detect can recover a previous MIDI Remixer setup. "
                + (
                    "You can also choose an existing fluidsynth.exe or install the release-pinned Windows build here."
                    if auto_install
                    else "Choose an existing trusted fluidsynth.exe. Automatic download is disabled in this release."
                )
            )
        )
        detect = message.addButton("Auto-detect", QMessageBox.ButtonRole.ActionRole)
        choose = message.addButton("Choose fluidsynth.exe", QMessageBox.ButtonRole.ActionRole)
        test = message.addButton("Run Audio Test", QMessageBox.ButtonRole.ActionRole)
        install = None
        if auto_install:
            install = message.addButton("Install FluidSynth", QMessageBox.ButtonRole.AcceptRole)
            install.setToolTip(
                "Download the official Windows FluidSynth package into MIDI Remixer\'s per-user dependency folder."
            )
        message.addButton(QMessageBox.StandardButton.Cancel)
        message.exec()
        clicked = message.clickedButton()
        if clicked is detect:
            return self.auto_detect_fluidsynth(True)
        if clicked is choose:
            return self.choose_fluidsynth_executable()
        if clicked is test:
            return self.test_audio_tone()
        if install is not None and clicked is install:
            return self.install_fluidsynth()
        return bool(detected)

    def ensure_fluidsynth_ready(self) -> bool:
        if self.resolved_fluidsynth_path():
            self.auto_detect_fluidsynth(False)
            return True
        return self.show_setup() and bool(self.resolved_fluidsynth_path())

    def apply_soundfont_path(self, path: str) -> None:
        studio = self.studio
        studio.project.soundfont_path = path
        studio.settings_store.update_many(
            {
                "last_soundfont_path": path,
                "last_soundfont_directory": str(Path(path).parent),
            }
        )
        self.set_soundfont_state(path)

    def set_soundfont_state(self, path: str | None) -> None:
        studio = self.studio
        available = bool(path and Path(path).exists())
        if available:
            resolved = str(Path(path).resolve())
            studio.soundfont_status.setText(f"Instrument Library: {Path(resolved).name}")
            studio.soundfont_status.setToolTip(resolved)
            studio.load_soundfont_button.setVisible(False)
            studio.soundfont_banner.setVisible(False)
        else:
            studio.soundfont_status.setText("Instrument Library: none")
            studio.soundfont_status.setToolTip(
                "Choose an .sf2 or .sf3 SoundFont from Settings or the Source Set screen."
            )
            studio.load_soundfont_button.setVisible(True)
            studio.soundfont_banner.setVisible(True)
        self.refresh_readiness_ui()

    def choose_soundfont(self) -> None:
        studio = self.studio
        detected = find_soundfonts(
            studio.root,
            studio.project.soundfont_path or studio.settings_store.get("last_soundfont_path"),
            studio.settings_store.get("last_soundfont_directory"),
        )
        browse_label = "Browse for another .sf2 or .sf3 file…"
        if detected:
            current = studio.project.soundfont_path or studio.settings_store.get("last_soundfont_path")
            labels: list[str] = []
            mapping: dict[str, str] = {}
            for index, path in enumerate(detected):
                tag = "Recommended" if index == 0 else "Detected"
                if current and Path(path) == Path(current):
                    tag = "Current / Recommended" if index == 0 else "Current"
                label = f"{tag}: {Path(path).name} — {Path(path).parent}"
                labels.append(label)
                mapping[label] = path
            labels.append(browse_label)
            choice, ok = QInputDialog.getItem(
                studio,
                "Choose Instrument Library",
                "Choose a detected library or browse for another:",
                labels,
                0,
                False,
            )
            if not ok:
                return
            if choice != browse_label:
                self.apply_soundfont_path(mapping[choice])
                return
        start = studio.settings_store.get("last_soundfont_directory") or str(studio.root)
        path, _ = QFileDialog.getOpenFileName(
            studio,
            "Choose instrument library",
            start,
            "Instrument libraries (*.sf2 *.sf3);;All files (*.*)",
        )
        if path:
            self.apply_soundfont_path(path)
