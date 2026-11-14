"""Unified search client: Brave Search API (primary) + Oxylabs AI Studio Instant Search (fallback).

All HTTP calls travel through `app.swarm.collectors.http.Session` so test isolation (`conftest.py`),
request budgets, size caps, and SSRF checks apply consistently.
"""
from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime, timedelta, timezone

from app import config
from app.pipeline import oxylabs
from app.receipts import domain as dom
from app.store import get_store, monotonic_ms
from app.swarm.collectors.http import BudgetExhausted, CollectorError, HTTPStatus, RateLimited, Session

BRAVE_NEWS_URL = "https://api.search.brave.com/res/v1/news/search"
BRAVE_WEB_URL = "https://api.search.brave.com/res/v1/web/search"
OXYLABS_INSTANT_URL = "https://api-aistudio.oxylabs.io/search/instant"
BRAVE_MIN_INTERVAL_S = 1.05
BRAVE_TIMEOUT_S = 10.0
OXY_TIMEOUT_S = 14.0

MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_ABS_DATE = re.compile(r"^\s*([A-Z][a-z]{2})[a-z]*\.?\s+(\d{1,2}),\s+(\d{4})\s*[—–·-]")
_REL_DATE = re.compile(r"^\s*(\d{1,3})\s+(minute|hour|day|week|month)s?\s+ago\b", re.IGNORECASE)
_brave_lock: asyncio.Lock | None = None
_brave_last = [0.0]


class SearchProviderError(Exception):
    """User-safe search failure."""


def reset() -> None:
    global _brave_lock
    _brave_lock = None
    _brave_last[0] = 0.0


def parse_timestamp(page_age: str | None = None, age_text: str | None = None, snippet: str | None = None,
                    now: datetime | None = None) -> float | None:
    if page_age and isinstance(page_age, str):
        raw = page_age.strip()
        raw = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        try:
            dt = datetime.fromisoformat(raw)
            return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()
        except ValueError:
            pass
    for cand in (age_text, snippet):
        if not cand or not isinstance(cand, str):
            continue
        m = _ABS_DATE.match(cand)
        if m and m.group(1).lower() in MONTHS:
            try:
                return datetime(int(m.group(3)), MONTHS[m.group(1).lower()], int(m.group(2)), tzinfo=timezone.utc).timestamp()
            except ValueError:
                pass
        m = _REL_DATE.match(cand)
        if m:
            unit = {"minute": 60, "hour": 3600, "day": 86400, "week": 604800, "month": 2592000}[m.group(2).lower()]
            return ((now or datetime.now(timezone.utc)) - timedelta(seconds=int(m.group(1)) * unit)).timestamp()
    return None


def _freshness(days: int | None) -> str | None:
    if not days:
        return None
    if days <= 1:
        return "pd"
    if days <= 7:
        return "pw"
    if days <= 31:
        return "pm"
    now = datetime.now(timezone.utc)
    return f"{(now - timedelta(days=days)).strftime('%Y-%m-%d')}to{now.strftime('%Y-%m-%d')}"


async def _pace_brave(s: Session) -> None:
    global _brave_lock
    if _brave_lock is None:
        _brave_lock = asyncio.Lock()
    async with _brave_lock:
        now = time.monotonic()
        start_at = max(now, _brave_last[0] + BRAVE_MIN_INTERVAL_S)
        if start_at - now > s.remaining() - 2.0:
            raise SearchProviderError("The search could not run within the time limit.")
        _brave_last[0] = start_at
    if start_at > now:
        await asyncio.sleep(start_at - now)


def _hit(url: str, title: str, desc: str, extras: list[str], ts: float | None,
         pos: int | None = None, lang: str | None = None) -> dict | None:
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return None
    try:
        host = dom.normalize_host(url)
        reg = dom.registrable(host)
    except ValueError:
        return None
    snippets: list[str] = []
    for x in [desc] + list(extras or []):
        if isinstance(x, str) and x.strip() and x.strip() not in snippets:
            snippets.append(x.strip())
    return {"url": url, "title": (title or "").strip(), "snippet": " … ".join(snippets), "snippets": snippets,
            "host": host, "domain": reg, "position": pos, "ts": ts, "language": lang}


async def brave_search(s: Session, query: str, *, kind: str = "web", days: int | None = None, limit: int = 20) -> list[dict]:
    key = config.brave_api_key()
    if not key:
        raise SearchProviderError("Brave Search is not configured on this server.")
    url = BRAVE_NEWS_URL if kind == "news" else BRAVE_WEB_URL
    params: dict[str, str | int] = {"q": query, "count": max(1, min(20 if kind == "web" else 50, limit)), "extra_snippets": "true"}
    if fresh := _freshness(days):
        params["freshness"] = fresh
    for attempt in range(2):
        await _pace_brave(s)
        try:
            data = await s.request_json("GET", url, params=params, headers={"X-Subscription-Token": key}, timeout_s=BRAVE_TIMEOUT_S)
            break
        except RateLimited:
            if attempt == 0 and s.remaining() > 3.0:
                await asyncio.sleep(1.1)
                continue
            raise SearchProviderError("Web search rate limit reached; please try again shortly.") from None
        except HTTPStatus as exc:
            if exc.status in (401, 403):
                raise SearchProviderError("Open-web search is not configured correctly on this server.") from None
            raise SearchProviderError(f"Open-web search failed (HTTP {exc.status}).") from None
        except (CollectorError, BudgetExhausted) as exc:
            raise SearchProviderError(exc.message if isinstance(exc, CollectorError) else "The search could not run within the time limit.") from None
    items = ((data or {}).get("results") if kind == "news" else ((data or {}).get("web") or {}).get("results")) if isinstance(data, dict) else []
    out = []
    for idx, r in enumerate(items or [], start=1):
        if isinstance(r, dict):
            desc = r.get("description") or ""
            extras = [x for x in (r.get("extra_snippets") or []) if isinstance(x, str)]
            h = _hit(r.get("url") or "", r.get("title") or "", desc, extras,
                     parse_timestamp(r.get("page_age"), r.get("age"), desc), idx, r.get("language"))
            if h:
                out.append(h)
    return out[:limit]


async def oxylabs_search(s: Session, query: str, *, limit: int = 10) -> list[dict]:
    key = config.oxylabs_api_key()
    if not key:
        raise SearchProviderError("Open-web search is not configured on this server.")
    cap = config.oxylabs_daily_cap()
    if cap > 0 and oxylabs.calls_today() >= cap:
        raise SearchProviderError("Daily open-web search budget reached on this server.")
    t0 = monotonic_ms()
    try:
        data = await s.post_json(OXYLABS_INSTANT_URL, {"query": query, "limit": max(1, min(10, limit))},
                                 headers={"x-api-key": key, "Content-Type": "application/json"}, timeout_s=OXY_TIMEOUT_S)
        get_store().log_usage("oxylabs_search", "oxylabs-ai-studio", 0, 0, monotonic_ms() - t0, ok=True)
    except HTTPStatus as exc:
        get_store().log_usage("oxylabs_search", "oxylabs-ai-studio", 0, 0, monotonic_ms() - t0, ok=False, error=f"HTTP_{exc.status}")
        if exc.status in (401, 403):
            raise SearchProviderError("Open-web search is not configured correctly on this server.") from None
        raise SearchProviderError(f"Open-web search failed (HTTP {exc.status}).") from None
    except RateLimited:
        get_store().log_usage("oxylabs_search", "oxylabs-ai-studio", 0, 0, monotonic_ms() - t0, ok=False, error="429")
        raise SearchProviderError("Open-web search rate limit reached.") from None
    except (CollectorError, BudgetExhausted) as exc:
        get_store().log_usage("oxylabs_search", "oxylabs-ai-studio", 0, 0, monotonic_ms() - t0, ok=False, error="timeout")
        raise SearchProviderError(exc.message if isinstance(exc, CollectorError) else "The search could not run within the time limit.") from None
    if not isinstance(data, dict):
        return []
    items = data.get("data") if isinstance(data.get("data"), list) else (data.get("results") or [])
    out = []
    for idx, r in enumerate(items, start=1):
        if isinstance(r, dict):
            outline = [o for o in (r.get("outline") or []) if isinstance(o, str)]
            desc = r.get("description") if isinstance(r.get("description"), str) else (outline[0] if outline else "")
            extras = outline[1:] + ([r["content"].strip()[:500]] if isinstance(r.get("content"), str) and r["content"].strip() else [])
            pos = (r.get("metadata") or {}).get("position") if isinstance(r.get("metadata"), dict) else idx
            h = _hit(r.get("url") or "", r.get("title") or "", desc, extras, parse_timestamp(None, None, desc), pos)
            if h:
                out.append(h)
    return out[:limit]


async def search_web(s: Session, query: str, *, kind: str = "web", days: int | None = None, limit: int = 20) -> list[dict]:
    provider = config.active_search_provider()
    if provider == "brave":
        try:
            hits = await brave_search(s, query, kind=kind, days=days, limit=limit)
            if not hits and kind == "news" and "site:" in query and s.remaining() > 3.5:
                hits = await brave_search(s, query, kind="web", days=days, limit=limit)
            return hits
        except SearchProviderError:
            if config.oxylabs_api_key() and config.search_provider() != "brave" and s.remaining() > 4.0:
                after = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d") if days else None
                q_oxy = f"{query} after:{after}" if after and "after:" not in query else query
                return await oxylabs_search(s, q_oxy, limit=min(10, limit))
            raise
    if provider == "oxylabs":
        after = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d") if days else None
        q_oxy = f"{query} after:{after}" if after and "after:" not in query else query
        return await oxylabs_search(s, q_oxy, limit=min(10, limit))
    raise SearchProviderError("Open-web search is not configured on this server.")
