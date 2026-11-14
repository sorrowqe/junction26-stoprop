"""News search for claim verification (pluggable). NEWS_SEARCH=gdelt (default) | oxylabs.

GDELT DOC 2.0 (https://api.gdeltproject.org/api/v2/doc/doc, free, no key): mode=ArtList, format=json,
timespan=Nd, `domain:` filters (OR-ed in parentheses for an outlet's several domains). GDELT asks for at most one
request every 5 seconds per client, so request STARTS are spaced 5.5 s apart process-wide (GDELT often takes
10-15 s to answer, so requests may overlap); a 429 or an error page means "search unavailable", never "nothing
found". Results are cached for 1 hour per query.
Oxylabs (if configured): Web API /v1/search with `site:` and `after:` operators.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone

from app import config
from app.receipts import domain as dom
from app.swarm.collectors.http import BudgetExhausted, CollectorError, HTTPStatus, RateLimited, Session

GDELT = "https://api.gdeltproject.org/api/v2/doc/doc"
MIN_INTERVAL_S = 5.5
REQUEST_TIMEOUT_S = 16.0
MIN_TIME_FOR_REQUEST_S = 8.0
CACHE_TTL_S = 3600
_lock: asyncio.Lock | None = None
_last = [0.0]
_cache: dict[tuple, tuple[float, list]] = {}


class SearchUnavailable(Exception):
    """The search could not run (busy, error, time). Message is user-facing."""


def backend() -> str:
    choice = config.env("NEWS_SEARCH", "gdelt").lower()
    if choice == "oxylabs" and config.search_api_key():
        return "oxylabs"
    return "gdelt"


def reset() -> None:
    global _lock
    _cache.clear()
    _last[0] = 0.0
    _lock = None


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


async def _oxylabs(s: Session, keywords: str, domains: list[str], days: int, limit: int) -> list[dict]:
    from app.trace import search as tsearch
    after = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    site = (" (" + " OR ".join(f"site:{d}" for d in domains) + ")") if domains else ""
    try:
        hits = await tsearch.web(s, f"{keywords}{site} after:{after}".replace('"', ""), exact=False)
    except tsearch.SearchUnavailable as exc:
        raise SearchUnavailable(str(exc)) from None
    return [{"url": h["url"], "title": h["title"], "domain": h["domain"], "ts": h["ts"], "language": None}
            for h in hits[:limit]]


async def search(s: Session, keywords: str, *, domains: list[str] | None = None, days: int = 14, limit: int = 25) -> list[dict]:
    """News articles for keywords in the last `days` days, optionally only on `domains`. Raises SearchUnavailable."""
    name = backend()
    key = (name, keywords.lower().strip(), tuple(sorted(domains or [])), days, limit)
    hit = _cache.get(key)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    fn = _gdelt if name == "gdelt" else _oxylabs
    if name == "gdelt" and not keywords.strip():
        raise SearchUnavailable("No search words.")
    result = await fn(s, keywords, list(domains or []), days, limit)
    _cache[key] = (time.monotonic() + CACHE_TTL_S, result)
    return result
