from __future__ import annotations

"""Explicit application-wide action-button semantics and sizing."""

from enum import Enum

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QPushButton, QSizePolicy, QWidget


class ActionRole(str, Enum):
    UTILITY = "UtilityButton"
    PRIMARY = "PrimaryButton"
    BACK = "BackButton"
    PLAY = "PlayButton"
    STOP = "StopButton"
    SUCCESS = "SuccessButton"
    DANGER = "DangerButton"
    DECISION = "DecisionButton"
    WIZARD_CHOICE = "WizardChoiceButton"
    ASSISTED_CHOICE = "AssistedChoice"
    RECIPE = "RecipeSelectButton"


_WIDE_ROLES = {
    ActionRole.DECISION.value,
    ActionRole.WIZARD_CHOICE.value,
    ActionRole.ASSISTED_CHOICE.value,
    ActionRole.RECIPE.value,
}


def set_action_role(button: QPushButton, role: ActionRole | str, *, wide: bool | None = None) -> QPushButton:
    role_value = role.value if isinstance(role, ActionRole) else str(role)
    button.setObjectName(role_value)
    button.setProperty("actionRole", role_value)
    if wide is not None:
        button.setProperty("wideAction", bool(wide))
    polish_action_button(button)
    return button


def action_button(text: str, role: ActionRole = ActionRole.UTILITY, parent: QWidget | None = None) -> QPushButton:
    """Create a button with a semantic role at construction time."""

    return set_action_role(QPushButton(text, parent), role)


def polish_action_button(button: QPushButton) -> None:
    """Apply a stable visual/interaction policy without inferring from copy."""

    role = str(button.property("actionRole") or button.objectName() or ActionRole.UTILITY.value)
    if not button.objectName():
        button.setObjectName(role)
    button.setProperty("actionRole", role)
    button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
    button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    button.setAutoDefault(False)
    button.setDefault(False)
    button.setMinimumHeight(max(30, button.minimumHeight()))

    wide = bool(button.property("wideAction")) or role in _WIDE_ROLES
    policy = QSizePolicy.Policy.Expanding if wide else QSizePolicy.Policy.Maximum
    button.setSizePolicy(policy, QSizePolicy.Policy.Fixed)

    if not button.accessibleName().strip():
        button.setAccessibleName(" ".join(button.text().replace("&", "").split()))
    disabled_reason = str(button.property("disabledReason") or "").strip()
    if disabled_reason and not button.toolTip().strip():
        button.setToolTip(disabled_reason)
        button.setStatusTip(disabled_reason)


def set_action_enabled(button: QPushButton, enabled: bool, reason: str = "") -> None:
    previous_reason = str(button.property("disabledReason") or "")
    next_reason = "" if enabled else str(reason or "Action is not available yet.")
    button.setProperty("disabledReason", next_reason)
    button.setEnabled(bool(enabled))
    if not enabled:
        button.setToolTip(next_reason)
        button.setStatusTip(next_reason)
    else:
        if previous_reason and button.toolTip() == previous_reason:
            button.setToolTip("")
        if previous_reason and button.statusTip() == previous_reason:
            button.setStatusTip("")
    polish_action_button(button)


class _ButtonPolicyFilter(QObject):
    """Polish only the child that was added; never queue QObject wrappers."""

    def eventFilter(self, watched, event) -> bool:  # type: ignore[override]
        if event.type() != QEvent.Type.ChildAdded:
            return False
        child_getter = getattr(event, "child", None)
        child = child_getter() if callable(child_getter) else None
        try:
            if isinstance(child, QPushButton):
                polish_action_button(child)
            if isinstance(child, QWidget):
                child.installEventFilter(self)
        except RuntimeError as exc:
            # A native internal child may be destroyed during its own ChildAdded
            # delivery. No reference is retained and no deferred callback exists.
            if "already deleted" not in str(exc).casefold():
                raise
        return False


def polish_action_buttons(root: QWidget) -> None:
    for button in root.findChildren(QPushButton):
        polish_action_button(button)


def install_button_policy(root: QWidget) -> None:
    polish_action_buttons(root)
    if getattr(root, "_midi_remixer_button_policy", None) is not None:
        return
    policy = _ButtonPolicyFilter(root)
    root.installEventFilter(policy)
    for widget in root.findChildren(QWidget):
        try:
            widget.installEventFilter(policy)
        except RuntimeError as exc:
            if "already deleted" not in str(exc).casefold():
                raise
    root._midi_remixer_button_policy = policy
