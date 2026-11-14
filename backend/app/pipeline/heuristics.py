"""Free lexical features (hints, not verdicts) - CLAUDE.md §4 step 2.

`extract_hints` returns hints with sentence ids; in M2 they are passed into the LLM prompt.
`spans_from_hints` turns a few high-precision patterns into candidate spans. These are kept
deliberately conservative: always amber, experimental and low-confidence (see taxonomy.gate).
"""
from __future__ import annotations

import re

from app.pipeline import lexicons
from app.pipeline.segment import Segmented, Sentence, detect_language
from app.pipeline.validate import quote_regions

# Sections of the lexicon that become one hint type each.
_LEXICON_HINTS = {
    "emotive": "emotive",
    "intensifier": "intensifier",
    "absolutist": "absolutist",
    "attribution": "attribution",
    "bandwagon": "bandwagon_phrase",
    "vague_authority": "vague_authority",
    "balance": "balance_marker",
    "opinion": "opinion_marker",
}

URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>\"]+", re.IGNORECASE)

_MAGNITUDE = (
    r"%|percent|per cent|protsent\w*|процент\w*|"
    r"thousand|million|billion|trillion|tuhat|tuhande\w*|miljon\w*|miljard\w*|"
    r"тысяч\w*|млн|млрд|миллион\w*|миллиард\w*"
)
STAT_RE = re.compile(
    rf"(?<![\w.,])\d+(?:[.,  ]\d{{3}})*(?:[.,]\d+)?\s?(?:{_MAGNITUDE})(?!\w)"
    r"|(?<![\w.,])\d{1,3}(?:[,  ]\d{3})+(?![\w.,]?\d)"   # 40,000 / 40 000
    r"|(?<![\w.,])\d{5,}(?![\w.,]?\d)",                      # 40000 (5+ digits, not a year)
    re.IGNORECASE,
)

ACRONYMS = {
    "NATO", "EU", "USA", "UK", "UN", "US", "COVID", "ERR", "BBC", "AP", "CEO", "GDP", "OK", "TV",
    "ID", "AI", "IT", "PM", "MP", "NGO", "FBI", "CIA", "WHO", "IMF", "ECB", "OECD", "BNS", "LNG",
    "ЕС", "США", "НАТО", "ООН", "РФ", "СССР",
}
_CAPS_WORD = re.compile(r"(?<!\w)[^\W\d_a-zа-яё]{2,}(?!\w)")


def _caps_runs(text: str) -> list[tuple[int, int]]:
    """Runs of shouting capitals, ignoring acronyms."""
    words = [m for m in _CAPS_WORD.finditer(text) if m.group(0).upper() == m.group(0)]
    runs: list[list[re.Match]] = []
    for m in words:
        if runs and text[runs[-1][-1].end() : m.start()].strip(" ,") == "":
            runs[-1].append(m)
        else:
            runs.append([m])
    out = []
    for run in runs:
        tokens = [m.group(0) for m in run]
        loud = [t for t in tokens if len(t) >= 4 and t not in ACRONYMS]
        if len(run) >= 2 and loud:
            out.append((run[0].start(), run[-1].end()))
        elif len(run) == 1 and len(tokens[0]) >= 6 and tokens[0] not in ACRONYMS and "!" in text:
            out.append((run[0].start(), run[0].end()))
    return out


def _hint(sent: Sentence, kind: str, a: int, b: int, detail: str = "") -> dict:
    return {
        "type": kind,
        "sentence_id": sent.id,
        "start": sent.start + a,
        "end": sent.start + b,
        "quote": sent.text[a:b],
        "detail": detail,
    }


def _is_year(s: str) -> bool:
    return re.fullmatch(r"(1[89]|20)\d\d", s.strip()) is not None


def extract_hints(seg: Segmented) -> list[dict]:
    doc_lang = seg.lang if seg.lang in lexicons.LANGS else None
    hints: list[dict] = []
    for para in seg.paragraphs:
        # mixed-language texts: use the paragraph's own language when it is clear enough
        para_lang = detect_language(para.text)
        lang = para_lang if para_lang in lexicons.LANGS else doc_lang
        regions = quote_regions(seg.text, para.start, para.end)
        for sent in para.sentences:
            t = sent.text
            folded = lexicons.fold(t)
            if lang:
                for section, kind in _LEXICON_HINTS.items():
                    for m in lexicons.find(lang, section, t, folded):
                        hints.append(_hint(sent, kind, m.start(), m.end()))
                ingroup = lexicons.find(lang, "ingroup", t, folded)
                outgroup = lexicons.find(lang, "outgroup", t, folded)
                if ingroup and outgroup:
                    m = outgroup[0]
                    hints.append(_hint(sent, "us_them", m.start(), m.end(), f"{len(ingroup)} we / {len(outgroup)} they"))
            for a, b in _caps_runs(t):
                hints.append(_hint(sent, "all_caps", a, b))
            bangs = t.count("!")
            if bangs:
                i = t.index("!")
                hints.append(_hint(sent, "exclamation", i, i + 1, f"{bangs} exclamation mark(s)"))
            links = list(URL_RE.finditer(t))
            for m in links:
                hints.append(_hint(sent, "link", m.start(), m.end()))
            attributed = bool(links) or bool(lang and lexicons.find(lang, "attribution", t, folded))
            for m in STAT_RE.finditer(t):
                if _is_year(m.group(0)):
                    continue
                kind = "number_attributed" if attributed else "number_unattributed"
                hints.append(_hint(sent, kind, m.start(), m.end()))
            for ra, rb in regions:
                if sent.start <= ra < sent.end:
                    hints.append(_hint(sent, "quotation", ra - sent.start, min(rb, sent.end) - sent.start))
    return hints


# ------------------------------------------------------------- candidate spans

_EXPLAIN = {
    "bandwagon": "Points to what “everyone” supposedly knows or agrees on, instead of giving evidence.",
    "questionable_authority": "Cites unnamed experts, studies or sources without saying who they are.",
    "unsupported_claim": (
        "States a figure without naming where it comes from in this sentence. "
        "That does not make it false; the text just gives no source for it here."
    ),
    "loaded_language": "Uses capital letters to shout, which adds emotional pressure rather than information.",
}
_RULE_NOTE = " (Rule-based hint, not an AI judgement.)"

_SPAN_RULES = {
    # hint type -> (label, confidence)
    "bandwagon_phrase": ("bandwagon", 0.45),
    "vague_authority": ("questionable_authority", 0.35),
    "number_unattributed": ("unsupported_claim", 0.3),
    "all_caps": ("loaded_language", 0.3),
}


def spans_from_hints(hints: list[dict]) -> list[dict]:
    """Candidate spans from the few hint types precise enough to show (as experimental)."""
    has_link = {h["sentence_id"] for h in hints if h["type"] == "link"}
    claimed: set[str] = set()  # at most one unsupported_claim per sentence
    out = []
    for h in hints:
        rule = _SPAN_RULES.get(h["type"])
        if rule is None:
            continue
        label, conf = rule
        sid = h["sentence_id"]
        if label == "questionable_authority" and sid in has_link:
            continue
        if label == "unsupported_claim":
            if sid in claimed:
                continue
            claimed.add(sid)
        out.append(
            {
                "sentence_id": sid,
                "quote": h["quote"],
                "label": label,
                "confidence": conf,
                "explanation": _EXPLAIN[label] + _RULE_NOTE,
                "source": "heuristic",
            }
        )
    return out
