"""Load the small per-language word lists in backend/config/lexicons/*.txt and match them.

Matching runs on `fold(text)`, which only swaps characters 1:1 (ё->е, curly apostrophe ->
straight), so match offsets are valid offsets in the original text.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

LEXICON_DIR = Path(__file__).resolve().parents[2] / "config" / "lexicons"
LANGS = ("en", "et", "ru")

_FOLD = str.maketrans({"ё": "е", "Ё": "Е", "’": "'", "‘": "'", "ʼ": "'"})


def fold(text: str) -> str:
    """Length-preserving normalization for lexicon matching."""
    return text.translate(_FOLD)


def _entry_to_regex(entry: str) -> str:
    words = entry.split()
    parts = []
    for w in words:
        if w.endswith("*"):
            parts.append(re.escape(w[:-1]) + r"\w*")
        else:
            parts.append(re.escape(w))
    return r"\s+".join(parts)


def _parse(path: Path) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip()
            sections.setdefault(current, [])
        elif current is not None:
            sections[current].append(fold(line))
    return sections


@lru_cache(maxsize=None)
def entries(lang: str) -> dict[str, list[str]]:
    path = LEXICON_DIR / f"{lang}.txt"
    return _parse(path) if path.exists() else {}


@lru_cache(maxsize=None)
def pattern(lang: str, section: str) -> re.Pattern | None:
    """Compiled whole-word, case-insensitive pattern for one section ('*' = 'all' languages)."""
    langs = LANGS if lang == "*" else (lang,)
    items = sorted({e for lg in langs for e in entries(lg).get(section, [])}, key=len, reverse=True)
    if not items:
        return None
    alternation = "|".join(_entry_to_regex(e) for e in items)
    return re.compile(rf"(?<!\w)(?:{alternation})(?!\w)", re.IGNORECASE)


def find(lang: str, section: str, text: str, folded: str | None = None) -> list[re.Match]:
    """All matches of a lexicon section in text (offsets valid in the original text)."""
    pat = pattern(lang, section)
    if pat is None:
        return []
    return list(pat.finditer(folded if folded is not None else fold(text)))
