from __future__ import annotations

from pathlib import Path


class UnavailableLibraryIndex:
    """Read-only empty index before Library Manager establishes a Library."""

    database_path = Path()

    def close(self) -> None: return None
    def invalidate(self, *args, **kwargs) -> None: return None
    def load_many(self, *args, **kwargs) -> dict: return {}
    def load_one(self, *args, **kwargs): return None
    def readiness_summary(self) -> dict: return {}
    def readiness_for_path(self, *args, **kwargs): return None
    def assisted_ready_paths(self, *args, **kwargs) -> tuple: return ()
