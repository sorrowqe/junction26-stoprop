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
    # models sometimes wrap the quote in quote marks, ellipses, or trailing sentence punctuation
    trimmed = re.sub(r"^(?:\.{2,}|…|\[\.{2,}\])\s*|\s*(?:\.{2,}|…|\[\.{2,}\])$", "", q).strip()
    if trimmed and trimmed != q:
        variants.append(trimmed)
    for base in list(variants):
        if len(base) > 2 and base[0] in "\"'" and base[-1] in "\"'":
            variants.append(base[1:-1].strip())
        stripped = base.rstrip(".,;:!?")
        if stripped != base:
            variants.append(stripped)
    return [v for v in dict.fromkeys(variants) if len(v) >= MIN_QUOTE_CHARS]


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


# ---- who is speaking? (CLAUDE.md §1 rule 3: only a specific, identifiable source makes it reported speech)

# Unnamed / collective sources: their vagueness is the technique ("experts say", "many believe", "insiders claim").
_VAGUE_WORDS = {
    "many", "some", "several", "most", "few", "numerous", "various", "certain", "countless", "unnamed", "anonymous",
    "others", "people", "everyone", "everybody", "someone", "somebody", "they", "experts", "insiders", "sources",
    "mõned", "paljud", "mitmed", "mitu", "eksperdid", "inimesed", "kõik", "nad",
    "многие", "некоторые", "несколько", "эксперты", "люди", "все", "они", "источники",
}
_FUNCTION = {
    "the", "a", "an", "this", "that", "these", "those", "his", "her", "its", "their", "our", "my", "your", "in", "on",
    "at", "during", "after", "before", "also", "and", "but", "so", "then", "meanwhile", "later", "earlier", "today",
    "yesterday", "of", "for", "from", "to", "by", "with", "as", "while", "when", "however", "now", "again", "has",
    "have", "had", "was", "were", "is", "are", "be", "been", "not", "never", "repeatedly", "once",
}
_DEFINITE = {"the", "this", "that"}
_SINGULAR_PRONOUNS = {"he", "she", "ta", "tema", "он", "она"}
_PREPOSITIONAL = ("according to", "по словам")      # the source follows the marker
_CONTRAST = re.compile(r"(?:^|[\s,])(?:but|yet|whereas|although|though|however|kuid|aga|ent|но|однако)\b|;", re.I)
_WORD = re.compile(r"[^\W\d_][\w’'-]*")


def _plural_looking(word: str) -> bool:
    w = word.lower()
    return len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is", "'s", "’s"))


def _words(text: str, offset: int = 0) -> list[tuple[str, int]]:
    return [(m.group(), m.start() + offset) for m in _WORD.finditer(text)]


def _is_vague(words: list[tuple[str, int]]) -> bool:
    return any(w.lower() in _VAGUE_WORDS for w, _ in words)


def _is_specific(words: list[tuple[str, int]], lang: str, sentence_start: int = 0) -> bool:
    """A named person/organisation/outlet, a definite singular role ("the minister", "the association"),
    or he/she. Unnamed groups ("Supporters argued", "senior healers say") are not specific.
    Outside English (not evaluated) only the vagueness test applies."""
    content = [(w, i) for w, i in words if w.lower() not in _FUNCTION]
    if not content or _is_vague(words):
        return False
    if lang != "en":
        return True
    lower = [w.lower() for w, _ in words]
    for k, (w, i) in enumerate(words):
        if w[0].isupper() and w.lower() not in _FUNCTION:
            if i > sentence_start:
                return True                      # capitalised mid-sentence: a name
            nxt = words[k + 1][0] if k + 1 < len(words) else ""
            if nxt[:1].isupper() or not _plural_looking(w):
                return True                      # "Lira Voss said", "Voss said" (not "Supporters said")
    if any(w in _SINGULAR_PRONOUNS for w in lower):
        return True
    return any(w in _DEFINITE for w in lower) and not _plural_looking(content[-1][0])


def _source_words(text: str, m_start: int, m_end: int, verb_after_span: bool) -> list[tuple[str, int]]:
    """Words naming who speaks, for a reporting verb at text[m_start:m_end] (text = one sentence)."""
    before = text[:m_start]
    cut = max(before.rfind(c) for c in ",;:—–(")
    clause = _words(before[cut + 1:], cut + 1)
    if any(w.lower() not in _FUNCTION for w, _ in clause):
        return clause[-8:]                       # "Minister Voss said", "<span>, the mayor said"
    after = text[m_end:]
    stop = re.search(r"[,;:.!?—–]", after)
    following = _words(after[: stop.start() if stop else len(after)], m_end)[:8]
    if verb_after_span or text[m_start:m_end].lower().startswith(_PREPOSITIONAL):
        return following                         # ", said the mayor", "according to the minister,"
    return _words(text)[:6]                      # "She ... , asking, <span>": the sentence's subject


def _attributed(text: str, para: Paragraph, rs: int, re_: int, lang: str = "en") -> bool:
    """Direct quote: quotation marks already show the words are someone else's, so any non-vague
    attribution counts ("“…,” one resident said"); vague ones ("Experts warn: “…”") do not."""
    before = text[max(para.start, rs - 100) : rs]
    after = text[re_ : min(para.end, re_ + 100)]
    if lexicons.find("*", "scare", before[-20:]):
        return False  # so-called "experts": the author's own scare quotes
    if lexicons.find("*", "vague_authority", before[-40:]) or lexicons.find("*", "bandwagon", before[-40:]):
        return False
    if before.rstrip().endswith(":"):
        return not _is_vague(_words(before.rstrip()[:-1])[-6:])
    for m in lexicons.find("*", "speech", before):
        if not _is_vague(_words(before[: m.start()])[-6:]):
            return True
    for m in lexicons.find("*", "speech", after):
        subject = _words(after[: m.start()])[-6:] + _words(re.split(r"[,;:.!?]", after[m.end():], maxsplit=1)[0])[:4]
        if not _is_vague(subject):
            return True
    return False


def attributed_regions(seg: Segmented, para: Paragraph) -> list[tuple[int, int]]:
    return [(a, b) for a, b in quote_regions(seg.text, para.start, para.end)
            if _attributed(seg.text, para, a, b, seg.lang)]


_FIRST_PERSON = {"i", "we", "ma", "mina", "me", "meie", "я", "мы"}


def _indirect_attribution(seg: Segmented, start: int, end: int) -> bool:
    """Attributed indirect speech in the same sentence (CLAUDE.md §1 rule 3):
    "X said (that) ...", "X called it ...", "according to X, ...", "..., the mayor said.",
    "ministri sõnul ...", "по словам X ...", "X заявил, что ...".

    Only a SPECIFIC, identifiable source counts (named person/organisation/outlet, "the minister", he/she).
    Not reported speech: first-person attribution ("I said that"), vague or unnamed sources that are
    themselves a technique ("experts say", "insiders claim", "Supporters argued"), "so-called", spans
    that contain the reporting verb itself, and the author's own clause after a contrast
    ("X says the grid needs caution, but can we really trust them?").
    """
    sent = next((s for s in seg.sentences if s.start <= start and end <= s.end), None)
    if sent is None:
        return False
    text = sent.text
    folded = lexicons.fold(text)
    vague = [(m.start(), m.end()) for sec in ("vague_authority", "bandwagon") for m in lexicons.find("*", sec, text, folded)]
    s_loc, e_loc = start - sent.start, end - sent.start
    for m in lexicons.find("*", "speech", text, folded):
        if m.start() < e_loc and s_loc < m.end():
            continue  # the span contains the reporting verb ("Experts say ...")
        if text[max(0, m.start() - 3):m.start()].lower().endswith("so-"):
            continue  # "so-called"
        if any(a <= m.start() and m.end() <= b for a, b in vague):
            continue  # "experts say", "many people are saying": the attribution itself is the technique
        before_words = re.findall(r"\w+", text[: m.start()])
        if before_words and before_words[-1].lower() in _FIRST_PERSON:
            continue  # "I said that ...": the author's own claim
        if m.end() <= s_loc:
            if _CONTRAST.search(text[m.end():s_loc]):
                continue  # "X said …, but <span>": the author's own clause
            if _is_specific(_source_words(text, m.start(), m.end(), False), seg.lang, _words(text)[0][1] if _words(text) else 0):
                return True  # "X said that <span>", "according to X, <span>"
        elif m.start() >= e_loc and re.search(r"[,—–-]", text[e_loc:m.start()]):
            if _is_specific(_source_words(text, m.start(), m.end(), True), seg.lang, _words(text)[0][1] if _words(text) else 0):
                return True  # "<span>, the mayor said."
    return False


def is_reported_speech(seg: Segmented, start: int, end: int) -> bool:
    """True if [start, end) is reported speech: inside quotation marks attributed to a third party,
    or inside attributed indirect speech in the same sentence (CLAUDE.md §1 rule 3)."""
    for para in seg.paragraphs:
        if para.start <= start and end <= para.end:
            if any(a <= start and end <= b for a, b in attributed_regions(seg, para)):
                return True
            return _indirect_attribution(seg, start, end)
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
