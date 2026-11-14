"""Exact-phrase search for copies of a fingerprint sentence.

- Open web: Oxylabs Web API /v1/search (ranked organic search results), SEARCH_PROVIDER=oxylabs and
  SEARCH_API_KEY (Bearer). The query is the phrase in double quotes. Fields used: url, title, outline
  (snippets), metadata.position; a date is taken only when a snippet starts with one.
- Bluesky: app.bsky.feed.searchPosts on api.bsky.app (works without login; public.api.bsky.app refuses search).
- Mastodon: full-text post search needs a logged-in account on every server, so it is reported as not available.
Only what the search APIs return for each result is used: no profile lookups, no identity research.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from app import config
from app.receipts import domain as dom
from app.swarm.collectors.http import CollectorError, HTTPStatus, RateLimited, Session

OXYLABS_SEARCH = "https://webapi.oxylabs.io/v1/search"
BSKY_SEARCH = "https://api.bsky.app/xrpc/app.bsky.feed.searchPosts"
WEB_MAX_RESULTS = 20
BSKY_LIMIT = 100
WEB_TIMEOUT_S = 15.0
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_ABS_DATE = re.compile(r"^\s*([A-Z][a-z]{2})[a-z]*\.? (\d{1,2}), (\d{4})\s*[—–-]")
_REL_DATE = re.compile(r"^\s*(\d{1,2}) (minute|hour|day|week)s? ago\s*[—–-]")


class SearchUnavailable(Exception):
    """A search source is not configured / not offered; message is user-facing."""


def web_configured() -> bool:
    return config.search_provider() == "oxylabs" and bool(config.search_api_key())


def snippet_date(snippet: str, now: datetime | None = None) -> float | None:
    """'Nov 3, 2026 — …' or '5 days ago — …' at the start of a search snippet -> unix time (UTC), else None."""
    m = _ABS_DATE.match(snippet or "")
    if m and m.group(1).lower() in MONTHS:
        try:
            return datetime(int(m.group(3)), MONTHS[m.group(1).lower()], int(m.group(2)), tzinfo=timezone.utc).timestamp()
        except ValueError:
            return None
    m = _REL_DATE.match(snippet or "")
    if m:
        unit = {"minute": 60, "hour": 3600, "day": 86400, "week": 7 * 86400}[m.group(2)]
        return ((now or datetime.now(timezone.utc)) - timedelta(seconds=int(m.group(1)) * unit)).timestamp()
    return None


async def web(s: Session, phrase: str) -> list[dict]:
    if not web_configured():
        raise SearchUnavailable("Open-web search is not configured on this server.")
    try:
        data = await s.post_json(OXYLABS_SEARCH, {"query": f'"{phrase}"', "max_results": WEB_MAX_RESULTS},
                                 headers={"Authorization": f"Bearer {config.search_api_key()}"}, timeout_s=WEB_TIMEOUT_S)
    except HTTPStatus as exc:
        if exc.status in (401, 403):
            raise SearchUnavailable("Open-web search is not configured correctly on this server.") from None
        raise SearchUnavailable(f"Open-web search failed (HTTP {exc.status}).") from None
    out = []
    for r in (data or {}).get("results") or []:
        url = r.get("url")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            continue
        try:
            host = dom.normalize_host(url)
            reg = dom.registrable(host)
        except ValueError:
            continue
        outline = [o for o in (r.get("outline") or []) if isinstance(o, str)]
        snippet = " … ".join(outline)
        out.append({"url": url, "title": r.get("title") or "", "snippet": snippet, "host": host, "domain": reg,
                    "position": (r.get("metadata") or {}).get("position"), "ts": snippet_date(outline[0] if outline else "")})
    return out


async def bluesky(s: Session, phrase: str) -> list[dict]:
    try:
        data = await s.get_json(BSKY_SEARCH, params={"q": f'"{phrase}"', "limit": BSKY_LIMIT, "sort": "latest"})
    except HTTPStatus as exc:
        raise SearchUnavailable(f"Bluesky search is not available right now (HTTP {exc.status}).") from None
    except RateLimited:
        raise SearchUnavailable("Bluesky asked us to slow down; its search was skipped.") from None
    return [p for p in (data or {}).get("posts") or [] if isinstance(p, dict) and "uri" in p]


MASTODON_NOTE = ("Mastodon: not searched. Full-text post search needs a logged-in account on every Mastodon server, "
                 "and Stoprop does not log in anywhere.")
__all__ = ["web", "bluesky", "web_configured", "snippet_date", "SearchUnavailable", "MASTODON_NOTE", "CollectorError"]
