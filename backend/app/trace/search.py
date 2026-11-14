"""Exact-phrase search for copies of a fingerprint sentence.

- Open web: Brave Search API (`BRAVE_API_KEY`) or Oxylabs AI Studio Instant Search (`SEARCH_API_KEY` /
  `OXYLABS_API_KEY`) via `app.pipeline.websearch`.
- Bluesky: app.bsky.feed.searchPosts on api.bsky.app (works without login; public.api.bsky.app refuses search).
- Mastodon: full-text post search needs a logged-in account on every server, so it is reported as not available.
Only what the search APIs return for each result is used: no profile lookups, no identity research.
"""
from __future__ import annotations

from datetime import datetime

from app import config
from app.pipeline import websearch
from app.swarm.collectors.http import CollectorError, HTTPStatus, RateLimited, Session

BSKY_SEARCH = "https://api.bsky.app/xrpc/app.bsky.feed.searchPosts"
HN_SEARCH = "https://hn.algolia.com/api/v1/search_by_date"
WEB_MAX_RESULTS = 20
BSKY_LIMIT = 50
FORUM_LIMIT = 20


class SearchUnavailable(Exception):
    """A search source is not configured / not offered; message is user-facing."""


def web_configured() -> bool:
    return config.active_search_provider() in ("brave", "oxylabs")


def snippet_date(snippet: str, now: datetime | None = None) -> float | None:
    """'Nov 3, 2026 — …' or '5 days ago — …' at the start of a search snippet -> unix time (UTC), else None."""
    return websearch.parse_timestamp(None, None, snippet, now=now)


async def web(s: Session, phrase: str, exact: bool = True) -> list[dict]:
    if not web_configured():
        raise SearchUnavailable("Open-web search is not configured on this server.")
    q = f'"{phrase}"' if exact else phrase
    try:
        return await websearch.search_web(s, q, kind="web", limit=WEB_MAX_RESULTS)
    except websearch.SearchProviderError as exc:
        raise SearchUnavailable(str(exc)) from None


async def bluesky(s: Session, phrase: str, exact: bool = True) -> list[dict]:
    q = f'"{phrase}"' if exact else phrase
    try:
        data = await s.request_json("GET", BSKY_SEARCH, params={"q": q, "limit": BSKY_LIMIT, "sort": "latest"},
                                    timeout_s=12.0)
    except HTTPStatus as exc:
        raise SearchUnavailable(f"Bluesky search is not available right now (HTTP {exc.status}).") from None
    except RateLimited:
        raise SearchUnavailable("Bluesky asked us to slow down; its search was skipped.") from None
    return [p for p in (data or {}).get("posts") or [] if isinstance(p, dict) and "uri" in p]


async def reddit_posts(s: Session, phrase: str, exact: bool = True) -> list[dict]:
    """Search public Reddit threads globally via configured web search (`site:reddit.com`)."""
    if not web_configured():
        raise SearchUnavailable("Open-web search is required for global Reddit search.")
    q = f'"{phrase}" site:reddit.com' if exact else f"{phrase} site:reddit.com"
    try:
        return await websearch.search_web(s, q, kind="web", limit=FORUM_LIMIT)
    except websearch.SearchProviderError as exc:
        raise SearchUnavailable(str(exc)) from None


async def telegram_posts(s: Session, phrase: str, exact: bool = True) -> list[dict]:
    """Search indexed public Telegram channels/posts globally (`site:t.me`)."""
    if not web_configured():
        raise SearchUnavailable("Open-web search is required for Telegram channel search.")
    q = f'"{phrase}" site:t.me' if exact else f"{phrase} site:t.me"
    try:
        return await websearch.search_web(s, q, kind="web", limit=FORUM_LIMIT)
    except websearch.SearchProviderError as exc:
        raise SearchUnavailable(str(exc)) from None


async def hackernews_posts(s: Session, phrase: str, exact: bool = True) -> list[dict]:
    """Search public Hacker News stories and comments via Algolia API."""
    q = f'"{phrase}"' if exact else phrase
    try:
        data = await s.get_json(HN_SEARCH, params={"query": q, "hitsPerPage": FORUM_LIMIT})
    except (HTTPStatus, RateLimited, CollectorError) as exc:
        raise SearchUnavailable(f"Hacker News search is busy right now ({exc}).") from None
    hits = (data or {}).get("hits") if isinstance(data, dict) else []
    return [h for h in hits or [] if isinstance(h, dict) and h.get("objectID")]


MASTODON_NOTE = ("Mastodon: not searched. Full-text post search needs a logged-in account on every Mastodon server, "
                 "and Stoprop does not log in anywhere.")
__all__ = ["web", "bluesky", "reddit_posts", "telegram_posts", "hackernews_posts", "web_configured", "snippet_date",
           "SearchUnavailable", "MASTODON_NOTE", "CollectorError"]
