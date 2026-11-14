"""Run the pipeline on a text: segment -> heuristics -> (LLM in M2) -> validate -> gate -> profile."""
from __future__ import annotations

import hashlib

from app import taxonomy
from app.pipeline import heuristics, profile, segment, validate

MAX_WORDS = 8000
LANG_OVERRIDES = {"en", "et", "ru", "other"}


def truncate_words(text: str, max_words: int = MAX_WORDS) -> tuple[str, bool]:
    """Keep the first `max_words` words. A prefix cut, so offsets still match the original."""
    for i, m in enumerate(profile.WORD_RE.finditer(text), start=1):
        if i == max_words:
            rest = text[m.end():]
            if profile.WORD_RE.search(rest):
                return text[: m.end()], True
            break
    return text, False


def analyze_text(text: str, sensitivity: str = "balanced", lang: str = "auto") -> dict:
    notes: list[str] = []
    text, truncated = truncate_words(text)
    if truncated:
        notes.append(f"Text truncated to the first {MAX_WORDS:,} words.")

    seg = segment.segment(text, lang=lang if lang in LANG_OVERRIDES else None)
    if seg.lang == "other":
        notes.append("Language not recognised as English, Estonian or Russian: only language-independent hints were used.")

    hints = heuristics.extract_hints(seg)
    candidates = heuristics.spans_from_hints(hints)
    spans, rejections = validate.validate_candidates(seg, candidates)
    spans = taxonomy.gate(spans, sensitivity)
    spans.sort(key=lambda s: (s["start"], -s["end"]))
    prof = profile.build_profile(seg, spans, hints, taxonomy.names())

    digest = hashlib.sha256(f"{text}\x00{sensitivity}\x00{seg.lang}".encode("utf-8")).hexdigest()
    return {
        "id": "r_" + digest[:12],
        "lang": seg.lang,
        "genre": prof["genre"],
        "spans": [
            {k: s[k] for k in ("start", "end", "quote", "label", "color", "confidence", "explanation",
                               "reported_speech", "experimental", "source", "sentence_id")}
            for s in spans
        ],
        "profile": prof,
        "mode": "heuristics_only",
        "notes": notes,
        "cost": {"input_tokens": 0, "output_tokens": 0, "cached": False},
        "stats": {
            "paragraphs": len(seg.paragraphs),
            "sentences": len(seg.sentences),
            "hints": len(hints),
            "validator_rejections": rejections,
            "analyzed_chars": len(text),
            "taxonomy_version": taxonomy.version(),
        },
    }
