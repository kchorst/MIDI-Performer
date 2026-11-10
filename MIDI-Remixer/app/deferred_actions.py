from __future__ import annotations

"""QObject-owned deferred callbacks that are cancelled with their owner."""

from collections.abc import Callable

from PySide6.QtCore import QObject, QTimer


class DeferredActions(QObject):
    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self._timers: dict[str, QTimer] = {}

    def schedule(self, key: str, delay_ms: int, callback: Callable[[], None]) -> None:
        self.cancel(key)
        timer = QTimer(self)
        timer.setSingleShot(True)

        def fire() -> None:
            self._timers.pop(key, None)
            callback()
            timer.deleteLater()

        timer.timeout.connect(fire)
        self._timers[key] = timer
        timer.start(max(0, int(delay_ms)))

    def cancel(self, key: str) -> None:
        timer = self._timers.pop(key, None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()

    def cancel_all(self) -> None:
        for key in list(self._timers):
            self.cancel(key)


def schedule_owned(owner: QObject, key: str, delay_ms: int, callback: Callable[[], None]) -> None:
    manager = getattr(owner, "_midi_remixer_deferred_actions", None)
    if not isinstance(manager, DeferredActions):
        manager = DeferredActions(owner)
        owner._midi_remixer_deferred_actions = manager
    manager.schedule(key, delay_ms, callback)


def cancel_owned(owner: QObject) -> None:
    manager = getattr(owner, "_midi_remixer_deferred_actions", None)
    if isinstance(manager, DeferredActions):
        manager.cancel_all()
