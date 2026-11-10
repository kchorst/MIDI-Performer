from __future__ import annotations

"""Owned, cancellable QThread operations for network/process/disk work."""

from collections.abc import Callable
import threading
from typing import Any

from PySide6.QtCore import QObject, QThread, Signal, Slot


OperationFunction = Callable[[threading.Event, Callable[[str, int], None]], Any]
SuccessCallback = Callable[[Any], None]
FailureCallback = Callable[[BaseException], None]
ProgressCallback = Callable[[str, int], None]


class _OperationThread(QThread):
    progress = Signal(str, int)
    succeeded = Signal(object)
    failed = Signal(object)

    def __init__(self, name: str, function: OperationFunction, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.operation_name = name
        self.function = function
        self.cancel_event = threading.Event()
        self.setObjectName(f"MIDI Remixer operation: {name}")

    def cancel(self) -> None:
        self.cancel_event.set()
        self.requestInterruption()

    def run(self) -> None:  # type: ignore[override]
        try:
            result = self.function(self.cancel_event, self.progress.emit)
            if self.cancel_event.is_set() or self.isInterruptionRequested():
                raise RuntimeError(f"{self.operation_name} was cancelled.")
        except Exception as exc:
            self.failed.emit(exc)
        else:
            self.succeeded.emit(result)


class OperationManager(QObject):
    """Own long-running operations and provide a shutdown barrier."""

    operationStarted = Signal(str)
    operationFinished = Signal(str)

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self._workers: dict[str, _OperationThread] = {}
        self._callbacks: dict[str, tuple[SuccessCallback, FailureCallback, ProgressCallback | None]] = {}
        self._shutting_down = False

    def is_active(self, name: str) -> bool:
        worker = self._workers.get(name)
        return bool(worker is not None and worker.isRunning())

    def active_names(self) -> tuple[str, ...]:
        return tuple(sorted(name for name, worker in self._workers.items() if worker.isRunning()))

    def start(
        self,
        name: str,
        function: OperationFunction,
        *,
        on_success: SuccessCallback,
        on_failure: FailureCallback,
        on_progress: ProgressCallback | None = None,
    ) -> bool:
        if self._shutting_down or self.is_active(name):
            return False
        worker = _OperationThread(name, function, self)
        self._workers[name] = worker
        self._callbacks[name] = (on_success, on_failure, on_progress)
        worker.progress.connect(lambda message, percent, key=name: self._handle_progress(key, message, percent))
        worker.succeeded.connect(lambda result, key=name: self._handle_success(key, result))
        worker.failed.connect(lambda error, key=name: self._handle_failure(key, error))
        worker.finished.connect(lambda key=name: self._handle_finished(key))
        self.operationStarted.emit(name)
        worker.start()
        return True

    @Slot(str, str, int)
    def _handle_progress(self, name: str, message: str, percent: int) -> None:
        callbacks = self._callbacks.get(name)
        if callbacks and callbacks[2] is not None and not self._shutting_down:
            callbacks[2](str(message), int(percent))

    @Slot(str, object)
    def _handle_success(self, name: str, result: object) -> None:
        callbacks = self._callbacks.get(name)
        if callbacks and not self._shutting_down:
            callbacks[0](result)

    @Slot(str, object)
    def _handle_failure(self, name: str, error: object) -> None:
        callbacks = self._callbacks.get(name)
        if not callbacks or self._shutting_down:
            return
        exc = error if isinstance(error, BaseException) else RuntimeError(str(error))
        callbacks[1](exc)

    @Slot(str)
    def _handle_finished(self, name: str) -> None:
        worker = self._workers.get(name)
        if worker is not None and not worker.isRunning():
            self._workers.pop(name, None)
            self._callbacks.pop(name, None)
            worker.deleteLater()
        self.operationFinished.emit(name)

    def cancel(self, name: str) -> bool:
        worker = self._workers.get(name)
        if worker is None:
            return True
        worker.cancel()
        return not worker.isRunning()

    def shutdown(self, timeout_ms: int = 8000) -> None:
        self._shutting_down = True
        workers = list(self._workers.items())
        for _name, worker in workers:
            worker.cancel()
        failures: list[str] = []
        per_worker = max(100, int(timeout_ms / max(1, len(workers))))
        for name, worker in workers:
            if worker.isRunning() and not worker.wait(per_worker):
                failures.append(name)
        if failures:
            raise RuntimeError("Background operations did not stop: " + ", ".join(failures))
        for name, worker in workers:
            self._workers.pop(name, None)
            self._callbacks.pop(name, None)
            worker.deleteLater()
