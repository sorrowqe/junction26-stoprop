"""Parse + validate a batch of posts (CLAUDE.md §5). In memory only; nothing is stored.

Required: id, author, timestamp (ISO 8601 or unix seconds/milliseconds), text.
Optional: url, author_created_at, platform.
Limits: MAX_POSTS valid posts per request, MAX_BYTES of input. Every rejected row is reported with
its row number (1 = first data row) and a plain-English reason; valid rows are processed anyway.
"""
from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

MAX_POSTS = 2000
MAX_BYTES = 2 * 1024 * 1024
MAX_TEXT_CHARS = 5000
MAX_REJECTED_LISTED = 100
REQUIRED = ("id", "author", "timestamp", "text")
OPTIONAL = ("url", "author_created_at", "platform")
_ALIASES = {"created_at": "timestamp", "time": "timestamp", "date": "timestamp", "user": "author",
            "username": "author", "screen_name": "author", "content": "text", "body": "text", "post_id": "id",
            "account_created_at": "author_created_at"}


class IngestError(ValueError):
    """The whole upload is unusable (wrong format, too big, too many posts)."""


@dataclass
class Post:
    id: str
    author: str
    ts: float                      # unix seconds, UTC
    text: str
    url: str | None = None
    author_created_at: float | None = None
    platform: str | None = None


@dataclass
class Batch:
    posts: list[Post] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    rejected_total: int = 0
    rows_total: int = 0
    truncated_texts: int = 0

    def reject(self, row: int, reason: str) -> None:
        self.rejected_total += 1
        if len(self.rejected) < MAX_REJECTED_LISTED:
            self.rejected.append({"row": row, "reason": reason})


def parse_time(value) -> float | None:
    """ISO 8601 (with or without zone; naive = UTC) or unix seconds / milliseconds."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        x = float(value)
    else:
        s = str(value).strip()
        if not s:
            return None
        if re.fullmatch(r"-?\d+(\.\d+)?", s):
            x = float(s)
        else:
            try:
                dt = datetime.fromisoformat(re.sub(r"[Zz]$", "+00:00", s))
            except ValueError:
                return None
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
    if x > 1e12:          # milliseconds
        x /= 1000.0
    if not (0 < x < 4102444800):  # 1970 .. 2100
        return None
    return x


def _canon(row: dict) -> dict:
    out = {}
    for k, v in row.items():
        if k is None:
            continue
        key = str(k).strip().lower().lstrip("﻿")
        key = _ALIASES.get(key, key)
        if key in REQUIRED + OPTIONAL and key not in out:
            out[key] = v
    return out


def _add(batch: Batch, row_no: int, raw: dict, seen_ids: set[str]) -> None:
    if not isinstance(raw, dict):
        batch.reject(row_no, "not an object with fields")
        return
    row = _canon(raw)
    missing = [k for k in REQUIRED if row.get(k) in (None, "") or not str(row.get(k)).strip()]
    if missing:
        batch.reject(row_no, f"missing {', '.join(missing)}")
        return
    pid, author = str(row["id"]).strip(), str(row["author"]).strip().lstrip("@")
    if pid in seen_ids:
        batch.reject(row_no, f"duplicate id {pid[:40]}")
        return
    ts = parse_time(row["timestamp"])
    if ts is None:
        batch.reject(row_no, "timestamp is not ISO 8601 or unix time")
        return
    text = str(row["text"])
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS]
        batch.truncated_texts += 1
    created = parse_time(row.get("author_created_at")) if row.get("author_created_at") not in (None, "") else None
    if len(batch.posts) >= MAX_POSTS:
        raise IngestError(f"Too many posts: the limit is {MAX_POSTS} per request.")
    seen_ids.add(pid)
    batch.posts.append(Post(id=pid, author=author[:100], ts=ts, text=text,
                            url=(str(row["url"]).strip() or None) if row.get("url") not in (None, "") else None,
                            author_created_at=created,
                            platform=(str(row["platform"]).strip() or None) if row.get("platform") else None))


def from_rows(rows: list) -> Batch:
    batch, seen = Batch(), set()
    for i, raw in enumerate(rows, start=1):
        batch.rows_total += 1
        _add(batch, i, raw, seen)
    return batch


def from_csv(text: str) -> Batch:
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise IngestError("The upload is larger than 2 MB.")
    text = text.lstrip("﻿")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    fields = {(_ALIASES.get(f.strip().lower(), f.strip().lower())) for f in (reader.fieldnames or [])}
    missing = [k for k in REQUIRED if k not in fields]
    if missing:
        raise IngestError(f"The CSV header must contain {', '.join(REQUIRED)}; missing: {', '.join(missing)}.")
    return from_rows(list(reader))


def from_json(data) -> Batch:
    if isinstance(data, dict):
        data = data.get("posts", data.get("data"))
    if not isinstance(data, list):
        raise IngestError('JSON must be a list of posts, or {"posts": [...]}.')
    return from_rows(data)


def parse_text(text: str) -> Batch:
    """Pasted or uploaded text: JSON if it looks like JSON, otherwise CSV."""
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise IngestError("The upload is larger than 2 MB.")
    s = text.lstrip("﻿").lstrip()
    if s and s[0] in "[{":
        try:
            return from_json(json.loads(s))
        except json.JSONDecodeError as exc:
            raise IngestError(f"Not valid JSON: {exc.msg} (line {exc.lineno}).") from exc
    if not s:
        raise IngestError("The upload is empty.")
    return from_csv(s)
