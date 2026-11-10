from __future__ import annotations

"""Normalize authorized CSV/XLSX relationship spreadsheets for MIDI Remixer.

This helper never fetches network data and never evaluates workbook formulas.
It converts a small, explicit set of columns into the JSON schema consumed by
``core.reference_catalog.ReferenceCatalog``.
"""

from pathlib import Path
import csv
from typing import Iterable

MAX_ROWS = 100_000
MAX_FILE_BYTES = 100 * 1024 * 1024


def _text(value: object) -> str:
    return str(value or "").strip()


def _int(value: object, default: int = 0) -> int:
    try:
        return int(float(_text(value)))
    except (TypeError, ValueError):
        return default


def _canonical_row(row: dict[object, object]) -> dict[str, object]:
    return {str(key or "").strip().casefold().replace(" ", "_"): value for key, value in row.items() if key is not None}


def read_rows(path: str | Path) -> list[dict[str, object]]:
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(source)
    if source.stat().st_size > MAX_FILE_BYTES:
        raise ValueError(f"Reference file exceeds {MAX_FILE_BYTES // (1024 * 1024)} MB safety limit.")
    suffix = source.suffix.casefold()
    if suffix == ".csv":
        with source.open("r", encoding="utf-8-sig", newline="") as fh:
            rows = [_canonical_row(row) for row in csv.DictReader(fh)]
        return rows[:MAX_ROWS]
    if suffix not in {".xlsx", ".xlsm"}:
        raise ValueError("Only CSV, XLSX, and XLSM spreadsheet imports are supported by this helper.")
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise RuntimeError(
            "Excel import requires openpyxl. Run Install Requirements.bat or import the same data as CSV."
        ) from exc
    workbook = load_workbook(source, read_only=True, data_only=True, keep_links=False)
    try:
        worksheet = workbook.active
        iterator = worksheet.iter_rows(values_only=True)
        headers = next(iterator, None)
        if not headers:
            return []
        keys = [str(value or "").strip().casefold().replace(" ", "_") for value in headers]
        rows: list[dict[str, object]] = []
        for values in iterator:
            if len(rows) >= MAX_ROWS:
                break
            row = {key: value for key, value in zip(keys, values) if key}
            if any(_text(value) for value in row.values()):
                rows.append(row)
        return rows
    finally:
        workbook.close()


def _first(row: dict[str, object], *keys: str) -> str:
    for key in keys:
        value = _text(row.get(key))
        if value:
            return value
    return ""


def normalize_rows(rows: Iterable[dict[str, object]], *, provider: str = "Imported spreadsheet") -> dict[str, object]:
    candidates: list[dict[str, object]] = []
    popularity: list[dict[str, object]] = []
    skipped = 0
    for raw in rows:
        row = _canonical_row(raw)
        title_a = _first(row, "anchor_title", "title_a", "source_title", "song_a", "track_a")
        title_b = _first(row, "candidate_title", "title_b", "related_title", "song_b", "track_b")
        title = _first(row, "title", "song", "track")
        rank = _int(row.get("rank"), 0)
        familiarity = _int(row.get("familiarity_score"), 0)
        year = _int(row.get("year"), 0)
        if title_a and title_b:
            confidence = max(0, min(100, _int(row.get("confidence"), 45)))
            candidates.append({
                "anchor_title": title_a,
                "candidate_title": title_b,
                "anchor_artist": _first(row, "anchor_artist", "artist_a", "source_artist"),
                "candidate_artist": _first(row, "candidate_artist", "artist_b", "related_artist"),
                "provider": _first(row, "provider", "source", "source_name") or provider,
                "evidence_type": _first(row, "evidence_type", "relationship_type", "type") or "imported candidate suggestion",
                "relationship_tags": _first(row, "relationship_tags", "tags", "relationship"),
                "source_url": _first(row, "source_url", "url", "link"),
                "confidence": confidence,
                "note": _first(row, "note", "notes", "comment", "comments"),
            })
        elif title and (rank or familiarity or year):
            popularity.append({
                "artist": _first(row, "artist", "composer"),
                "title": title,
                "rank": rank,
                "year": year,
                "familiarity_score": max(0, min(100, familiarity)),
                "provider": _first(row, "provider", "source", "source_name") or provider,
                "source_url": _first(row, "source_url", "url", "link"),
            })
        else:
            skipped += 1
    return {
        "format": "MIDI Remixer normalized reference evidence",
        "version": 1,
        "provider": provider,
        "external_candidates": candidates,
        "song_popularity": popularity,
        "import_summary": {
            "candidates": len(candidates),
            "popularity": len(popularity),
            "skipped": skipped,
        },
    }
