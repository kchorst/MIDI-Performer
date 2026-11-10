from __future__ import annotations

"""Small reusable Qt components shared by the main and decision workflows."""

from typing import Any

from PySide6.QtCore import QByteArray, QEvent, QObject, Qt, Signal
from PySide6.QtWidgets import QTableWidget, QTableWidgetItem

SORT_ROLE = int(Qt.ItemDataRole.UserRole) + 1


def encode_qbytearray(value: QByteArray) -> str:
    return bytes(value.toBase64()).decode("ascii")


def decode_qbytearray(value: str | None) -> QByteArray:
    if not value:
        return QByteArray()
    try:
        return QByteArray.fromBase64(value.encode("ascii"))
    except (AttributeError, TypeError, ValueError):
        return QByteArray()


class LibraryScanBridge(QObject):
    """Typed Qt signal boundary for Remixer-owned Library observation work."""

    startupComplete = Signal(object)
    startupFailed = Signal(object)
    deepState = Signal(str, str)
    libraryChanged = Signal(str)


class SortableItem(QTableWidgetItem):
    def __init__(self, text: str, sort_value: Any = None) -> None:
        super().__init__(text)
        self.setData(SORT_ROLE, text if sort_value is None else sort_value)

    def __lt__(self, other: QTableWidgetItem) -> bool:
        left = self.data(SORT_ROLE)
        right = other.data(SORT_ROLE)
        try:
            return left < right
        except TypeError:
            return str(left).casefold() < str(right).casefold()


class HoverTableWidget(QTableWidget):
    hoverRowChanged = Signal(int)

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.viewport().installEventFilter(self)

    def mouseMoveEvent(self, event) -> None:
        self.hoverRowChanged.emit(self.rowAt(event.position().toPoint().y()))
        super().mouseMoveEvent(event)

    def eventFilter(self, watched, event) -> bool:
        if watched is self.viewport() and event.type() == QEvent.Type.Leave:
            self.hoverRowChanged.emit(-1)
        return super().eventFilter(watched, event)
