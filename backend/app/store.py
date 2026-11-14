"""SQLite: paragraph cache, paragraph sightings, LLM usage log.

If data/ is not writable (e.g. the host bind mount is owned by root), we fall back to an
in-memory database so the app still starts; /api/health then reports storage "memory".
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from app import config

log = logging.getLogger("stoprop.store")

SCHEMA = """
CREATE TABLE IF NOT EXISTS paragraph_cache (
  key TEXT PRIMARY KEY,
  para_hash TEXT NOT NULL,
  model TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  taxonomy_version INTEGER NOT NULL,
  result TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS paragraph_seen (
  para_hash TEXT PRIMARY KEY,
  seen_count INTEGER NOT NULL DEFAULT 0,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS paragraph_domains (
  para_hash TEXT NOT NULL,
  domain TEXT NOT NULL,
  PRIMARY KEY (para_hash, domain)
);
CREATE TABLE IF NOT EXISTS usage_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  day TEXT NOT NULL,
  kind TEXT NOT NULL,
  model TEXT NOT NULL,
  input_tokens INTEGER NOT NULL DEFAULT 0,
  output_tokens INTEGER NOT NULL DEFAULT 0,
  latency_ms INTEGER NOT NULL DEFAULT 0,
  ok INTEGER NOT NULL,
  error TEXT
);
CREATE INDEX IF NOT EXISTS usage_day ON usage_log(day);
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def today() -> str:
    """Budget day = UTC date."""
    return _now().strftime("%Y-%m-%d")


class Store:
    def __init__(self, path: str | Path | None = None):
        self._lock = threading.Lock()
        self.persistent = False
        if path is not None and str(path) != ":memory:":
            try:
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                self._db = sqlite3.connect(str(path), check_same_thread=False)
                self._db.execute("PRAGMA journal_mode=WAL")
                self._db.executescript(SCHEMA)
                self.persistent = True
            except (sqlite3.Error, OSError) as exc:
                log.warning("SQLite at %s not usable (%s); using in-memory storage", path, type(exc).__name__)
        if not self.persistent:
            self._db = sqlite3.connect(":memory:", check_same_thread=False)
            self._db.executescript(SCHEMA)

    @property
    def kind(self) -> str:
        return "sqlite" if self.persistent else "memory"

    def _exec(self, sql: str, params: tuple = ()) -> list[tuple]:
        with self._lock:
            try:
                cur = self._db.execute(sql, params)
                rows = cur.fetchall()
                self._db.commit()
                return rows
            except sqlite3.Error as exc:  # never let storage break an analysis
                log.warning("SQLite error: %s", exc)
                return []

    # ------------------------------------------------------------ cache
    def cache_get(self, key: str) -> list | None:
        rows = self._exec("SELECT result FROM paragraph_cache WHERE key = ?", (key,))
        return json.loads(rows[0][0]) if rows else None

    def cache_put(self, key: str, para_hash: str, model: str, prompt_version: str,
                  taxonomy_version: int, result: list) -> None:
        self._exec(
            "INSERT OR REPLACE INTO paragraph_cache VALUES (?, ?, ?, ?, ?, ?, ?)",
            (key, para_hash, model, prompt_version, taxonomy_version, json.dumps(result, ensure_ascii=False),
             _now().isoformat(timespec="seconds")),
        )

    def cache_size(self) -> int:
        rows = self._exec("SELECT COUNT(*) FROM paragraph_cache")
        return rows[0][0] if rows else 0

    # ------------------------------------------------------ sightings
    def note_seen(self, para_hash: str, domain: str | None = None) -> None:
        now = _now().isoformat(timespec="seconds")
        self._exec(
            "INSERT INTO paragraph_seen VALUES (?, 1, ?, ?) "
            "ON CONFLICT(para_hash) DO UPDATE SET seen_count = seen_count + 1, last_seen = excluded.last_seen",
            (para_hash, now, now),
        )
        if domain:
            self._exec("INSERT OR IGNORE INTO paragraph_domains VALUES (?, ?)", (para_hash, domain.lower()))

    def seen(self, para_hash: str) -> tuple[int, int]:
        """(seen_count, distinct_domains) for a paragraph hash."""
        a = self._exec("SELECT seen_count FROM paragraph_seen WHERE para_hash = ?", (para_hash,))
        b = self._exec("SELECT COUNT(*) FROM paragraph_domains WHERE para_hash = ?", (para_hash,))
        return (a[0][0] if a else 0, b[0][0] if b else 0)

    # ---------------------------------------------------------- usage
    def log_usage(self, kind: str, model: str, input_tokens: int, output_tokens: int,
                  latency_ms: int, ok: bool, error: str | None = None) -> None:
        now = _now()
        self._exec(
            "INSERT INTO usage_log (ts, day, kind, model, input_tokens, output_tokens, latency_ms, ok, error) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (now.isoformat(timespec="seconds"), now.strftime("%Y-%m-%d"), kind, model,
             int(input_tokens), int(output_tokens), int(latency_ms), 1 if ok else 0, error),
        )

    def usage_today(self) -> dict:
        rows = self._exec(
            "SELECT COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0), COUNT(*), "
            "COALESCE(SUM(1-ok),0) FROM usage_log WHERE day = ?",
            (today(),),
        )
        i, o, calls, errors = rows[0] if rows else (0, 0, 0, 0)
        return {"input_tokens": i, "output_tokens": o, "tokens": i + o, "calls": calls, "errors": errors}

    def usage_log(self) -> list[dict]:
        cols = ("ts", "kind", "model", "input_tokens", "output_tokens", "latency_ms", "ok", "error")
        rows = self._exec(f"SELECT {', '.join(cols)} FROM usage_log ORDER BY id")
        return [dict(zip(cols, r)) for r in rows]

    def usage_rows(self) -> list[tuple]:
        return self._exec("SELECT kind, model, input_tokens, output_tokens, ok, error FROM usage_log ORDER BY id")


_store: Store | None = None
_store_lock = threading.Lock()


def get_store() -> Store:
    global _store
    with _store_lock:
        if _store is None:
            _store = Store(config.DB_PATH)
        return _store


def set_store(store: Store | None) -> None:
    """For tests."""
    global _store
    with _store_lock:
        _store = store


def monotonic_ms() -> int:
    return int(time.monotonic() * 1000)
