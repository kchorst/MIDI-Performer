from __future__ import annotations

"""Persistent Guided activity feedback shared across every wizard page."""

import time

from app.button_policy import set_action_enabled


class GuidedWizardActivityMixin:
    def _set_scanning(self, active: bool, message: str = "") -> None:
        self.scan_frame.setVisible(active or bool(message))
        self.scan_policy_row.setVisible(False if active else self.scan_policy_row.isVisible())
        self.deep_control_row.setVisible(False if active else self.deep_control_row.isVisible())
        self.scan_progress.setVisible(active)
        if active:
            self.scan_progress.setRange(0, 0)
        self.scan_status.setText(message or ("Opening prepared mash ideas…" if active else "Ready"))

        # This banner lives outside the stacked pages. A rematch/search therefore
        # stays visibly active even when the Prepare page is no longer visible.
        if active:
            if not self._guided_activity_started_at:
                self._guided_activity_started_at = time.perf_counter()
            self.guided_activity_panel.setVisible(True)
            self.guided_activity_progress.setRange(0, 0)
            self.guided_activity_label.setText(message or "MIDI Remixer is opening prepared mash ideas…")
            self.guided_activity_cancel.setVisible(
                self.studio.operation_manager.is_active(self._guided_discovery_operation_name)
                or "mash" in (message or "").casefold()
            )
            self.guided_activity_cancel.setEnabled(True)
            self.guided_activity_timer.start()
        else:
            self.guided_activity_timer.stop()
            self._guided_activity_started_at = 0.0
            self.guided_activity_panel.setVisible(False)

        ready = self.studio.audio_ready()
        if active:
            self.use_library.setEnabled(False)
            self.choose_folder_button.setEnabled(False)
            set_action_enabled(self.next, False, "Choose one mash idea from the recommendation cards above.")
        else:
            summary = self.studio.guided_library_summary_cached()
            self.use_library.setEnabled(ready and summary["guided_ready_songs"] >= 2)
            self.choose_folder_button.setEnabled(True)
            self._show_page(self.current_page)

    def _refresh_guided_activity_heartbeat(self) -> None:
        if not self.guided_activity_panel.isVisible() or not self._guided_activity_started_at:
            return
        elapsed = max(0.0, time.perf_counter() - self._guided_activity_started_at)
        base = self.scan_status.text().strip() or "MIDI Remixer is opening prepared ideas…"
        if " · working for " in base:
            base = base.split(" · working for ", 1)[0]
        self.guided_activity_label.setText(f"{base} · working for {elapsed:.0f}s")
