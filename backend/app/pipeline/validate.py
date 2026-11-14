"""Verbatim span validation, offsets, reported-speech check.

Principle (CLAUDE.md §1.2): the LLM proposes, deterministic code verifies. A proposed span
is kept only if its quote is found inside the given sentence, after normalizing whitespace,
quote marks, dashes, zero-width characters and Unicode compatibility forms. Offsets are
computed here from the original text; offsets from a model are never trusted.
"""
from __future__ import annotations

import re
import unicodedata

from app.pipeline import lexicons
from app.pipeline.segment import Paragraph, Segmented

_DQ = set('"“”„‟«»″〝〞＂')
_SQ = set("'‘’‚‛‹›`´′")
_DASH = set("‐‑‒–—―−")
_ZERO_WIDTH = {"​", "‌", "‍", "⁠", "﻿", "­"}
MIN_QUOTE_CHARS = 2


def _clusters(s: str):
    """Yield (start, end) of base char + following combining marks."""
    i, n = 0, len(s)
    while i < n:
        j = i + 1
        while j < n and unicodedata.combining(s[j]):
            j += 1
        yield i, j
        i = j


def normalize_with_map(s: str, fold_case: bool = False) -> tuple[str, list[tuple[int, int]]]:
    """Normalized string + for each output char the (start, end) source range in `s`."""
    out: list[str] = []
    src: list[tuple[int, int]] = []
    prev_space = False
    for a, b in _clusters(s):
        cluster = s[a:b]
        if cluster in _ZERO_WIDTH:
            continue
        norm = unicodedata.normalize("NFKC", cluster)
        for ch in norm:
            if ch in _ZERO_WIDTH:
                continue
            if ch.isspace():
                if prev_space:
                    continue
                out.append(" ")
                src.append((a, b))
                prev_space = True
                continue
            prev_space = False
            if ch in _DQ:
                ch = '"'
            elif ch in _SQ:
                ch = "'"
            elif ch in _DASH:
                ch = "-"
            elif ch == "ё":
                ch = "е"
            elif ch == "Ё":
                ch = "Е"
            if fold_case:
                low = ch.lower()
                if len(low) == 1:
                    ch = low
            out.append(ch)
            src.append((a, b))
    return "".join(out), src


def _quote_variants(quote: str) -> list[str]:
    q = normalize_with_map(quote)[0].strip()
    variants = [q]
    # models sometimes wrap the quote in quote marks or add trailing sentence punctuation
    if len(q) > 2 and q[0] in "\"'" and q[-1] in "\"'":
        variants.append(q[1:-1].strip())
    stripped = q.rstrip(".,;:!?")
    if stripped != q:
        variants.append(stripped)
    return [v for v in variants if len(v) >= MIN_QUOTE_CHARS]


def locate_in(text: str, start: int, end: int, quote: str, occurrence: int = 1) -> tuple[int, int] | None:
    """Find `quote` inside text[start:end]; return global (start, end) or None.

    Case-sensitive first, then a case-insensitive fallback. `occurrence` picks the n-th
    match (1 = first) when the phrase repeats.
    """
    if not quote or not quote.strip() or occurrence < 1:
        return None
    chunk = text[start:end]
    for fold_case in (False, True):
        norm, src = normalize_with_map(chunk, fold_case)
        for variant in _quote_variants(quote):
            needle = normalize_with_map(variant, fold_case)[0]
            pos, seen = -1, 0
            while True:
                pos = norm.find(needle, pos + 1)
                if pos < 0:
                    break
                seen += 1
                if seen == occurrence:
                    s_local = src[pos][0]
                    e_local = src[pos + len(needle) - 1][1]
                    return start + s_local, start + e_local
    return None


def locate(seg: Segmented, sentence_id: str, quote: str, occurrence: int = 1) -> tuple[int, int] | None:
    """Find `quote` verbatim (normalized) inside the given sentence only."""
    sent = seg.sentence(sentence_id)
    if sent is None:
        return None
    return locate_in(seg.text, sent.start, sent.end, quote, occurrence)


# ------------------------------------------------------------ reported speech

_CLOSERS = {"“": "”“", "„": "“”", "«": "»", '"': '"', "‹": "›"}


def quote_regions(text: str, start: int, end: int) -> list[tuple[int, int]]:
    """Quoted regions in text[start:end] as global (start, end) including the marks.

    Pairs “…”, „…“, „…”, «…» (nesting-aware), "…". An unclosed opening mark runs to the
    end of the range (multi-paragraph quotes often leave the paragraph open).
    """
    regions = []
    i = start
    while i < end:
        ch = text[i]
        closers = _CLOSERS.get(ch)
        if closers is None:
            i += 1
            continue
        j = i + 1
        depth = 0
        while j < end:
            c = text[j]
            if ch == "«" and c == "«":
                depth += 1
            elif c in closers:
                if depth == 0:
                    break
                depth -= 1
            j += 1
        stop = min(j + 1, end)
        if stop - i > 2:
            regions.append((i, stop))
        i = stop
    return regions


def _attributed(text: str, para: Paragraph, rs: int, re_: int) -> bool:
    before = text[max(para.start, rs - 100) : rs]
    after = text[re_ : min(para.end, re_ + 100)]
    if lexicons.find("*", "scare", before[-20:]):
        return False  # so-called "experts": the author's own scare quotes
    if before.rstrip().endswith(":"):
        return True
    return bool(lexicons.find("*", "speech", before) or lexicons.find("*", "speech", after))


def attributed_regions(seg: Segmented, para: Paragraph) -> list[tuple[int, int]]:
    return [(a, b) for a, b in quote_regions(seg.text, para.start, para.end) if _attributed(seg.text, para, a, b)]


def is_reported_speech(seg: Segmented, start: int, end: int) -> bool:
    """True if [start, end) lies inside quotation marks attributed to a third party."""
    for para in seg.paragraphs:
        if para.start <= start and end <= para.end:
            return any(a <= start and end <= b for a, b in attributed_regions(seg, para))
    return False


# ------------------------------------------------------------------ candidates

def validate_candidates(seg: Segmented, candidates: list[dict]) -> tuple[list[dict], int]:
    """Turn proposed spans into validated spans; return (spans, validator_rejections).

    Candidate: {sentence_id, quote, label, confidence, explanation, source}.
    """
    spans: list[dict] = []
    rejections = 0
    seen: set[tuple[int, int, str]] = set()
    for c in candidates:
        loc = locate(seg, str(c.get("sentence_id", "")), str(c.get("quote", "")))
        if loc is None:
            rejections += 1
            continue
        start, end = loc
        key = (start, end, c.get("label", ""))
        if key in seen:
            continue
        seen.add(key)
        spans.append(
            {
                "start": start,
                "end": end,
                "quote": seg.text[start:end],
                "label": c.get("label"),
                "confidence": round(float(c.get("confidence", 0.0)), 2),
                "explanation": c.get("explanation", ""),
                "reported_speech": is_reported_speech(seg, start, end),
                "sentence_id": c.get("sentence_id"),
                "source": c.get("source", "llm"),
            }
        )
    return spans, rejections
