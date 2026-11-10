from __future__ import annotations

"""Playback/UI coordination extracted from the main studio window."""

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QMessageBox

from core.operation_diagnostics import operation_failure, write_diagnostic
from core.playback_engine import PlaybackState
from core.remix_version import APP_NAME
from core.take_rendering import audio_gain_for_volume_percent
from core.remix_engine import take_identity_key

if TYPE_CHECKING:
    from app.studio_window import MidiRemixerStudio


class PlaybackController:
    """Own transport state and the active playback target.

    The controller deliberately leaves audio scheduling in ``PlaybackEngine``.
    Its job is to keep the main window, child dialogs, selected Take and resume
    target synchronized through one boundary.
    """

    def __init__(self, studio: "MidiRemixerStudio") -> None:
        self.studio = studio

    def set_transport_state(self, state: str) -> None:
        studio = self.studio
        if state == "playing":
            studio.transport_play.setText("Pause")
            studio.transport_play.setProperty("transportState", "pause")
            studio.transport_stop.setEnabled(True)
            studio.transport_stop.setProperty("transportState", "active")
        elif state == "paused":
            studio.transport_play.setText("Resume")
            studio.transport_play.setProperty("transportState", "ready")
            studio.transport_stop.setEnabled(True)
            studio.transport_stop.setProperty("transportState", "active")
        else:
            studio.transport_play.setText("Play")
            studio.transport_play.setProperty("transportState", "ready")
            studio.transport_stop.setEnabled(False)
            studio.transport_stop.setProperty("transportState", "idle")
        for button in (studio.transport_play, studio.transport_stop):
            button.style().unpolish(button)
            button.style().polish(button)
            button.update()
        selector = getattr(studio, "library_selector_dialog", None)
        if selector is not None:
            selector.set_transport_state(state)
        if studio.mixer_dialog is not None:
            studio.mixer_dialog.set_transport_state(state)

    def play_path(
        self,
        path: str,
        label: str,
        version_key: str,
        *,
        start_percent: int = 0,
        start_tick: int | None = None,
    ) -> bool:
        studio = self.studio
        target_path = Path(path)
        if not target_path.is_file():
            self.set_transport_state("stopped")
            write_diagnostic(studio.paths.logs / "ui-errors.log", "Play MIDI", FileNotFoundError(path), path=path, label=label)
            QMessageBox.critical(
                studio,
                "Playback file is missing",
                f"The selected MIDI file is no longer available:\n{path}\n\nChoose the Take again or generate a new Trial. Earlier source files were not changed.",
            )
            return False
        soundfont = studio.project.soundfont_path or studio.settings_store.get("last_soundfont_path")
        if not soundfont or not Path(soundfont).exists() or not studio.resolved_fluidsynth_path():
            QMessageBox.warning(
                studio,
                "Finish audio setup",
                studio.audio_readiness_message() + "\n\nUse Audio Setup on the first screen or Settings → Audio Setup.",
            )
            studio._refresh_audio_readiness_ui()
            return False
        if not studio._ensure_fluidsynth_ready():
            return False
        try:
            snapshot = studio.playback_engine.play(
                path,
                label,
                version_key,
                soundfont,
                audio_gain_for_volume_percent(studio.volume_slider.value()),
                100,
                start_percent=max(0, min(99, int(start_percent))),
                start_tick=start_tick,
            )
        except Exception as exc:
            self.set_transport_state("stopped")
            write_diagnostic(studio.paths.logs / "ui-errors.log", "Play MIDI", exc, path=path, label=label)
            QMessageBox.critical(
                studio,
                "Playback failed",
                operation_failure(
                    "Play MIDI",
                    exc,
                    path=path,
                    corrective_action="Confirm FluidSynth and the SoundFont in Settings, then try the same Take again.",
                ),
            )
            return False
        studio.current_playback_path = path
        studio.current_playback_label = label
        studio.current_playback_key = version_key
        studio.current_playback_generation = snapshot.generation
        studio.poll_timer.start()
        self.set_transport_state("playing")
        studio.playback_label.setText(f"Playing: {label}")
        studio.statusBar().showMessage(f"Playing {label}")
        return True

    def play_selected_take(self) -> bool:
        studio = self.studio
        if not studio.selected_take:
            QMessageBox.information(studio, APP_NAME, "Single-click a Take first. Double-clicking a Take also plays it.")
            return False
        identity = take_identity_key(studio.selected_take)
        return self.play_path(studio.selected_take.path, studio.selected_take.name, "take::" + "::".join(identity))

    def pause_or_resume_current(self) -> None:
        studio = self.studio
        if studio.playback_engine.is_playing():
            snapshot = studio.playback_engine.pause()
            studio.poll_timer.stop()
            self.set_transport_state("paused")
            studio.playback_label.setText(f"Paused: {snapshot.label or 'MIDI'}")
            return
        if not studio.playback_engine.is_paused() or not studio.current_playback_path:
            return
        soundfont = studio.project.soundfont_path or studio.settings_store.get("last_soundfont_path")
        if not soundfont:
            return
        try:
            snapshot = studio.playback_engine.play(
                studio.current_playback_path,
                studio.current_playback_label or "MIDI",
                studio.current_playback_key or "resume",
                soundfont,
                audio_gain_for_volume_percent(studio.volume_slider.value()),
                100,
                start_tick=studio.playback_engine.current_tick,
            )
        except Exception as exc:
            write_diagnostic(
                studio.paths.logs / "ui-errors.log",
                "Resume MIDI playback",
                exc,
                path=studio.current_playback_path,
            )
            QMessageBox.critical(studio, "Playback failed", operation_failure("Resume MIDI playback", exc, path=studio.current_playback_path))
            return
        studio.current_playback_generation = snapshot.generation
        studio.poll_timer.start()
        self.set_transport_state("playing")

    def toggle_play_pause(self) -> None:
        studio = self.studio
        if studio.playback_engine.is_playing() or studio.playback_engine.is_paused():
            self.pause_or_resume_current()
            return
        if studio.tabs.currentIndex() == 3 and studio.selected_take:
            self.play_selected_take()
        else:
            studio.play_selected_source_raw()

    def stop(self, *, reset_label: bool = True) -> None:
        studio = self.studio
        studio.playback_engine.stop(reset_to_start=True)
        studio.poll_timer.stop()
        self.set_transport_state("stopped")
        if reset_label:
            studio.playback_label.setText(
                studio.selected_take.name if studio.selected_take and studio.tabs.currentIndex() == 3 else "Stopped"
            )

    def poll(self) -> None:
        studio = self.studio
        snapshot = studio.playback_engine.poll()
        active_generation = int(getattr(studio, "current_playback_generation", 0) or 0)
        if snapshot.finished and active_generation and snapshot.generation and snapshot.generation != active_generation:
            # Completion from a superseded scheduler must not stop or relabel the
            # newer Guided/Assisted Take that is currently playing.
            return
        if snapshot.error:
            studio.poll_timer.stop()
            self.set_transport_state("stopped")
            path = studio.current_playback_path or ""
            write_diagnostic(
                studio.paths.logs / "ui-errors.log",
                "MIDI playback scheduler",
                RuntimeError(snapshot.error),
                path=path,
                label=snapshot.label or studio.current_playback_label or "MIDI",
            )
            studio.playback_label.setText("Playback failed")
            studio.statusBar().showMessage("Playback failed; the Take remains saved.", 7000)
            QMessageBox.critical(
                studio,
                "Playback stopped unexpectedly",
                operation_failure(
                    "MIDI playback",
                    RuntimeError(snapshot.error),
                    path=path,
                    corrective_action="Press Play again. If it repeats, reselect the Take and confirm FluidSynth and the SoundFont in Audio Setup.",
                ),
            )
            return
        if snapshot.finished or snapshot.state != PlaybackState.PLAYING:
            studio.poll_timer.stop()
            if snapshot.finished:
                self.set_transport_state("stopped")
                studio.playback_label.setText("Finished")

    def volume_changed(self, value: int) -> None:
        studio = self.studio
        studio.volume_value.setText(f"{value}%")
        studio.settings_store.data["master_volume"] = value
        if studio.playback_engine.is_playing():
            studio.playback_engine.change_gain(audio_gain_for_volume_percent(value))
