from __future__ import annotations

"""Persistent Remixer-owned pair routing decisions.

Library analysis is reusable across applications, but creative decisions such as
"these two source files are neither a useful mash nor a useful Version Fusion"
belong to MIDI Remixer.  Store them outside the Engine-owned catalogue so the
Library remains a factual analysis database and a later algorithm version can
clear/reclassify creative decisions without rebuilding 96K source files.
"""

from dataclasses import dataclass
import hashlib
import logging
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any

from core.remix_profile import MidiProfile

DISPOSITION_NEITHER = "neither"
_LOG = logging.getLogger(__name__)
SCHEMA_VERSION = 1


def variant_identity_key(profile: MidiProfile) -> str:
    """Return a stable *file/version* identity, not a composition identity."""
    content = str(getattr(profile, "content_fingerprint", "") or "").strip().casefold()
    if content:
        return "content:" + content
    fingerprint = str(getattr(profile, "fingerprint", "") or "").strip().casefold()
    if fingerprint:
        return "profile:" + fingerprint
    path = str(Path(str(getattr(profile, "path", "") or "")).expanduser()).casefold()
    return "path:" + hashlib.sha256(path.encode("utf-8", errors="replace")).hexdigest()[:32]


def ordered_variant_pair(a: MidiProfile, b: MidiProfile) -> tuple[str, str]:
    first, second = variant_identity_key(a), variant_identity_key(b)
    if first == second:
        # Two paths with identical content still need one deterministic row. The
        # duplicate is intentionally represented as one self-equivalent pair.
        return first, second
    return (first, second) if first < second else (second, first)


@dataclass(frozen=True)
class PairDisposition:
    identity_a: str
    identity_b: str
    label_a: str
    label_b: str
    disposition: str
    reason: str
    created_at: float
    updated_at: float


class PairDispositionStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.path, timeout=10.0, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=NORMAL")
        self._connection.execute("PRAGMA busy_timeout=10000")
        self._initialize()

    def _initialize(self) -> None:
        with self._lock:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS pair_dispositions_v1 (
                    identity_a TEXT NOT NULL,
                    identity_b TEXT NOT NULL,
                    label_a TEXT NOT NULL,
                    label_b TEXT NOT NULL,
                    disposition TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(identity_a, identity_b)
                );
                CREATE INDEX IF NOT EXISTS idx_pair_dispositions_kind_v1
                    ON pair_dispositions_v1(disposition, updated_at DESC);
                PRAGMA user_version=1;
                """
            )
            self._connection.commit()

    def classify_neither(self, a: MidiProfile, b: MidiProfile, *, reason: str) -> None:
        first, second = ordered_variant_pair(a, b)
        labels = {variant_identity_key(a): a.title or Path(a.path).stem, variant_identity_key(b): b.title or Path(b.path).stem}
        now = time.time()
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO pair_dispositions_v1(
                    identity_a, identity_b, label_a, label_b, disposition, reason, created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(identity_a, identity_b) DO UPDATE SET
                    label_a=excluded.label_a,
                    label_b=excluded.label_b,
                    disposition=excluded.disposition,
                    reason=excluded.reason,
                    updated_at=excluded.updated_at
                """,
                (first, second, labels.get(first, "Version A"), labels.get(second, "Version B"),
                 DISPOSITION_NEITHER, str(reason or "").strip(), now, now),
            )
            self._connection.commit()

    def is_neither(self, a: MidiProfile, b: MidiProfile) -> bool:
        first, second = ordered_variant_pair(a, b)
        with self._lock:
            row = self._connection.execute(
                "SELECT disposition FROM pair_dispositions_v1 WHERE identity_a=? AND identity_b=?",
                (first, second),
            ).fetchone()
        return bool(row and str(row[0]) == DISPOSITION_NEITHER)

    def restore(self, identity_a: str, identity_b: str) -> None:
        first, second = (str(identity_a), str(identity_b))
        if first > second:
            first, second = second, first
        with self._lock:
            self._connection.execute(
                "DELETE FROM pair_dispositions_v1 WHERE identity_a=? AND identity_b=?", (first, second)
            )
            self._connection.commit()

    def neither_pairs(self) -> list[PairDisposition]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT identity_a,identity_b,label_a,label_b,disposition,reason,created_at,updated_at
                FROM pair_dispositions_v1
                WHERE disposition=?
                ORDER BY updated_at DESC,label_a,label_b
                """,
                (DISPOSITION_NEITHER,),
            ).fetchall()
        return [
            PairDisposition(
                identity_a=str(row[0]), identity_b=str(row[1]), label_a=str(row[2]), label_b=str(row[3]),
                disposition=str(row[4]), reason=str(row[5] or ""), created_at=float(row[6]), updated_at=float(row[7]),
            )
            for row in rows
        ]

    def close(self) -> None:
        with self._lock:
            connection = self._connection
            if connection is None:
                return
            try:
                connection.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchall()
            except sqlite3.Error as exc:
                _LOG.debug("Pair disposition WAL checkpoint failed: %s", exc, exc_info=True)
            connection.close()
            self._connection = None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) FROM pair_dispositions_v1 WHERE disposition=?", (DISPOSITION_NEITHER,)
            ).fetchone()
        return {"schema_version": SCHEMA_VERSION, "neither_pairs": int(row[0] if row else 0), "path": str(self.path)}
