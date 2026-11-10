from __future__ import annotations

"""Library metadata-index ownership and path helpers."""

from pathlib import Path
from typing import TYPE_CHECKING, Iterable

from core.candidate_index import CandidateIndex, CandidateIndexStats, candidate_neighbor_map
from core.operation_diagnostics import write_diagnostic
from core.remix_profile import MidiProfile

if TYPE_CHECKING:
    from app.studio_window import MidiRemixerStudio


class LibraryController:
    """Own non-visual library state that used to be recreated by each screen."""

    _EAGER_INDEX_LIMIT = 2000

    def __init__(self, studio: "MidiRemixerStudio") -> None:
        self.studio = studio
        self._neighbor_map: dict[str, tuple[str, ...]] = {}
        self._large_index: CandidateIndex | None = None
        self._large_index_count = 0
        self._stats: CandidateIndexStats | None = None

    @property
    def candidate_index_stats(self) -> CandidateIndexStats | None:
        return self._stats

    @property
    def large_index(self) -> CandidateIndex | None:
        return self._large_index

    def install_candidate_index(self, index: CandidateIndex, *, signature: str = "shared-large-library") -> CandidateIndexStats:
        """Install an immutable index prepared off the GUI thread.

        Six-figure libraries must never rebuild this structure from a UI callback.
        """
        self._neighbor_map = {}
        self._large_index = index
        self._large_index_count = len(index.profiles)
        self._stats = CandidateIndexStats(self._large_index_count, 0, False, signature)
        return self._stats

    def invalidate_candidate_index(self) -> None:
        self._neighbor_map = {}
        self._large_index = None
        self._large_index_count = 0
        self._stats = None

    def rebuild_candidate_index(self, profiles: Iterable[MidiProfile] | None = None) -> CandidateIndexStats | None:
        source = profiles if profiles is not None else self.studio.profiles.values()
        try:
            count = len(source)  # dict_values and ordinary sequences are O(1)
        except TypeError:
            items = list(source)
            count = len(items)
        else:
            items = None
        if count < 2:
            self.invalidate_candidate_index()
            return None
        if count > self._EAGER_INDEX_LIMIT:
            # Large indexes are prepared by the existing startup/scan worker and
            # installed with install_candidate_index(). Never rebuild 96k rows
            # synchronously merely because a screen asked for candidates.
            if self._large_index is not None and self._large_index_count == count:
                return self._stats
            self._neighbor_map = {}
            self._large_index = None
            self._large_index_count = count
            self._stats = CandidateIndexStats(count, 0, False, "awaiting-background-index")
            return self._stats
        if items is None:
            items = list(source)
        try:
            self._neighbor_map, self._stats = candidate_neighbor_map(items, app_root=self.studio.root, per_anchor=48)
            self._large_index = None
            self._large_index_count = 0
            return self._stats
        except (OSError, ValueError, TypeError) as exc:
            write_diagnostic(self.studio.paths.logs / "ui-errors.log", "Build library candidate index", exc, profile_count=len(items))
            self._neighbor_map = {}
            self._stats = None
            return None

    def candidate_paths(self, anchor_path: str, *, limit: int = 48) -> tuple[str, ...]:
        count = len(self.studio.profiles)
        if count > self._EAGER_INDEX_LIMIT:
            # Large-library candidate construction is intentionally performed by
            # owned background operations (Guided/Assisted/Workbench). Never
            # build a 96k CandidateIndex from a synchronous UI helper.
            if self._large_index is None or self._large_index_count != count:
                return ()
            return self._large_index.candidate_paths(anchor_path, limit=max(1, int(limit)))
        if anchor_path not in self._neighbor_map and count >= 2:
            self.rebuild_candidate_index()
        return self._neighbor_map.get(anchor_path, ())[: max(1, int(limit))]

    def normalised_paths(self, raw_paths: Iterable[object]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for raw in raw_paths:
            try:
                path = str(Path(str(raw)).expanduser().resolve())
            except (OSError, ValueError, TypeError):
                continue
            key = path.casefold()
            if key not in seen:
                result.append(path)
                seen.add(key)
        return result
