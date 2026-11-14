"""YouTube: YouTube Data API v3 with GOOGLE_API_KEY (server-side env; never logged or returned).

Thread mode: videos.list (the video itself) -> commentThreads.list (100 per page, newest first, with up
to 5 replies each) -> comments.list for threads with more replies, while the budget lasts ->
channels.list (50 per request) for channel creation dates. Each call costs 1 unit of the key's daily
quota (10,000 by default). Single-post mode (several links): watch links -> videos.list, links to a
comment (&lc=...) -> comments.list, 50 ids per request.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

from app import config
from app.swarm.collectors.base import Collected, make_post
from app.swarm.collectors.http import BudgetExhausted, CollectorError, HTTPStatus, RateLimited, Session

API = "https://www.googleapis.com/youtube/v3"
HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
MAX_REPLY_EXPANSIONS = 5
BATCH = 50


def parse(url: str) -> tuple[str, str | None] | None:
    """(video id, comment id or None)."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if host not in HOSTS:
        return None
    q = parse_qs(parts.query)
    vid = None
    if host == "youtu.be":
        vid = parts.path.strip("/").split("/")[0]
    elif parts.path == "/watch":
        vid = (q.get("v") or [None])[0]
    else:
        m = re.match(r"^/(?:shorts|live|embed)/([^/?#]+)", parts.path)
        vid = m.group(1) if m else None
    if not vid or not _ID.match(vid):
        return None
    return vid, (q.get("lc") or [None])[0]


def _key() -> str:
    key = config.env("GOOGLE_API_KEY")
    if not key:
        raise CollectorError("YouTube collection is not configured on this server (no Google API key). Use "
                             "'Upload a file' instead.", 503, "not_configured")
    return key


def _reason(exc: HTTPStatus) -> CollectorError:
    errors = ((exc.body or {}).get("error") or {}).get("errors") or [{}]
    reason = errors[0].get("reason", "")
    if reason == "commentsDisabled":
        return CollectorError("Comments are turned off for this video.", 404, "comments_disabled")
    if reason in ("videoNotFound", "notFound") or exc.status == 404:
        return CollectorError("That YouTube video was not found (private, deleted, or the link is wrong).", 404, "not_found")
    if reason in ("quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"):
        return CollectorError("YouTube's daily quota for this server is used up. Please try again tomorrow or use "
                              "'Upload a file'.", 503, "quota")
    if reason in ("keyInvalid", "accessNotConfigured", "forbidden") or exc.status in (400, 403):
        return CollectorError("YouTube collection is not configured correctly on this server.", 503, "not_configured")
    return CollectorError(f"YouTube answered with an error (HTTP {exc.status}).", 502)


def _comment_post(c: dict, channels: dict[str, str]):
    sn = c.get("snippet") or {}
    cid = (sn.get("authorChannelId") or {}).get("value", "")
    author = (sn.get("authorDisplayName") or cid).lstrip("@")
    return make_post(f"yt:{c['id']}", author, sn.get("publishedAt"), sn.get("textOriginal") or sn.get("textDisplay") or "",
                     author_created=channels.get(cid), platform="youtube")


async def _channels(s: Session, ids: set[str], coll: Collected) -> dict[str, str]:
    out: dict[str, str] = {}
    ids = sorted(i for i in ids if i)
    for i in range(0, len(ids), BATCH):
        if not s.can_request():
            coll.notes.append("Some channel creation dates were not looked up (request limit).")
            break
        data = await s.get_json(f"{API}/channels", params={"part": "snippet", "id": ",".join(ids[i:i + BATCH]),
                                                           "maxResults": BATCH, "key": _key()})
        for ch in (data or {}).get("items", []):
            out[ch["id"]] = (ch.get("snippet") or {}).get("publishedAt")
    return out


async def collect_thread(url: str, s: Session) -> Collected:
    vid, _ = parse(url)
    key = _key()
    coll = Collected("youtube", url)
    try:
        video = await s.get_json(f"{API}/videos", params={"part": "snippet,statistics", "id": vid, "key": key})
    except HTTPStatus as exc:
        raise _reason(exc) from None
    items = (video or {}).get("items") or []
    if not items:
        raise CollectorError("That YouTube video was not found (private, deleted, or the link is wrong).", 404, "not_found")
    sn, stats = items[0].get("snippet") or {}, items[0].get("statistics") or {}
    coll.root = {"author": sn.get("channelTitle"), "text": sn.get("title", ""), "url": f"https://www.youtube.com/watch?v={vid}",
                 "created": sn.get("publishedAt"), "replies": int(stats["commentCount"]) if stats.get("commentCount") else None}
    raw: list[dict] = []
    expand: list[str] = []
    try:
        token = None
        while s.can_request() and len(raw) < 2000:
            params = {"part": "snippet,replies", "videoId": vid, "maxResults": 100, "order": "time",
                      "textFormat": "plainText", "key": key, **({"pageToken": token} if token else {})}
            try:
                data = await s.get_json(f"{API}/commentThreads", params=params)
            except HTTPStatus as exc:
                raise _reason(exc) from None
            for th in (data or {}).get("items", []):
                top = (th.get("snippet") or {}).get("topLevelComment")
                if top:
                    raw.append(top)
                replies = (th.get("replies") or {}).get("comments") or []
                raw.extend(replies)
                if (th.get("snippet") or {}).get("totalReplyCount", 0) > len(replies):
                    expand.append(th["id"])
            s.progress(f"Fetched {len(raw)} comments from {len({(c.get('snippet') or {}).get('authorChannelId', {}).get('value') for c in raw})} channels…")
            token = (data or {}).get("nextPageToken")
            if not token:
                break
        if token:
            coll.complete = False
            coll.notes.append("Not all comments were fetched (post, request or time limit for one run).")
        # keep requests for creation dates: expand only a few long reply threads
        for parent in expand[:MAX_REPLY_EXPANSIONS]:
            if s.requests >= s.max_requests - 10 or not s.can_request():
                break
            data = await s.get_json(f"{API}/comments", params={"part": "snippet", "parentId": parent, "maxResults": 100,
                                                               "textFormat": "plainText", "key": key})
            raw.extend((data or {}).get("items", []))
        if len(expand) > MAX_REPLY_EXPANSIONS:
            coll.notes.append(f"Replies were fully expanded for {MAX_REPLY_EXPANSIONS} of {len(expand)} long reply threads; "
                              "the others show their first 5 replies.")
        channels = await _channels(s, {(c.get("snippet") or {}).get("authorChannelId", {}).get("value") for c in raw}, coll)
    except (BudgetExhausted, RateLimited, HTTPStatus) as exc:
        if isinstance(exc, HTTPStatus) and not raw:
            raise _reason(exc) from None
        coll.complete = False
        channels = {}
        coll.notes.append("YouTube asked us to slow down; showing what was collected so far." if isinstance(exc, RateLimited)
                          else f"Stopped at the {exc} for one run; showing what was collected so far.")
    for c in raw:
        p = _comment_post(c, channels)
        if p and not coll.add(p):
            break
    return coll


async def collect_single(urls: list[str], s: Session, coll: Collected) -> None:
    key = _key()
    videos, comments = {}, {}
    for u in urls:
        vid, lc = parse(u)
        (comments if lc else videos)[lc or vid] = u
    found: list[tuple[str, dict]] = []
    try:
        for kind, ids in (("videos", list(videos)), ("comments", list(comments))):
            for i in range(0, len(ids), BATCH):
                params = {"part": "snippet", "id": ",".join(ids[i:i + BATCH]), "key": key}
                if kind == "comments":
                    params["textFormat"] = "plainText"
                data = await s.get_json(f"{API}/{kind}", params=params)
                found += [(kind, it) for it in (data or {}).get("items", [])]
    except HTTPStatus as exc:
        raise _reason(exc) from None
    got = {it["id"] for _, it in found}
    for ident, u in {**videos, **comments}.items():
        if ident not in got:
            coll.failed.append({"url": u, "reason": "YouTube video or comment not found"})
    chan_ids = {(it.get("snippet") or {}).get("channelId") if kind == "videos"
                else ((it.get("snippet") or {}).get("authorChannelId") or {}).get("value") for kind, it in found}
    channels = await _channels(s, chan_ids, coll)
    for kind, it in found:
        sn = it.get("snippet") or {}
        if kind == "comments":
            p = _comment_post(it, channels)
        else:
            text = f"{sn.get('title', '')}\n{(sn.get('description') or '')[:1000]}".strip()
            p = make_post(f"yt:{it['id']}", sn.get("channelTitle") or sn.get("channelId", ""), sn.get("publishedAt"), text,
                          url=f"https://www.youtube.com/watch?v={it['id']}", author_created=channels.get(sn.get("channelId")),
                          platform="youtube")
        if p:
            coll.add(p)
