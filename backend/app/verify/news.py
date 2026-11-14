"""News search for claim verification.

Auto-selects the best configured provider:
1. Brave Search API (`BRAVE_API_KEY`) -> `/res/v1/news/search` + `/res/v1/web/search` (`extra_snippets=true`, ~1 s).
2. Oxylabs AI Studio Instant Search (`SEARCH_API_KEY` / `OXYLABS_API_KEY`) -> `/search/instant` (~2 s).
3. GDELT DOC 2.0 (free fallback when no search API keys are configured; 5.5 s spacing per GDELT rules).
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

from app import config
from app.pipeline import websearch
from app.receipts import domain as dom
from app.swarm.collectors.http import BudgetExhausted, CollectorError, HTTPStatus, RateLimited, Session

GDELT = "https://api.gdeltproject.org/api/v2/doc/doc"
MIN_INTERVAL_S = 5.5
REQUEST_TIMEOUT_S = 12.0
MIN_TIME_FOR_REQUEST_S = 6.0
CACHE_TTL_S = 3600
_lock: asyncio.Lock | None = None
_last = [0.0]
_cache: dict[tuple, tuple[float, list]] = {}


class SearchUnavailable(Exception):
    """The search could not run (busy, error, time). Message is user-facing."""


def backend() -> str:
    explicit = config.env("NEWS_SEARCH", "").lower()
    if explicit == "gdelt":
        return "gdelt"
    if explicit == "brave" and config.brave_api_key():
        return "brave"
    if explicit == "oxylabs" and config.oxylabs_api_key():
        return "oxylabs"
    active = config.active_search_provider()
    if active in ("brave", "oxylabs"):
        return active
    return "gdelt"


def reset() -> None:
    global _lock
    _cache.clear()
    _last[0] = 0.0
    _lock = None
    websearch.reset()


def clean_keywords(text: str) -> str:
    """GDELT rejects very short tokens ("phrase too short"): keep words of >= 3 characters."""
    import re
    return " ".join(w for w in re.findall(r"[^\W_][\w'’.-]*", text or "") if sum(ch.isalpha() for ch in w) >= 3)


def any_of(terms: list[str]) -> str:
    """(term1 OR "multi word term" OR ...) for a lenient search."""
    parts = []
    for t in terms:
        t = clean_keywords(t)
        if t:
            parts.append(f'"{t}"' if " " in t else t)
    if not parts:
        return ""
    return parts[0] if len(parts) == 1 else "(" + " OR ".join(parts) + ")"


def _gdelt_query(keywords: str, domains: list[str]) -> str:
    q = keywords.strip()
    if domains:
        filt = [f"domain:{d}" for d in domains]
        q += " " + (filt[0] if len(filt) == 1 else "(" + " OR ".join(filt) + ")")
    return q


def _seendate(s: str | None) -> float | None:
    try:
        return datetime.strptime(s or "", "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


async def _gdelt(s: Session, keywords: str, domains: list[str], days: int, limit: int) -> list[dict]:
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    params = {"query": _gdelt_query(keywords, domains), "mode": "ArtList", "format": "json", "maxrecords": limit,
              "timespan": f"{days}d", "sort": "HybridRel"}
    async with _lock:                       # reserve a start slot >= 5.5 s after the previous one
        now = time.monotonic()
        start_at = max(now, _last[0] + MIN_INTERVAL_S)
        if start_at - now > s.remaining() - MIN_TIME_FOR_REQUEST_S:
            raise SearchUnavailable("The news search could not run within the time limit.")
        _last[0] = start_at
    if start_at > now:
        await asyncio.sleep(start_at - now)
    try:
        data = await s.request_json("GET", GDELT, params=params, timeout_s=REQUEST_TIMEOUT_S)
    except RateLimited:
        raise SearchUnavailable("The news index (GDELT) is busy right now (rate limit).") from None
    except HTTPStatus as exc:
        raise SearchUnavailable(f"The news index (GDELT) answered with an error (HTTP {exc.status}).") from None
    except CollectorError as exc:
        msg = "The news index (GDELT) did not return results." if exc.code == "bad_json" else exc.message
        raise SearchUnavailable(msg) from None
    except BudgetExhausted:
        raise SearchUnavailable("The news search could not run within the time limit.") from None
    out = []
    for a in (data or {}).get("articles") or [] if isinstance(data, dict) else []:
        url = a.get("url")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            continue
        try:
            reg = dom.registrable(dom.normalize_host(url))
        except ValueError:
            continue
        out.append({"url": url, "title": a.get("title") or "", "domain": reg, "ts": _seendate(a.get("seendate")),
                    "language": a.get("language")})
    return out


async def _search_api(s: Session, keywords: str, domains: list[str], days: int, limit: int) -> list[dict]:
    q = keywords.replace('"', "").strip()
    if domains:
        sites = [f"site:{d}" for d in domains]
        q = f"{q} {sites[0]}" if len(sites) == 1 else f"{q} ({' OR '.join(sites)})"
    # Domain-restricted (`site:`) lookups use Brave's general web index directly (with freshness) because
    # Brave's `/news/search` vertical omits many regional/non-US domains that `/web/search` indexes cleanly.
    kind = "web" if domains else "news"
    try:
        hits = await websearch.search_web(s, q.strip(), kind=kind, days=days, limit=limit)
        # Historical fallback: if recent window (14d/30d) returned 0 articles, retry once without date filter
        # on general web index so older claims, archival reports, and previously debunked hoaxes are still found.
        if not hits and days and s.remaining() > 4.0:
            hits = await websearch.search_web(s, q.strip(), kind="web", days=None, limit=limit)
    except websearch.SearchProviderError as exc:
        raise SearchUnavailable(str(exc)) from None
    if domains:
        allowed = set(domains)
        hits = [h for h in hits if h["domain"] in allowed]
    return [{"url": h["url"], "title": h["title"], "domain": h["domain"], "ts": h["ts"],
             "language": h.get("language"), "snippets": h.get("snippets") or []}
            for h in hits[:limit]]


async def search(s: Session, keywords: str, *, domains: list[str] | None = None, days: int = 14, limit: int = 25) -> list[dict]:
    """News articles for keywords in the last `days` days, optionally only on `domains`. Raises SearchUnavailable."""
    name = backend()
    key = (name, keywords.lower().strip(), tuple(sorted(domains or [])), days, limit)
    hit = _cache.get(key)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    if not keywords.strip() and not domains:
        raise SearchUnavailable("No search words.")
    if name == "gdelt" and not keywords.strip():
        raise SearchUnavailable("No search words.")
    fn = _gdelt if name == "gdelt" else _search_api
    result = await fn(s, keywords, list(domains or []), days, limit)
    _cache[key] = (time.monotonic() + CACHE_TTL_S, result)
    return result
