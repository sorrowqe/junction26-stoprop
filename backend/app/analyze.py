"""Run the pipeline: segment -> heuristics -> LLM tagger -> validate -> gate -> profile."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging

from app import config, languages, taxonomy
from app.llm import budget
from app.pipeline import heuristics, profile, segment, tagger, validate
from app.store import get_store

log = logging.getLogger("stoprop.analyze")

MAX_WORDS = 8000
LANG_OVERRIDES = {"en", "et", "ru", "other"}
SPAN_FIELDS = ("start", "end", "quote", "label", "color", "confidence", "explanation",
               "reported_speech", "experimental", "source", "sentence_id")

NOTE_NO_KEY = "AI analysis is not configured on this server, so only rule-based hints are shown."
NOTE_BUDGET = "Today's AI budget is used up, so only rule-based hints are shown until tomorrow (UTC)."


def truncate_words(text: str, max_words: int = MAX_WORDS) -> tuple[str, bool]:
    """Keep the first `max_words` words. A prefix cut, so offsets still match the original."""
    for i, m in enumerate(profile.WORD_RE.finditer(text), start=1):
        if i == max_words:
            rest = text[m.end():]
            if profile.WORD_RE.search(rest):
                return text[: m.end()], True
            break
    return text, False


async def analyze_text(text: str, sensitivity: str | None = None, lang: str = "auto",
                       private: bool = False, domain: str | None = None, count_seen: bool = True) -> dict:
    notes: list[str] = []
    text, truncated = truncate_words(text)
    if truncated:
        notes.append(f"Text truncated to the first {MAX_WORDS:,} words.")

    seg = segment.segment(text, lang=lang if lang in LANG_OVERRIDES else None)
    if seg.lang == "other":
        notes.append("Language not recognised as English, Estonian or Russian: word-list hints were limited.")

    language_evaluated = languages.is_evaluated(seg.lang)
    if sensitivity not in taxonomy.SENSITIVITIES:
        sensitivity = languages.default_sensitivity(seg.lang)

    hints = heuristics.extract_hints(seg)
    heuristic_candidates = heuristics.spans_from_hints(hints)

    available, reason = budget.llm_available()
    mode, mode_reason = "heuristics_only", reason
    usage = {"input_tokens": 0, "output_tokens": 0, "llm_calls": 0}
    rejections_extra = 0
    if available and seg.paragraphs:
        result = await tagger.tag(seg, hints, private=private)
        usage = {"input_tokens": result.input_tokens, "output_tokens": result.output_tokens,
                 "llm_calls": result.llm_calls}
        rejections_extra = result.dropped_items
        failed = result.failed
        ok_paras = len(seg.paragraphs) - len(failed)
        if ok_paras > 0:
            mode, mode_reason = "llm", "ok"
            if failed:
                notes.append(f"AI analysis failed for {len(failed)} of {len(seg.paragraphs)} paragraphs; "
                             "rule-based hints are shown there instead.")
        else:
            mode_reason = "llm_error"
            notes.append("AI analysis is temporarily unavailable, so only rule-based hints are shown.")
        failed_sentences = {s.id for p in seg.paragraphs if p.id in failed for s in p.sentences}
        candidates = result.candidates + [c for c in heuristic_candidates if c["sentence_id"] in failed_sentences]
    else:
        candidates = heuristic_candidates
        if reason == "no_key":
            notes.append(NOTE_NO_KEY)
        elif reason == "budget":
            notes.append(NOTE_BUDGET)

    spans, rejections = validate.validate_candidates(seg, candidates)
    spans = taxonomy.gate(spans, sensitivity)
    spans.sort(key=lambda s: (s["start"], -s["end"]))
    prof = profile.build_profile(seg, spans, hints, taxonomy.names())

    seen_before = 0
    if seg.paragraphs:
        store = get_store()
        hashes = [tagger.paragraph_hash(p.text) for p in seg.paragraphs]
        seen_before = max(store.seen(h)[0] for h in hashes)
        if count_seen and not private:
            for h in hashes:
                store.note_seen(h, domain)

    digest = hashlib.sha256(
        json.dumps([text, sensitivity, seg.lang, mode], ensure_ascii=False).encode("utf-8")).hexdigest()
    return {
        "id": "r_" + digest[:12],
        "lang": seg.lang,
        "language_evaluated": language_evaluated,
        "language_note": languages.note(seg.lang),
        "sensitivity": sensitivity,
        "genre": prof["genre"],
        "spans": [{k: s[k] for k in SPAN_FIELDS} for s in spans],
        "profile": prof,
        "innocent_explanations": profile.innocent_explanations(spans, prof["genre"]),
        "receipts": [],  # filled for URLs in M3
        "mode": mode,
        "mode_reason": mode_reason,
        "notes": notes,
        "cost": {
            "input_tokens": usage["input_tokens"],
            "output_tokens": usage["output_tokens"],
            "llm_calls": usage["llm_calls"],
            "cached": mode == "llm" and usage["llm_calls"] == 0,
        },
        "stats": {
            "paragraphs": len(seg.paragraphs),
            "sentences": len(seg.sentences),
            "hints": len(hints),
            "validator_rejections": rejections + rejections_extra,
            "analyzed_chars": len(text),
            "taxonomy_version": taxonomy.version(),
            "prompt_version": tagger.prompt_version(),
            "model": config.model_tagger() if mode == "llm" else None,
            "seen_before": seen_before,
        },
    }


async def prewarm_samples() -> None:
    """Analyze the demo samples once so judges' first clicks hit the cache (costs LLM calls only once).

    Runs as a background task after startup; it must never block startup or /api/health and never
    raise: any failure is logged and the app keeps serving (otherwise the deploy health check could fail).
    Already-cached samples cost nothing (cache key = paragraph hash + model + prompt + taxonomy version).
    """
    try:
        if not budget.llm_available()[0]:
            log.info("pre-warm skipped (LLM not configured or budget used up)")
            return
        samples = json.loads((config.WEB_DIR / "static" / "samples.json").read_text(encoding="utf-8"))
        for s in samples:
            await analyze_text(s["text"], count_seen=False)
        log.info("pre-warmed %d demo samples", len(samples))
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - never break startup
        log.exception("pre-warm failed; the app keeps running")
