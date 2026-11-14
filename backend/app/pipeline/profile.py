"""Genre, density, coverage, neutral summary (template, no LLM call). CLAUDE.md §4 step 6.

Every gated span is shown (validated or "not yet validated"); reported speech is not counted against the
source. "Flagged" means red (technique) or amber (unsupported claim). Green/blue spans are counted, not flagged.
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

    shown = [s for s in spans if not s.get("reported_speech")]
    flagged = [s for s in shown if s.get("color") in ("red", "amber")]
    experimental = [s for s in shown if s.get("experimental")]
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
        "techniques": {"found": sum(s.get("color") == "red" for s in shown),
                       "not_yet_validated": sum(s.get("color") == "red" and s.get("experimental") for s in shown)},
        "summary": summary(seg.lang, genre, words, flagged, len(experimental), len(reported), flagged_share, density, names),
    }


def summary(lang, genre, words, flagged, n_experimental, n_reported, share, density, names) -> str:
    """One or two neutral sentences. Describes the text, never the author."""
    lang_name = "English" if lang == "en" else "other language"
    parts = [f"{GENRE_PHRASES[genre]} ({lang_name}, {words:,} words)."]
    techniques = [s for s in flagged if s.get("color") == "red"]
    claims = [s for s in flagged if s.get("color") == "amber"]
    if techniques:
        by_label = Counter(s["label"] for s in techniques).most_common()
        listing = ", ".join(f"{n} × {names.get(lbl, lbl.replace('_', ' ')).lower()}" for lbl, n in by_label)
        parts.append(f"{_plural(len(techniques), 'technique')} found ({listing}).")
    if claims:
        parts.append(f"{_plural(len(claims), 'claim')} without a source in the text.")
    if flagged:
        parts.append(f"About {density:g} flags per 1,000 words, covering {share:.0%} of the words.")
    else:
        parts.append("Nothing is flagged.")
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
MAX_INNOCENT = 3
_INNOCENT_BY_GENRE = {
    "opinion": "This reads as an opinion piece; arguing for a position with persuasive language is expected there.",
    "social": "Social media posts are often informal and emotional without any intent to mislead.",
}


def innocent_explanations(spans: list[dict], genre: str) -> list[str]:
    """Plausible non-malicious explanations (principle 9): only for labels actually found (not reported speech),
    most confident label first, at most 3. Empty if nothing is flagged."""
    flagged = [s for s in spans if s.get("color") in ("red", "amber") and not s.get("reported_speech")]
    out: list[str] = []
    for s in sorted(flagged, key=lambda s: -s.get("confidence", 0)):
        text = _INNOCENT_BY_LABEL.get(s.get("label"))
        if text and text not in out:
            out.append(text)
    return out[:MAX_INNOCENT]
