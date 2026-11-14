"""Genre, density, coverage, neutral summary (template, no LLM call). CLAUDE.md §4 step 6.

What counts against the source: spans shown by default (not experimental) that are not
reported speech. "Flagged" means red or amber. Green/blue spans are counted but not flagged.
"""
from __future__ import annotations

import re
from collections import Counter

from app.pipeline import lexicons
from app.pipeline.segment import Segmented

WORD_RE = re.compile(r"\w+(?:['’\-]\w+)*")
_DATELINE = re.compile(r"^[A-ZÄÖÜÕŠŽА-Я][A-ZÄÖÜÕŠŽА-Я .'-]{2,}(?:,[^–—\n]{0,30})?\s*(?:\([^)]{1,40}\)\s*)?[–—-]\s")
_HASHTAG = re.compile(r"(?<!\w)[#@]\w+")
_EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿]")

LANG_NAMES = {"en": "English", "et": "Estonian", "ru": "Russian"}
GENRE_PHRASES = {
    "news": "Reads like a news report",
    "opinion": "Reads like an opinion piece",
    "social": "Reads like a social media post",
    "unknown": "Genre unclear",
}


def word_spans(text: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in WORD_RE.finditer(text)]


def guess_genre(seg: Segmented, hints: list[dict], words: int) -> str:
    """news | opinion | social | unknown - simple, explainable rules."""
    if words < 15:
        return "unknown"
    text = seg.text
    sentences = max(1, len(seg.sentences))
    kinds = Counter(h["type"] for h in hints)
    exclam_ratio = len({h["sentence_id"] for h in hints if h["type"] == "exclamation"}) / sentences
    tags = len(_HASHTAG.findall(text)) + len(_EMOJI.findall(text))
    per100 = 100 / words
    attribution = kinds["attribution"] * per100
    lang = seg.lang if seg.lang in lexicons.LANGS else None
    first_person = len(lexicons.find(lang, "first_person", text)) * per100 if lang else 0.0
    opinion_markers = kinds["opinion_marker"]
    dateline = bool(_DATELINE.match(text.lstrip()))

    if words < 250 and (tags >= 2 or exclam_ratio >= 0.25 or (exclam_ratio >= 0.15 and kinds["all_caps"])):
        return "social"
    if dateline or (attribution >= 1.0 and first_person < 1.5):
        return "news"
    if first_person >= 1.5 or opinion_markers >= 2:
        return "opinion"
    if attribution >= 0.5:
        return "news"
    return "unknown"


def _covered_words(spans: list[dict], wspans: list[tuple[int, int]]) -> int:
    intervals = sorted((s["start"], s["end"]) for s in spans)
    merged: list[list[int]] = []
    for a, b in intervals:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    count, i = 0, 0
    for ws, we in wspans:
        while i < len(merged) and merged[i][1] <= ws:
            i += 1
        if i < len(merged) and merged[i][0] < we and ws < merged[i][1]:
            count += 1
    return count


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def build_profile(seg: Segmented, spans: list[dict], hints: list[dict], names: dict[str, str] | None = None) -> dict:
    names = names or {}
    wspans = word_spans(seg.text)
    words = len(wspans)
    genre = guess_genre(seg, hints, words)

    shown = [s for s in spans if not s.get("experimental") and not s.get("reported_speech")]
    flagged = [s for s in shown if s.get("color") in ("red", "amber")]
    experimental = [s for s in spans if s.get("experimental") and not s.get("reported_speech")]
    reported = [s for s in spans if s.get("reported_speech")]

    counts = dict(Counter(s["label"] for s in shown))
    flagged_share = round(_covered_words(flagged, wspans) / words, 3) if words else 0.0
    density = round(len(flagged) / words * 1000, 1) if words else 0.0

    return {
        "words": words,
        "genre": genre,
        "flagged_share": flagged_share,
        "density_per_1000": density,
        "counts": counts,
        "experimental": {"count": len(experimental), "counts": dict(Counter(s["label"] for s in experimental))},
        "reported_speech": len(reported),
        "summary": summary(seg.lang, genre, words, flagged, len(experimental), len(reported), flagged_share, density, names),
    }


def summary(lang, genre, words, flagged, n_experimental, n_reported, share, density, names) -> str:
    """One or two neutral sentences. Describes the text, never the author."""
    lang_name = LANG_NAMES.get(lang, "a language without word lists yet")
    parts = [f"{GENRE_PHRASES[genre]} ({lang_name}, {words:,} words)."]
    if flagged:
        by_label = Counter(s["label"] for s in flagged).most_common()
        listing = ", ".join(f"{n} × {names.get(lbl, lbl.replace('_', ' ')).lower()}" for lbl, n in by_label)
        parts.append(
            f"{_plural(len(flagged), 'passage')} flagged ({listing}), about {density:g} per 1,000 words, "
            f"covering {share:.0%} of the words."
        )
    else:
        parts.append("Nothing is flagged by default.")
    if n_experimental:
        parts.append(f"{_plural(n_experimental, 'experimental hint')} hidden by default (lower measured precision).")
    if n_reported:
        parts.append(f"{_plural(n_reported, 'quoted passage')} attributed to someone else, not counted.")
    return " ".join(parts)


# ------------------------------------------------------- innocent explanations

_INNOCENT_BY_LABEL = {
    "loaded_language": "Strong wording can reflect genuine feeling or a writing style, not only an attempt to steer the reader.",
    "name_calling": "Labels can be shorthand used within a community; check whether the text also engages with the argument.",
    "fear_appeal": "Warnings about real risks can be legitimate; what matters is whether the danger is backed by evidence.",
    "exaggeration_minimisation": "Informal writing often exaggerates for emphasis without meaning it literally.",
    "whataboutism": "Comparing with others can be fair context, not only a way to deflect.",
    "false_dilemma": "Sometimes the options really are limited; check whether other choices exist.",
    "questionable_authority": "Short texts often summarise expert views without naming the experts; the original source may name them.",
    "causal_oversimplification": "Short texts simplify causes for space; a longer source may give the fuller picture.",
    "bandwagon": "Phrases like “everyone knows” are also an everyday figure of speech.",
    "doubt_casting": "Questioning credibility can be legitimate if the evidence is given elsewhere.",
    "unsupported_claim": "A source may be given elsewhere: in a link, an earlier paragraph, or the original article this text came from.",
}
_INNOCENT_BY_GENRE = {
    "opinion": "This reads as an opinion piece; arguing for a position with persuasive language is expected there.",
    "social": "Social media posts are often informal and emotional without any intent to mislead.",
}


def innocent_explanations(spans: list[dict], genre: str) -> list[str]:
    """Plausible non-malicious explanations for what was flagged (principle 9). Empty if nothing flagged."""
    flagged = [s for s in spans if s.get("color") in ("red", "amber")]
    if not flagged:
        return []
    out: list[str] = []
    if any(s.get("reported_speech") for s in flagged):
        out.append("Some of the flagged wording is quoted from someone else; reporting what others said is normal journalism.")
    if genre in _INNOCENT_BY_GENRE:
        out.append(_INNOCENT_BY_GENRE[genre])
    seen = set()
    for s in sorted(flagged, key=lambda s: -s.get("confidence", 0)):
        label = s.get("label")
        if label in _INNOCENT_BY_LABEL and label not in seen:
            seen.add(label)
            out.append(_INNOCENT_BY_LABEL[label])
    if any(s.get("source") == "heuristic" for s in flagged):
        out.append("Rule-based hints match words, not meaning, and can misfire on ordinary text.")
    return out[:6]
