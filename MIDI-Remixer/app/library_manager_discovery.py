from __future__ import annotations

"""Pure discovery helpers for the separately installable MIDI Library Manager.

This module deliberately has no Qt dependency so separate-install discovery can be
qualified on every platform and reused by the Windows RC runner.  It discovers
launchers only; launching remains a UI/application responsibility.
"""

from pathlib import Path
from typing import Iterable


LIBRARY_MANAGER_LAUNCHERS = (
    "Launch MIDI Library Manager.bat",
    "MIDI Library Manager.pyw",
    "launch.bat",
)


def _resolved(value: object) -> Path | None:
    if value is None or not str(value).strip():
        return None
    try:
        return Path(str(value)).expanduser().resolve(strict=False)
    except (OSError, TypeError, ValueError):
        return None


def library_manager_candidates(
    remixer_root: str | Path,
    *,
    remembered_home: object = None,
    environment_home: object = None,
    engine_source_root: object = None,
) -> tuple[Path, ...]:
    """Return deterministic Library Manager launcher candidates in priority order.

    Priority is remembered installation, explicit environment override, live
    Engine source root, co-located/sibling installation, then MIDI/Library-named
    sibling directories.  Missing candidates are retained so callers can apply
    their own existence/launch policy without duplicating discovery semantics.
    """

    root = _resolved(remixer_root)
    if root is None:
        return ()

    candidates: list[Path] = []
    seen: set[str] = set()

    def add_candidate(path: Path) -> None:
        key = str(path).casefold()
        if key in seen:
            return
        seen.add(key)
        candidates.append(path)

    def add_root(value: object) -> None:
        path = _resolved(value)
        if path is None:
            return
        if path.is_file():
            add_candidate(path)
            return
        for name in LIBRARY_MANAGER_LAUNCHERS:
            add_candidate(path / name)

    add_root(remembered_home)
    add_root(environment_home)
    add_root(engine_source_root)
    add_root(root)
    add_root(root.parent)

    try:
        siblings: Iterable[Path] = sorted(root.parent.iterdir(), key=lambda item: item.name.casefold())
    except OSError:
        siblings = ()
    for sibling in siblings:
        if not sibling.is_dir():
            continue
        folded = sibling.name.casefold()
        if "library" in folded and "midi" in folded:
            add_root(sibling)

    return tuple(candidates)


def first_existing_library_manager_launcher(
    remixer_root: str | Path,
    *,
    remembered_home: object = None,
    environment_home: object = None,
    engine_source_root: object = None,
) -> Path | None:
    """Return the first existing file from :func:`library_manager_candidates`."""

    for candidate in library_manager_candidates(
        remixer_root,
        remembered_home=remembered_home,
        environment_home=environment_home,
        engine_source_root=engine_source_root,
    ):
        if candidate.is_file():
            return candidate
    return None
