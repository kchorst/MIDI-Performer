from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import hashlib
import json
import os
from typing import TYPE_CHECKING, Iterable, Sequence

from core.remix_engine import RemixTake, take_identity_key

if TYPE_CHECKING:
    from app.decision_workflow import DecisionWorkflowStudio




def _normalized_path(path: str) -> str:
    try:
        return os.path.normcase(str(Path(path).expanduser().resolve(strict=False)))
    except (OSError, RuntimeError, ValueError):
        return os.path.normcase(str(path or ""))


_KEY_TO_PC = {
    "c": 0, "b#": 0,
    "c#": 1, "db": 1,
    "d": 2,
    "d#": 3, "eb": 3,
    "e": 4, "fb": 4,
    "e#": 5, "f": 5,
    "f#": 6, "gb": 6,
    "g": 7,
    "g#": 8, "ab": 8,
    "a": 9,
    "a#": 10, "bb": 10,
    "b": 11, "cb": 11,
}


@dataclass(frozen=True)
class WorkflowBranchKey:
    """Stable identity for one generated branch of a workflow.

    A branch changes when the source order or any generation-shaping value
    changes. Earlier Trials and Takes remain in project history, but the active
    Take must be cleared so playback and customization cannot silently target a
    previous branch.
    """

    source_paths: tuple[str, ...]
    target_bpm: int | None = None
    target_key_pc: int | None = None
    target_mode: str = ""
    bars: int | None = None
    section_bars: int | None = None
    recipe_keys: tuple[str, ...] = ()
    options: tuple[str, ...] = ()

    @classmethod
    def create(
        cls,
        source_paths: Iterable[str],
        *,
        target_bpm: int | None = None,
        target_key_pc: int | None = None,
        target_mode: str = "",
        bars: int | None = None,
        section_bars: int | None = None,
        recipe_keys: Iterable[str] = (),
        options: Iterable[object] = (),
    ) -> "WorkflowBranchKey":
        return cls(
            source_paths=tuple(_normalized_path(path) for path in source_paths),
            target_bpm=int(target_bpm) if target_bpm is not None else None,
            target_key_pc=int(target_key_pc) if target_key_pc is not None else None,
            target_mode=str(target_mode or "").casefold(),
            bars=int(bars) if bars is not None else None,
            section_bars=int(section_bars) if section_bars is not None else None,
            recipe_keys=tuple(str(value) for value in recipe_keys),
            options=tuple(str(value) for value in options),
        )

    @property
    def signature(self) -> str:
        """Compact deterministic fingerprint for feedback and diagnostics."""

        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def take_source_paths(take: RemixTake) -> tuple[str, ...]:
    return tuple(take.source_paths or tuple(path for path in (take.source_a, take.source_b) if path))


def target_key_parts(target_key: str) -> tuple[int | None, str | None]:
    """Parse a rendered Take key label into UI key/mode values."""

    parts = str(target_key or "").strip().split()
    if not parts:
        return None, None
    tonic = _KEY_TO_PC.get(parts[0].casefold())
    mode = parts[1].casefold() if len(parts) > 1 and parts[1].casefold() in {"major", "minor"} else None
    return tonic, mode


class WorkflowStateController:
    """Own exact Take selection, branch invalidation, and mode handoff.

    The wizards used to write separately to wizard state, studio state, table
    selection, playback state, and source state. Centralizing that transition
    prevents a newly described mash from replaying or customizing an older one.
    """

    def __init__(self, studio: "DecisionWorkflowStudio") -> None:
        self.studio = studio

    def resolve_take(self, take_or_id: RemixTake | str | None) -> RemixTake | None:
        if take_or_id is None:
            return None
        if isinstance(take_or_id, RemixTake):
            exact_key = take_identity_key(take_or_id)
            return next((take for take in self.studio.takes if take_identity_key(take) == exact_key), None)
        take_id = str(take_or_id)
        candidates = [take for take in self.studio.takes if take.take_id == take_id]
        if len(candidates) == 1:
            return candidates[0]
        active_trial = str(getattr(self.studio, "current_trial_id", "") or "")
        return next((take for take in candidates if take.trial_id == active_trial), candidates[-1] if candidates else None)

    def clear_active_take(self, *, clear_playback_reference: bool = True) -> None:
        """Clear only the active branch selection; preserve all project history."""

        self.studio.stop_playback()
        if clear_playback_reference:
            self.studio.current_playback_path = None
            self.studio.current_playback_label = None
            self.studio.current_playback_key = None
            self.studio.current_playback_generation = 0
        self.studio.selected_take = None
        table = getattr(self.studio, "takes_table", None)
        if table is not None:
            table.blockSignals(True)
            table.clearSelection()
            table.setCurrentCell(-1, -1)
            table.blockSignals(False)
        update = getattr(self.studio, "_update_take_actions", None)
        if callable(update):
            update()

    def select_take(self, take_or_id: RemixTake | str, *, refresh_table: bool = True) -> RemixTake:
        take = self.resolve_take(take_or_id)
        if take is None:
            take_id = take_or_id.take_id if isinstance(take_or_id, RemixTake) else str(take_or_id)
            raise LookupError(f"Take {take_id!r} is no longer available in this project.")

        self.studio.selected_take = take
        self.studio.project.set_takes(self.studio.takes)
        if refresh_table:
            self.studio._refresh_takes()
        self._select_table_row(take_identity_key(take))
        # Table selection normally updates this field. Reassert it in case the
        # table is not visible or has not yet been constructed.
        self.studio.selected_take = take
        return take

    def open_take_in_workbench(self, take_or_id: RemixTake | str, *, destination: int) -> RemixTake:
        """Open the exact generated Take and its sources in Creative Workbench."""

        take = self.resolve_take(take_or_id)
        if take is None:
            raise LookupError("The chosen Take no longer exists. Choose another Take before opening Creative Workbench.")

        generated_path = Path(take.path)
        if not generated_path.is_file():
            self.clear_active_take()
            raise FileNotFoundError(
                f"The generated file for {take.name or take.take_id} is missing. Choose or regenerate a Take before opening Creative Workbench."
            )

        self.studio.stop_playback()
        profile_paths = {_normalized_path(path): path for path in self.studio.profiles}
        requested_paths = take_source_paths(take)
        missing_sources = [path for path in requested_paths if _normalized_path(path) not in profile_paths]
        if missing_sources:
            raise LookupError(
                "The exact source set for this Take is no longer loaded: "
                + ", ".join(Path(path).name for path in missing_sources)
                + ". Re-add those songs before opening Creative Workbench."
            )
        paths = [profile_paths[_normalized_path(path)] for path in requested_paths]
        if paths:
            self.studio._add_source_paths(paths, replace=True)

        self._restore_take_frame(take)
        self.select_take(take, refresh_table=True)
        self.studio.choose_guidance_mode("workbench")
        # choose_guidance_mode may refresh controls, so select once more after
        # the mode transition and then navigate to the requested exact context.
        self.select_take(take, refresh_table=False)
        self.studio.navigate_to(destination, force=True)
        self._select_table_row(take_identity_key(take))
        self.studio.selected_take = take
        return take

    def _restore_take_frame(self, take: RemixTake) -> None:
        if hasattr(self.studio, "target_bpm"):
            self.studio.target_bpm.setValue(max(self.studio.target_bpm.minimum(), min(self.studio.target_bpm.maximum(), int(take.target_bpm))))
        tonic, mode = target_key_parts(take.target_key)
        if tonic is not None and hasattr(self.studio, "target_key"):
            index = self.studio.target_key.findData(tonic)
            if index >= 0:
                self.studio.target_key.setCurrentIndex(index)
        if mode is not None and hasattr(self.studio, "target_mode"):
            index = self.studio.target_mode.findData(mode)
            if index >= 0:
                self.studio.target_mode.setCurrentIndex(index)
        if hasattr(self.studio, "length_mode"):
            self.studio.length_mode.setCurrentText("Custom bars")
        if hasattr(self.studio, "bars_spin"):
            self.studio.bars_spin.setValue(max(self.studio.bars_spin.minimum(), min(self.studio.bars_spin.maximum(), int(take.bars))))
        if hasattr(self.studio, "_populate_recipe_table"):
            self.studio._populate_recipe_table()
        recipe = take.recipe or take.kind
        if recipe and hasattr(self.studio, "recipe_checks"):
            for button in self.studio.recipe_checks.values():
                button.setChecked(False)
            for prefix in ("pair:", "multi:", ""):
                button = self.studio.recipe_checks.get(f"{prefix}{recipe}")
                if button is not None and button.isEnabled():
                    button.setChecked(True)
                    break

    def _select_table_row(self, take_key: tuple[str, str, str]) -> None:
        table = getattr(self.studio, "takes_table", None)
        if table is None:
            return
        target_row = -1
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            raw_key = item.data(0x0100) if item is not None else None
            row_key = tuple(str(value) for value in raw_key) if isinstance(raw_key, (tuple, list)) and len(raw_key) == 3 else None
            if row_key == take_key:
                target_row = row
                break
        if target_row < 0:
            return
        table.blockSignals(True)
        table.clearSelection()
        table.setCurrentCell(target_row, 0)
        table.selectRow(target_row)
        table.scrollToItem(table.item(target_row, 0))
        table.blockSignals(False)
        changed = getattr(self.studio, "_take_selection_changed", None)
        if callable(changed):
            changed()
