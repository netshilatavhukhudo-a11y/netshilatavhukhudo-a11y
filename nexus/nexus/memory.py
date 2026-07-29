"""Cross-step, cross-session memory.

v1 is a SQLite key-value table. The interface — `save` / `recall` / `search`
— is deliberately the shape a vector store would also satisfy, so v2 can swap
substring search for cosine similarity behind the same three methods without
touching any caller.

Concurrency: SQLite in WAL mode with a short busy timeout, so the agent loop
and a code_exec subprocess writing to the same store don't collide.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Iterable

from nexus import workspace_dir

__all__ = ["Memory"]


class Memory:
    """A durable key -> value store with newest-first substring search."""

    def __init__(self, path: str | Path | None = None) -> None:
        if path is None:
            path = workspace_dir() / "memory.sqlite3"
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), timeout=5.0, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                ts    REAL NOT NULL
            )
            """
        )

    # -- v1 interface (stable across the v2 vector upgrade) ----------------
    def save(self, key: str, value: str) -> None:
        """Upsert. A later save to the same key overwrites and re-timestamps."""
        if not key or not key.strip():
            raise ValueError("memory key must be non-empty")
        self._conn.execute(
            "INSERT INTO memory(key, value, ts) VALUES(?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, ts=excluded.ts",
            (key.strip(), str(value), time.time()),
        )

    def recall(self, key: str) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM memory WHERE key = ?", (key.strip(),)
        ).fetchone()
        return row[0] if row else None

    def search(self, query: str, limit: int = 10) -> list[str]:
        """v1: case-insensitive substring over key and value, newest first.

        v2 will embed values and rank by cosine similarity; the return type
        (a ranked list of values) is unchanged, so callers stay put.
        """
        needle = f"%{(query or '').strip()}%"
        rows = self._conn.execute(
            "SELECT value FROM memory WHERE key LIKE ? OR value LIKE ? "
            "ORDER BY ts DESC LIMIT ?",
            (needle, needle, limit),
        ).fetchall()
        return [r[0] for r in rows]

    # -- housekeeping ------------------------------------------------------
    def keys(self) -> Iterable[str]:
        return [r[0] for r in self._conn.execute("SELECT key FROM memory ORDER BY ts DESC")]

    def delete(self, key: str) -> bool:
        cur = self._conn.execute("DELETE FROM memory WHERE key = ?", (key.strip(),))
        return cur.rowcount > 0

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Memory":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
