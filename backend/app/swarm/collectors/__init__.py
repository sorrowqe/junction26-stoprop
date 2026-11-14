"""Link-based collection for swarm mode: public post link(s) -> the same Post list an upload produces.

Supported: Bluesky (public AppView API), Mastodon-compatible servers (the server's public API),
YouTube (Data API v3 with GOOGLE_API_KEY). Closed platforms get an honest explanation and a pointer to
file upload. Limits per run: 2000 posts, 40 API requests, 25 s. Results are cached in memory for 10 min
per link (or link set); nothing is written to disk.
"""
from __future__ import annotations

import time
from urllib.parse import urlsplit

from app.swarm.collectors import bluesky, mastodon, youtube
from app.swarm.collectors.base import Collected
from app.swarm.collectors.http import BudgetExhausted, CollectorError, HTTPStatus, RateLimited, Session

MAX_LINKS = 50
CACHE_TTL_S = 600
CACHE_MAX = 200
PLATFORM_NAMES = {"bluesky": "Bluesky", "mastodon": "Mastodon", "youtube": "YouTube"}
UPLOAD_HINT = " If you can export the posts yourself, use “Upload a file” (CSV or JSON)."
CLOSED = {
    ("x.com", "twitter.com", "mobile.twitter.com", "t.co"):
        "X (Twitter) does not offer free public access to posts and replies: reading them requires a paid developer "
        "account and login, so Stoprop cannot fetch them.",
    ("facebook.com", "m.facebook.com", "fb.com", "fb.watch", "instagram.com", "threads.net", "threads.com"):
        "Meta's platforms (Facebook, Instagram, Threads) do not let outside tools read posts and comments without "
        "login and approved app access, so Stoprop cannot fetch them.",
    ("tiktok.com", "vm.tiktok.com"):
        "TikTok only opens its research API to approved academic researchers, so Stoprop cannot fetch comments.",
    ("reddit.com", "old.reddit.com", "new.reddit.com", "redd.it"):
        "Reddit requires registered API access and blocks anonymous automated reading, so Stoprop cannot fetch threads.",
}
SUPPORTED_HINT = ("Supported links: Bluesky posts (bsky.app/profile/…/post/…), Mastodon posts on any server "
                  "(…/@user/123…), and YouTube videos (youtube.com/watch?v=… or youtu.be/…).")

_cache: dict[tuple, tuple[float, Collected]] = {}


def _host(url: str) -> str:
    try:
        h = (urlsplit(url.strip()).hostname or "").lower()
    except ValueError:
        return ""
    return h[4:] if h.startswith("www.") else h


def classify(url: str) -> str:
    """Platform key, or raise CollectorError (400) with a plain explanation."""
    url = (url or "").strip()
    if not url:
        raise CollectorError("Please paste a link to a post.", 400, "empty")
    host = _host(url)
    for hosts, why in CLOSED.items():
        if host in hosts or any(host.endswith("." + h) for h in hosts):
            raise CollectorError(why + UPLOAD_HINT, 400, "closed_platform")
    if bluesky.parse(url):
        return "bluesky"
    if youtube.parse(url):
        return "youtube"
    if host in youtube.HOSTS or host == "youtube.com":
        raise CollectorError("That YouTube link does not point to a video. " + SUPPORTED_HINT, 400, "unsupported")
    if mastodon.parse(url):
        return "mastodon"
    if host == "bsky.app":
        raise CollectorError("That Bluesky link does not point to a post. " + SUPPORTED_HINT, 400, "unsupported")
    raise CollectorError("Stoprop cannot read posts from that link. " + SUPPORTED_HINT + UPLOAD_HINT, 400, "unsupported")


def _cached(key: tuple) -> Collected | None:
    hit = _cache.get(key)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    _cache.pop(key, None)
    return None


def _store(key: tuple, coll: Collected) -> None:
    if len(_cache) >= CACHE_MAX:
        _cache.pop(min(_cache, key=lambda k: _cache[k][0]))
    _cache[key] = (time.monotonic() + CACHE_TTL_S, coll)


def _note_no_text(coll: Collected) -> None:
    if coll.no_text:
        coll.notes.append(f"{coll.no_text} posts contain only images or video and no text, so they were left out.")


def reset_cache() -> None:
    _cache.clear()


async def collect_url(url: str, progress=None) -> tuple[Collected, bool]:
    """Thread mode for one link. Returns (collected, from_cache)."""
    url = url.strip()
    platform = classify(url)
    key = ("thread", url)
    hit = _cached(key)
    if hit:
        return hit, True
    module = {"bluesky": bluesky, "mastodon": mastodon, "youtube": youtube}[platform]
    if platform == "youtube":
        youtube._key()  # "not configured" before any network setup
    try:
        async with Session(progress=progress) as s:
            coll = await module.collect_thread(url, s)
            coll.requests = s.requests
    except RateLimited:
        raise CollectorError(f"{PLATFORM_NAMES[platform]} asked us to slow down. Please try again in a few minutes.",
                             429, "rate_limited") from None
    except BudgetExhausted:
        raise CollectorError("Collecting took too long. Please try again.", 504, "timeout") from None
    except HTTPStatus as exc:
        raise CollectorError(f"{PLATFORM_NAMES[platform]} answered with an error ({exc}).", 502) from None
    if not coll.posts:
        raise CollectorError("No public posts with text could be collected from that link.", 404, "empty_result")
    _note_no_text(coll)
    _store(key, coll)
    return coll, False


def validate_urls(urls: list[str]) -> tuple[dict[str, list[str]], list[dict]]:
    """Group links by platform; unusable ones become failed entries with a reason."""
    clean = list(dict.fromkeys(u.strip() for u in urls if u and u.strip()))
    if not clean:
        raise CollectorError("Please paste at least one link.", 400, "empty")
    if len(clean) > MAX_LINKS:
        raise CollectorError(f"Too many links: up to {MAX_LINKS} per run.", 400, "too_many")
    groups: dict[str, list[str]] = {}
    failed = []
    for u in clean:
        try:
            groups.setdefault(classify(u), []).append(u)
        except CollectorError as exc:
            failed.append({"url": u, "reason": exc.message})
    if not groups:
        raise CollectorError("None of the links can be read. " + failed[0]["reason"], 400, "unsupported")
    return groups, failed


async def collect_urls(urls: list[str], progress=None) -> tuple[Collected, bool]:
    """Single-post mode for several links (mixed platforms). Returns (collected, from_cache)."""
    groups, failed = validate_urls(urls)
    key = ("multi", tuple(sorted(u for us in groups.values() for u in us)))
    hit = _cached(key)
    if hit:
        return hit, True
    coll = Collected("mixed" if len(groups) > 1 else next(iter(groups)), f"{sum(map(len, groups.values()))} links")
    coll.failed = list(failed)
    async with Session(progress=progress) as s:
        for platform, us in groups.items():
            module = {"bluesky": bluesky, "mastodon": mastodon, "youtube": youtube}[platform]
            try:
                await module.collect_single(us, s, coll)
            except CollectorError as exc:
                coll.failed += [{"url": u, "reason": exc.message} for u in us]
            except (BudgetExhausted, RateLimited, HTTPStatus) as exc:  # keep what the other platforms gave
                coll.complete = False
                why = {"RateLimited": "the platform asked us to slow down", "HTTPStatus": f"the platform answered {exc}"}
                coll.notes.append(f"Some {PLATFORM_NAMES[platform]} links may be missing: "
                                  f"{why.get(type(exc).__name__, 'request or time limit for one run')}.")
            if progress:
                progress(f"Fetched {len(coll.posts)} posts from {len({p.author.lower() for p in coll.posts})} accounts…")
        coll.requests = s.requests
    _note_no_text(coll)
    if not coll.posts:
        reason = coll.failed[0]["reason"] if coll.failed else "nothing found"
        raise CollectorError(f"No public posts could be collected from those links ({reason}).", 404, "empty_result")
    _store(key, coll)
    return coll, False
