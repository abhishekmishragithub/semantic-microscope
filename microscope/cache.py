"""SQLite key/value cache so re-runs with an unchanged preset never touch the network."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any


def cache_key(
    sentence: str, prev: str | None, nxt: str | None, preset_digest: str, model: str
) -> str:
    blob = json.dumps([sentence, prev, nxt, preset_digest, model])
    return hashlib.sha256(blob.encode()).hexdigest()


class Cache:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL, "
            "created REAL NOT NULL DEFAULT (unixepoch('subsec')))"
        )
        self._pending = 0

    def get(self, key: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key: str, value: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (key, json.dumps(value))
        )
        self._pending += 1
        if self._pending >= 25:
            self.commit()

    def commit(self) -> None:
        self.conn.commit()
        self._pending = 0

    def close(self) -> None:
        self.commit()
        self.conn.close()
