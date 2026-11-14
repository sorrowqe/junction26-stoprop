"""Read candidate articles and judge them against a claim.

Articles are fetched with the SSRF-safe fetcher (pipeline.extract); if a page cannot be read (paywall, bot
block), only its headline is judged. One MODEL_SUMMARY call per claim judges all its articles: supports /
contradicts / unrelated, each with an evidence quote that code must find VERBATIM in that article's text or
headline. A stance whose quote is not found is discarded (treated as unrelated) and counted.
"""
from __future__ import annotations

import asyncio
import re

from app import config
from app.llm import client as llm
from app.pipeline import extract
from app.trace.fingerprint import norm

EXCERPT_CHARS = 2500
FETCH_TIMEOUT_S = 8.0
SYSTEM = """You check whether news articles report a specific claim. For each numbered article decide:
- "supports": the article reports the same event or fact as the claim;
- "contradicts": the article says the claim is false, or reports something that rules it out;
- "unrelated": anything else (different event, only the same topic, or not enough information).
For "supports" and "contradicts" copy one evidence sentence or phrase (max 30 words) from that article's text
EXACTLY as written. Use only the article texts, not outside knowledge. Return only a JSON array:
[{"article": <number>, "stance": "supports|contradicts|unrelated", "quote": "<exact words or empty>"}]"""


def excerpt(text: str, entities: list[str], limit: int = EXCERPT_CHARS) -> str:
    """Paragraphs mentioning the claim's entities first, then the start of the article."""
    paras = [p.strip() for p in re.split(r"\n\s*\n|\n", text or "") if p.strip()]
    keys = [e.lower() for e in entities if len(e) >= 3]
    hit = [p for p in paras if any(k in p.lower() for k in keys)]
    rest = [p for p in paras if p not in hit]
    out, size = [], 0
    for p in hit + rest:
        if size + len(p) > limit:
            break
        out.append(p)
        size += len(p) + 1
    return "\n".join(out) if out else (text or "")[:limit]


async def read(article: dict, timeout_s: float) -> dict:
    """Adds 'text' (main text or '') and 'read': True/False."""
    try:
        async with asyncio.timeout(max(1.0, min(FETCH_TIMEOUT_S, timeout_s))):
            got = await extract.fetch_article(article["url"])
        return {**article, "text": got.text, "read": True, "title": article.get("title") or got.title or "",
                "date": got.date}
    except Exception:  # noqa: BLE001 - paywalls, bot blocks, timeouts: judge the headline only
        return {**article, "text": "", "read": False}


def quote_found(quote: str, article: dict) -> bool:
    q = norm(quote or "")
    if len(q.split()) < 3:
        return False
    return q in norm(article.get("text") or "") or q in norm(article.get("title") or "")


async def judge(claim: dict, articles: list[dict]) -> tuple[list[dict], dict]:
    """Returns (articles with 'stance', 'quote', 'quote_verified'), meta with llm cost and discarded count."""
    meta = {"llm_calls": 0, "credits": 0.0, "input_tokens": 0, "output_tokens": 0, "discarded": 0}
    if not articles:
        return [], meta
    blocks = []
    for i, a in enumerate(articles, start=1):
        body = excerpt(a.get("text") or "", claim.get("entities") or []) if a.get("read") else "(article text not available)"
        blocks.append(f"[Article {i}] {a.get('domain')} | {a.get('title') or ''}\n{body}")
    user = f"Claim: {claim['claim']}\n\n" + "\n\n".join(blocks)
    out = await llm.chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
                         model=config.model_summary(), max_tokens=600, kind="verify_judge")
    meta.update(llm_calls=1, credits=out.credits, input_tokens=out.input_tokens, output_tokens=out.output_tokens)
    verdicts = {}
    for item in llm.extract_json_array(out.text) or []:
        if isinstance(item, dict) and isinstance(item.get("article"), int) and 1 <= item["article"] <= len(articles):
            verdicts[item["article"] - 1] = item
    judged = []
    for i, a in enumerate(articles):
        v = verdicts.get(i, {})
        stance = v.get("stance") if v.get("stance") in ("supports", "contradicts") else "unrelated"
        quote = v.get("quote") if isinstance(v.get("quote"), str) else ""
        ok = stance != "unrelated" and quote_found(quote, a)
        if stance != "unrelated" and not ok:
            meta["discarded"] += 1
            stance, quote = "unrelated", ""
        judged.append({k: a.get(k) for k in ("url", "title", "domain", "ts", "read")}
                      | {"stance": stance, "quote": quote if ok else "", "quote_verified": ok})
    return judged, meta
