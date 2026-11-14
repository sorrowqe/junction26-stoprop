"""Gold-sheet CSV format shared by the synthetic generator, the SemEval converter and (M4) sheet_to_jsonl.py.

One row per gold span. A text without spans has one row with label "none" and empty quote/start/end.
Offsets are Python str indices (code points) into `text`, end exclusive (same as the API).

Columns:
  id, source (synthetic | semeval | human), lang, genre, kind, topic, direction, text, quote, label,
  start, end, reported_speech (y | n | empty = unknown), ok (human review: y | n | empty), notes,
  ai_ok (two-model consensus: y | n | empty = reviewers disagreed / not reviewed), ai_reasons
Decision per row (sheet_to_jsonl.py): `ok` if filled, else `ai_ok`; semeval/human rows are kept as labelled.

Marker syntax used by the generator:  [[label|exact phrase]]  or  [[label|exact phrase|rs]]
(rs = the phrase is quoted from a third party = reported speech, must not count against the text).
"""
from __future__ import annotations

import csv
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import taxonomy  # noqa: E402
from app.pipeline.segment import detect_language, segment  # noqa: E402
from app.pipeline.validate import quote_regions  # noqa: E402

GOLD_COLUMNS = ["id", "source", "lang", "genre", "kind", "topic", "direction", "text", "quote", "label",
                "start", "end", "reported_speech", "ok", "notes", "ai_ok", "ai_reasons"]
SYMMETRY_COLUMNS = ["pair_id", "variant", "source", "lang", "topic", "swap", "text", "quote", "label",
                    "start", "end", "ok", "notes", "ai_ok", "ai_reasons"]
CSV_ENCODING = "utf-8-sig"  # BOM so Excel / Google Sheets open Cyrillic and Estonian correctly

MARKER = re.compile(r"\[\[([a-z_]+)\|([^\[\]|]+?)(?:\|(rs))?\]\]")


def labels() -> set[str]:
    return set(taxonomy.load()["by_label"])


def technique_labels() -> set[str]:
    return {t["label"] for t in taxonomy.load()["techniques"] if t["kind"] == "technique"}


@dataclass
class Span:
    label: str
    quote: str
    start: int
    end: int
    rs: bool


class MarkerError(ValueError):
    pass


def strip_markers(marked: str) -> tuple[str, list[Span]]:
    """'a [[loaded_language|vile scheme]] b' -> ('a vile scheme b', [Span(...)]). Raises MarkerError."""
    out: list[str] = []
    spans: list[Span] = []
    pos = 0
    length = 0
    for m in MARKER.finditer(marked):
        chunk = marked[pos:m.start()]
        out.append(chunk)
        length += len(chunk)
        label, phrase, rs = m.group(1), m.group(2), m.group(3)
        if phrase != phrase.strip() or not phrase.strip():
            raise MarkerError(f"phrase has surrounding spaces or is empty: {phrase!r}")
        spans.append(Span(label, phrase, length, length + len(phrase), rs == "rs"))
        out.append(phrase)
        length += len(phrase)
        pos = m.end()
    out.append(marked[pos:])
    text = "".join(out)
    if "[[" in text or "]]" in text or "[[" in marked[pos:]:
        raise MarkerError("malformed or nested marker")
    for s in spans:
        if text[s.start:s.end] != s.quote:
            raise MarkerError("offset mismatch")  # cannot happen; guards the arithmetic
    return text, spans


def validate_spans(text: str, spans: list[Span], *, lang: str | None = None) -> list[str]:
    """Problems that make an item unusable as gold (empty list = OK)."""
    problems = []
    known = labels()
    seg = segment(text)
    sentences = [(s.start, s.end) for s in seg.sentences]
    for s in spans:
        if s.label not in known:
            problems.append(f"unknown label {s.label}")
        if len(s.quote.split()) > 25:
            problems.append(f"span too long ({len(s.quote.split())} words)")
        if not any(a <= s.start and s.end <= b for a, b in sentences):
            problems.append(f"span crosses a sentence boundary: {s.quote[:40]!r}")
        if s.rs:
            regions = [r for p in seg.paragraphs for r in quote_regions(text, p.start, p.end)]
            if not any(a <= s.start and s.end <= b for a, b in regions):
                problems.append(f"rs span not inside quotation marks: {s.quote[:40]!r}")
    if lang and detect_language(text) != lang:
        problems.append(f"language detected as {detect_language(text)}, expected {lang}")
    return problems


def gold_rows(item: dict, text: str, spans: list[Span]) -> list[dict]:
    base = {k: item.get(k, "") for k in ("id", "source", "lang", "genre", "kind", "topic", "direction")}
    base["text"] = text
    if not spans:
        return [{**base, "quote": "", "label": "none", "start": "", "end": "", "reported_speech": "",
                 "ok": "", "notes": item.get("notes", "")}]
    return [{**base, "quote": s.quote, "label": s.label, "start": s.start, "end": s.end,
             "reported_speech": "y" if s.rs else "n", "ok": "", "notes": item.get("notes", "")} for s in spans]


def write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding=CSV_ENCODING, newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding=CSV_ENCODING, newline="") as f:
        return [{k: (v if v is not None else "") for k, v in row.items()} for row in csv.DictReader(f)]


def check_not_open(path: Path) -> None:
    """Refuse to rewrite a CSV that LibreOffice / Excel currently has open (it would be overwritten on save)."""
    for lock in (path.parent / f".~lock.{path.name}#", path.parent / f"~${path.name}"):
        if lock.exists():
            raise SystemExit(f"{path.name} is open in a spreadsheet program ({lock.name}). Close it and run again.")
