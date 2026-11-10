from __future__ import annotations

"""Bounded read-only access to the shared persistent MIDI catalogue.

MIDI Library Engine remains the sole writer. Remixer uses this reader only for
browsing/selection so a 96K catalogue is never materialized into a Qt table and
the browser remains usable while Library Manager continues scanning.
"""

from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
import sqlite3
from typing import Any

CATALOGUE_TABLE = "library_catalog_files_v11"
PAGE_SIZE_DEFAULT = 250
PAGE_SIZE_MAX = 1000


@dataclass(frozen=True, slots=True)
class CataloguePage:
    rows: tuple[dict[str, Any], ...]
    total_matches: int
    offset: int
    limit: int


class CatalogueReader:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)

    def exists(self) -> bool:
        return self.database_path.is_file()

    def _connect(self) -> sqlite3.Connection:
        if not self.database_path.is_file():
            raise FileNotFoundError(f"Saved MIDI catalogue not found: {self.database_path}")
        uri = self.database_path.resolve(strict=False).as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=2.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA busy_timeout=1500")
        return connection

    def summary(self) -> dict[str, Any]:
        if not self.exists():
            return {"available": False, "total": 0, "catalogued": 0, "quick_failed": 0, "complete": 0, "failed": 0}
        try:
            with closing(self._connect()) as conn:
                row = conn.execute(
                    f"SELECT COUNT(*) total, SUM(CASE WHEN quick_status='failed' THEN 1 ELSE 0 END) quick_failed, "
                    "SUM(CASE WHEN quick_status<>'failed' AND deep_status='complete' THEN 1 ELSE 0 END) complete, "
                    "SUM(CASE WHEN quick_status<>'failed' AND deep_status='failed' THEN 1 ELSE 0 END) failed, "
                    "SUM(CASE WHEN lifecycle_state='blessed' AND eligibility_state='mash_ready' THEN 1 ELSE 0 END) mash_ready, "
                    "SUM(CASE WHEN lifecycle_state='blessed' AND eligibility_state='fusion_candidate' THEN 1 ELSE 0 END) fusion_candidate, "
                    "SUM(CASE WHEN lifecycle_state='blessed' AND eligibility_state='usable_not_mash' THEN 1 ELSE 0 END) usable_not_mash "
                    f"FROM {CATALOGUE_TABLE}"
                ).fetchone()
        except (sqlite3.Error, OSError):
            return {"available": False, "total": 0, "catalogued": 0, "quick_failed": 0, "complete": 0, "failed": 0}
        total = int(row["total"] or 0) if row else 0
        quick_failed = int(row["quick_failed"] or 0) if row else 0
        return {
            "available": True,
            "total": total,
            "catalogued": max(0, total - quick_failed),
            "quick_failed": quick_failed,
            "complete": int(row["complete"] or 0) if row else 0,
            "failed": int(row["failed"] or 0) if row else 0,
            "mash_ready": int(row["mash_ready"] or 0) if row else 0,
            "fusion_candidate": int(row["fusion_candidate"] or 0) if row else 0,
            "usable_not_mash": int(row["usable_not_mash"] or 0) if row else 0,
        }

    @staticmethod
    def _filter_clause(search: str, status: str) -> tuple[str, list[object]]:
        clauses: list[str] = []
        params: list[object] = []
        text = str(search or "").strip()
        if text:
            pattern = f"%{text}%"
            clauses.append(
                "(filename LIKE ? COLLATE NOCASE OR path LIKE ? COLLATE NOCASE "
                "OR COALESCE(catalog_title,'') LIKE ? COLLATE NOCASE "
                "OR COALESCE(catalog_artist,'') LIKE ? COLLATE NOCASE)"
            )
            params.extend([pattern, pattern, pattern, pattern])
        state = str(status or "all").casefold()
        if state == "mash_ready":
            clauses.append("lifecycle_state='blessed' AND eligibility_state='mash_ready'")
        elif state == "fusion_eligible":
            clauses.append("lifecycle_state='blessed' AND eligibility_state IN ('mash_ready','fusion_candidate')")
        elif state == "fusion_candidate":
            clauses.append("lifecycle_state='blessed' AND eligibility_state='fusion_candidate'")
        elif state == "usable_not_mash":
            clauses.append("lifecycle_state='blessed' AND eligibility_state='usable_not_mash'")
        elif state == "needs_review":
            clauses.append("eligibility_state='needs_review'")
        elif state == "quarantined":
            clauses.append("eligibility_state='quarantined'")
        elif state == "rejected":
            clauses.append("eligibility_state='rejected'")
        elif state == "quick_failed":
            clauses.append("quick_status='failed'")
        elif state in {"pending", "paused", "analyzing", "complete", "failed"}:
            clauses.append("quick_status<>'failed' AND deep_status=?")
            params.append(state)
        elif state == "unfinished":
            clauses.append("quick_status<>'failed' AND COALESCE(deep_status,'pending')<>'complete'")
        elif state == "usable":
            clauses.append("lifecycle_state='blessed' AND eligibility_state IN ('mash_ready','fusion_candidate','usable_not_mash')")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        return where, params

    def page(
        self,
        *,
        search: str = "",
        status: str = "usable",
        sort_key: str = "title",
        descending: bool = False,
        offset: int = 0,
        limit: int = PAGE_SIZE_DEFAULT,
    ) -> CataloguePage:
        limit = max(1, min(PAGE_SIZE_MAX, int(limit)))
        offset = max(0, int(offset))
        order_columns = {
            "title": "COALESCE(NULLIF(catalog_title,''),filename) COLLATE NOCASE",
            "artist": "COALESCE(catalog_artist,'') COLLATE NOCASE",
            "filename": "filename COLLATE NOCASE",
            "path": "path COLLATE NOCASE",
            "duration": "COALESCE(duration_seconds,0)",
            "bpm": "COALESCE(bpm,0)",
            "key": "COALESCE(detected_key,'') COLLATE NOCASE",
            "tracks": "track_count",
            "notes": "note_count",
            "deep_status": "COALESCE(deep_status,'pending') COLLATE NOCASE",
            "arrangement": "COALESCE(arrangement_type,'') COLLATE NOCASE",
            "eligibility": "COALESCE(eligibility_state,'unresolved') COLLATE NOCASE",
        }
        order = order_columns.get(str(sort_key or "title"), order_columns["title"])
        direction = "DESC" if bool(descending) else "ASC"
        where, params = self._filter_clause(search, status)
        with closing(self._connect()) as conn:
            total_row = conn.execute(f"SELECT COUNT(*) FROM {CATALOGUE_TABLE}{where}", tuple(params)).fetchone()
            rows = conn.execute(
                "SELECT path,filename,COALESCE(NULLIF(catalog_title,''),filename) display_title,"
                "COALESCE(catalog_artist,'') artist,duration_seconds,bpm,detected_key,mode,meter,"
                "track_count,note_count,arrangement_type,quick_status,lifecycle_state,eligibility_state,COALESCE(deep_status,'pending') deep_status,error,deep_last_error "
                f"FROM {CATALOGUE_TABLE}{where} ORDER BY {order} {direction},path COLLATE NOCASE ASC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
        payload: list[dict[str, Any]] = []
        for row in rows:
            path = str(row["path"] or "")
            payload.append({
                "path": path,
                "folder": str(Path(path).parent) if path else "",
                "filename": str(row["filename"] or ""),
                "title": str(row["display_title"] or ""),
                "artist": str(row["artist"] or ""),
                "duration_seconds": float(row["duration_seconds"]) if row["duration_seconds"] is not None else None,
                "bpm": float(row["bpm"]) if row["bpm"] is not None else None,
                "key": str(row["detected_key"] or ""),
                "mode": str(row["mode"] or ""),
                "meter": str(row["meter"] or ""),
                "tracks": int(row["track_count"] or 0),
                "notes": int(row["note_count"] or 0),
                "arrangement": str(row["arrangement_type"] or ""),
                "quick_status": str(row["quick_status"] or ""),
                "lifecycle_state": str(row["lifecycle_state"] or ""),
                "eligibility_state": str(row["eligibility_state"] or "unresolved"),
                "deep_status": str(row["deep_status"] or "pending"),
                "error": str(row["deep_last_error"] or row["error"] or ""),
            })
        return CataloguePage(tuple(payload), int(total_row[0] or 0) if total_row else 0, offset, limit)
