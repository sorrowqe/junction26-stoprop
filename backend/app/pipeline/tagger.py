"""LLM technique/claim tagging via the AI Router (CLAUDE.md §4 step 3 + "Caching & freshness").

- System prompt: static (taxonomy rendered in), cache-friendly, versioned by its own hash.
- Cache per PARAGRAPH, key = sha256(normalize(paragraph)) + model + prompt_version + taxonomy_version,
  no TTL. Results are stored with paragraph-local sentence indexes, so the same paragraph inside a
  different text (different p{n}) still hits the cache. Validation always re-runs on read.
- Single-flight: concurrent requests for the same uncached paragraph wait for one computation.
- Uncached paragraphs are batched into chunks of ~1200 words, one LLM call per chunk.
- Fail soft: if a chunk fails (after the client's retry and one JSON retry), its paragraphs are
  reported as failed and the caller uses heuristic spans there. Failures are not cached.
- private=True: cache is read, but nothing is written (no cache rows, no sighting counts).
"""
from __future__ import annotations

import asyncio
import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache

from app import config, taxonomy
from app.llm import budget
from app.llm import client as llm
from app.pipeline.segment import Paragraph, Segmented
from app.store import get_store

CHUNK_WORDS = 1200
MAX_CONCURRENT_CALLS = 3
WAIT_TIMEOUT_S = 120
MAX_OUTPUT_TOKENS = 2000
HINT_TYPES = (
    "bandwagon_phrase", "vague_authority", "emotive", "intensifier", "absolutist", "us_them", "all_caps",
    "exclamation", "number_unattributed", "number_attributed", "attribution", "link", "quotation",
    "balance_marker",
)

_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿"), None)
_inflight: dict[str, asyncio.Future] = {}
_semaphore: asyncio.Semaphore | None = None


def normalize(text: str) -> str:
    """NFKC, strip zero-width chars, collapse whitespace (cache key only; offsets use the original)."""
    t = unicodedata.normalize("NFKC", text).translate(_ZERO_WIDTH)
    return re.sub(r"\s+", " ", t).strip()


def paragraph_hash(text: str) -> str:
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ prompt

@lru_cache(maxsize=1)
def system_prompt() -> str:
    lines = []
    for t in taxonomy.load()["techniques"]:
        if not t.get("enabled", True) or t.get("tagger") is False:
            continue
        lines.append(f"- {t['label']} ({t['name']}): {t['definition']} Example: {t['example']}")
    template = (config.PROMPTS_DIR / "tagger_system.md").read_text(encoding="utf-8")
    return template.replace("{{TAXONOMY}}", "\n".join(lines))


@lru_cache(maxsize=1)
def prompt_version() -> str:
    return hashlib.sha256(system_prompt().encode("utf-8")).hexdigest()[:10]


def cache_key(para_hash: str, model: str) -> str:
    return f"{para_hash}:{model}:{prompt_version()}:t{taxonomy.version()}"


def user_message(seg: Segmented, paragraphs: list[Paragraph], hints: list[dict]) -> str:
    ids = {s.id for p in paragraphs for s in p.sentences}
    out = [f"Language: {seg.lang}", "", "Sentences:"]
    for p in paragraphs:
        for s in p.sentences:
            out.append(f"[{s.id}] {' '.join(s.text.split())}")
        out.append("")
    by_sentence: dict[str, list[str]] = {}
    for h in hints:
        if h["sentence_id"] in ids and h["type"] in HINT_TYPES:
            by_sentence.setdefault(h["sentence_id"], []).append(f'{h["type"]} "{" ".join(h["quote"].split())[:60]}"')
    out.append("Rule-based hints (may be wrong):")
    if by_sentence:
        for sid in sorted(by_sentence, key=lambda x: [int(n) for n in re.findall(r"\d+", x)]):
            out.append(f"{sid}: " + "; ".join(by_sentence[sid][:6]))
    else:
        out.append("(none)")
    return "\n".join(out)


# ----------------------------------------------------------------- results

@dataclass
class TagResult:
    candidates: list[dict] = field(default_factory=list)   # global sentence ids, source="llm"
    failed: set[str] = field(default_factory=set)            # paragraph ids that fell back to heuristics
    input_tokens: int = 0
    output_tokens: int = 0
    credits: float = 0.0
    llm_calls: int = 0
    cached_paragraphs: int = 0
    dropped_items: int = 0                                   # malformed / wrong sentence id


def _clean_item(item) -> dict | None:
    if not isinstance(item, dict):
        return None
    sid, quote, label = item.get("sentence_id"), item.get("quote"), item.get("label")
    if not (isinstance(sid, str) and isinstance(quote, str) and isinstance(label, str) and quote.strip()):
        return None
    try:
        conf = float(item.get("confidence", 0.5))
    except (TypeError, ValueError):
        conf = 0.5
    expl = item.get("explanation")
    return {
        "sentence_id": sid.strip(),
        "quote": quote,
        "label": label.strip(),
        "confidence": min(1.0, max(0.0, conf)),
        "explanation": expl.strip()[:400] if isinstance(expl, str) else "",
    }


def _split_by_paragraph(items: list, paragraphs: list[Paragraph]) -> tuple[dict[str, list[dict]], int]:
    """Model items -> {paragraph_id: [stored item with local sentence index 's']}."""
    local = {s.id: (p.id, i) for p in paragraphs for i, s in enumerate(p.sentences, start=1)}
    per_para: dict[str, list[dict]] = {p.id: [] for p in paragraphs}
    dropped = 0
    for raw in items:
        item = _clean_item(raw)
        if item is None or item["sentence_id"] not in local:
            dropped += 1
            continue
        pid, idx = local[item["sentence_id"]]
        stored = {k: item[k] for k in ("quote", "label", "confidence", "explanation")}
        stored["s"] = idx
        per_para[pid].append(stored)
    return per_para, dropped


def _to_candidates(para: Paragraph, stored: list[dict]) -> list[dict]:
    out = []
    for it in stored:
        idx = it.get("s", 0)
        if 1 <= idx <= len(para.sentences):
            out.append({
                "sentence_id": para.sentences[idx - 1].id,
                "quote": it["quote"],
                "label": it["label"],
                "confidence": it["confidence"],
                "explanation": it["explanation"],
                "source": "llm",
            })
    return out


def chunk_paragraphs(paragraphs: list[Paragraph], max_words: int = CHUNK_WORDS) -> list[list[Paragraph]]:
    chunks: list[list[Paragraph]] = []
    cur: list[Paragraph] = []
    words = 0
    for p in paragraphs:
        n = len(p.text.split())
        if cur and words + n > max_words:
            chunks.append(cur)
            cur, words = [], 0
        cur.append(p)
        words += n
    if cur:
        chunks.append(cur)
    return chunks


async def _call_chunk(seg: Segmented, chunk: list[Paragraph], hints: list[dict], model: str, res: TagResult):
    """One LLM call (+ one retry if the JSON is unusable). Returns {pid: items} or None on failure."""
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(MAX_CONCURRENT_CALLS)
    messages = [
        {"role": "system", "content": system_prompt()},
        {"role": "user", "content": user_message(seg, chunk, hints)},
    ]
    for _attempt in range(2):
        if not budget.llm_available()[0]:
            return None
        async with _semaphore:
            try:
                out = await llm.chat(messages, model=model, max_tokens=MAX_OUTPUT_TOKENS, kind="tagger")
            except llm.LLMError:
                return None
        res.llm_calls += 1
        res.input_tokens += out.input_tokens
        res.output_tokens += out.output_tokens
        res.credits += getattr(out, "credits", 0.0) or 0.0
        items = llm.extract_json_array(out.text)
        if items is not None:
            per_para, dropped = _split_by_paragraph(items, chunk)
            res.dropped_items += dropped
            return per_para
    return None


async def tag(seg: Segmented, hints: list[dict], *, private: bool = False) -> TagResult:
    res = TagResult()
    if not seg.paragraphs:
        return res
    store = get_store()
    model = config.model_tagger()
    loop = asyncio.get_running_loop()

    keys = {p.id: cache_key(paragraph_hash(p.text), model) for p in seg.paragraphs}
    stored: dict[str, list[dict]] = {}
    waits: dict[str, asyncio.Future] = {}
    mine: dict[str, asyncio.Future] = {}   # key -> future I must resolve
    todo: list[Paragraph] = []

    for p in seg.paragraphs:
        key = keys[p.id]
        hit = store.cache_get(key)
        if hit is not None:
            stored[p.id] = hit
            res.cached_paragraphs += 1
        elif key in _inflight:
            waits[p.id] = _inflight[key]
        else:
            fut = loop.create_future()
            _inflight[key] = fut
            mine[key] = fut
            todo.append(p)

    try:
        if todo:
            # one representative paragraph per key (a text may repeat a paragraph)
            unique: dict[str, Paragraph] = {}
            for p in todo:
                unique.setdefault(keys[p.id], p)
            chunks = chunk_paragraphs(list(unique.values()))
            results = await asyncio.gather(*(_call_chunk(seg, c, hints, model, res) for c in chunks))
            for chunk, per_para in zip(chunks, results):
                for p in chunk:
                    key = keys[p.id]
                    value = None if per_para is None else per_para.get(p.id, [])
                    if value is not None and not private:
                        store.cache_put(key, paragraph_hash(p.text), model, prompt_version(), taxonomy.version(), value)
                    fut = mine.get(key)
                    if fut is not None and not fut.done():
                        fut.set_result(value)
            for p in todo:
                value = mine[keys[p.id]].result() if mine[keys[p.id]].done() else None
                if value is None:
                    res.failed.add(p.id)
                else:
                    stored[p.id] = value
    finally:
        for key, fut in mine.items():
            if not fut.done():
                fut.set_result(None)
            if _inflight.get(key) is fut:
                del _inflight[key]

    for pid, fut in waits.items():
        try:
            value = await asyncio.wait_for(asyncio.shield(fut), WAIT_TIMEOUT_S)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            value = None
        if value is None:
            res.failed.add(pid)
        else:
            stored[pid] = value
            res.cached_paragraphs += 1

    for p in seg.paragraphs:
        if p.id in stored:
            res.candidates.extend(_to_candidates(p, stored[p.id]))
    return res


def reset_inflight() -> None:
    """For tests."""
    global _semaphore
    _inflight.clear()
    _semaphore = None
