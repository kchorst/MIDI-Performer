from __future__ import annotations

"""Remixer-side read gate for Library v0.39.3 composition-family authority."""

from typing import Any


def family_reconciliation_readiness(status: dict[str, Any]) -> tuple[bool, str, str, int, int]:
    family = status.get("family_reconciliation")
    if not isinstance(family, dict):
        return False, "MIDI Library Manager v0.39.3 or newer is required before remix discovery.", "missing", 0, 0
    state = str(family.get("state") or "unknown").casefold()
    current = int(family.get("current") or 0)
    total = int(family.get("total") or 0)
    if state == "complete":
        return True, "Composition-family reconciliation is complete.", state, current, total
    if state == "running":
        progress = f" ({current:,}/{total:,})" if total else ""
        return False, f"MIDI Library Manager is reconciling composition families{progress}.", state, current, total
    if state == "cancelled":
        return False, "Composition-family reconciliation was cancelled; reopen MIDI Library Manager to complete it.", state, current, total
    return False, "Composition-family reconciliation has not completed yet.", state, current, total
