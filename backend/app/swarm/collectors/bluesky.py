"""Bluesky: public AppView API (https://public.api.bsky.app, no login).

Thread mode: getPostThread (depth 1000) for the linked post and its full reply tree; sub-threads the
AppView cut short are re-fetched while the request budget lasts; quote posts via getQuotes (up to 3
pages); account creation dates come with the authors, missing ones via getProfiles (25 per request).
Single-post mode (several links): getProfiles resolves handles to DIDs, getPosts fetches 25 per request.
Timestamps are the post's own createdAt (set by the author's app).
"""
from __future__ import annotations

import re

from app.swarm.collectors.base import Collected, make_post
from app.swarm.collectors.http import BudgetExhausted, CollectorError, HTTPStatus, RateLimited, Session

API = "https://public.api.bsky.app/xrpc"
URL_RE = re.compile(r"^https?://(?:www\.)?bsky\.app/profile/([^/?#]+)/post/([a-z0-9]+)/?(?:[?#].*)?$", re.I)
QUOTE_PAGES = 3
BATCH = 25


def parse(url: str) -> tuple[str, str] | None:
    m = URL_RE.match(url.strip())
    return (m.group(1), m.group(2)) if m else None


def web_url(post: dict) -> str:
    rkey = post["uri"].rsplit("/", 1)[-1]
    return f"https://bsky.app/profile/{post['author'].get('handle') or post['author']['did']}/post/{rkey}"


def _link(record: dict) -> str | None:
    embed = record.get("embed") or {}
    ext = (embed.get("external") or (embed.get("media") or {}).get("external") or {})
    if ext.get("uri"):
        return ext["uri"]
    for facet in record.get("facets") or []:
        for feat in facet.get("features") or []:
            if feat.get("$type", "").endswith("#link") and feat.get("uri"):
                return feat["uri"]
    return None


def _post(p: dict):
    rec = p.get("record") or {}
    author = p.get("author") or {}
    return make_post(p["uri"], author.get("handle") or author.get("did", ""), rec.get("createdAt") or p.get("indexedAt"),
                     rec.get("text", ""), url=_link(rec), author_created=author.get("createdAt"), platform="bluesky")


def _walk(node: dict, out: list[dict], truncated: list[str]) -> None:
    if not isinstance(node, dict) or "post" not in node:
        return  # notFound / blocked nodes
    out.append(node["post"])
    replies = node.get("replies")
    if replies is None and (node["post"].get("replyCount") or 0) > 0:
        truncated.append(node["post"]["uri"])
    for r in replies or []:
        _walk(r, out, truncated)


async def _fill_profiles(s: Session, coll: Collected, raw: list[dict]) -> None:
    missing = sorted({p["author"]["did"] for p in raw if not p["author"].get("createdAt")})
    created: dict[str, str] = {}
    for i in range(0, len(missing), BATCH):
        if not s.can_request():
            coll.notes.append("Some account creation dates were not looked up (request limit).")
            break
        data = await s.get_json(f"{API}/app.bsky.actor.getProfiles", params=[("actors", d) for d in missing[i:i + BATCH]])
        for prof in (data or {}).get("profiles", []):
            if prof.get("createdAt"):
                created[prof["did"]] = prof["createdAt"]
    for p in raw:
        if not p["author"].get("createdAt") and p["author"]["did"] in created:
            p["author"]["createdAt"] = created[p["author"]["did"]]


async def collect_thread(url: str, s: Session) -> Collected:
    actor, rkey = parse(url)
    coll = Collected("bluesky", url)
    uri = f"at://{actor}/app.bsky.feed.post/{rkey}"
    try:
        data = await s.get_json(f"{API}/app.bsky.feed.getPostThread", params={"uri": uri, "depth": 1000, "parentHeight": 0})
    except HTTPStatus as exc:
        if exc.status in (400, 404):
            raise CollectorError("That Bluesky post was not found (deleted, or the link is wrong).", 404, "not_found") from None
        raise CollectorError(f"Bluesky answered with an error (HTTP {exc.status}).", 502) from None
    thread = (data or {}).get("thread") or {}
    if "post" not in thread:
        raise CollectorError("That Bluesky post is not publicly visible (blocked, deleted or login required).", 404, "not_found")
    raw: list[dict] = []
    truncated: list[str] = []
    _walk(thread, raw, truncated)
    root = thread["post"]
    coll.root = {"author": root["author"].get("handle"), "text": (root.get("record") or {}).get("text", ""),
                 "url": web_url(root), "created": (root.get("record") or {}).get("createdAt"),
                 "replies": root.get("replyCount"), "quotes": root.get("quoteCount")}
    s.progress(f"Fetched {len(raw) - 1} replies from {len({p['author']['did'] for p in raw})} accounts…")
    try:
        while truncated and s.can_request() and len(raw) < 2000:
            sub = truncated.pop(0)
            data = await s.get_json(f"{API}/app.bsky.feed.getPostThread", params={"uri": sub, "depth": 1000, "parentHeight": 0})
            node = (data or {}).get("thread") or {}
            more: list[dict] = []
            _walk(node, more, truncated)
            raw.extend(more[1:])  # the sub-root is already collected
            s.progress(f"Fetched {len(raw) - 1} replies from {len({p['author']['did'] for p in raw})} accounts…")
        if truncated:
            coll.complete = False
            coll.notes.append(f"{len(truncated)} deeper reply branches were not fetched (request or time limit).")
        cursor, pages = None, 0
        while (root.get("quoteCount") or 0) > 0 and pages < QUOTE_PAGES and s.can_request():
            params = {"uri": root["uri"], "limit": 100, **({"cursor": cursor} if cursor else {})}
            data = await s.get_json(f"{API}/app.bsky.feed.getQuotes", params=params)
            quotes = (data or {}).get("posts", [])
            raw.extend(quotes)
            pages += 1
            cursor = (data or {}).get("cursor")
            s.progress(f"Fetched {len(raw) - 1} replies and quote posts from {len({p['author']['did'] for p in raw})} accounts…")
            if not cursor or not quotes:
                break
        if cursor and pages >= QUOTE_PAGES:
            coll.complete = False
            coll.notes.append(f"Only the first {QUOTE_PAGES * 100} quote posts were fetched.")
        await _fill_profiles(s, coll, raw)
    except (BudgetExhausted, RateLimited, HTTPStatus) as exc:
        coll.complete = False
        coll.notes.append("Bluesky asked us to slow down; showing what was collected so far." if isinstance(exc, RateLimited)
                          else f"Bluesky answered {exc} part-way; showing what was collected so far."
                          if isinstance(exc, HTTPStatus)
                          else f"Stopped at the {exc} for one run; showing what was collected so far.")
    for p in raw:
        post = _post(p)
        if post and not coll.add(post):
            break
    return coll


async def collect_single(urls: list[str], s: Session, coll: Collected) -> None:
    """Several links, one post each (no thread expansion)."""
    parsed = [(u, *parse(u)) for u in urls]
    handles = sorted({a for _, a, _ in parsed if not a.startswith("did:")})
    did_of: dict[str, str] = {}
    for i in range(0, len(handles), BATCH):
        data = await s.get_json(f"{API}/app.bsky.actor.getProfiles", params=[("actors", h) for h in handles[i:i + BATCH]])
        for prof in (data or {}).get("profiles", []):
            did_of[prof["handle"].lower()] = prof["did"]
    uris, by_uri = [], {}
    for u, actor, rkey in parsed:
        did = actor if actor.startswith("did:") else did_of.get(actor.lower())
        if not did:
            coll.failed.append({"url": u, "reason": "Bluesky account not found"})
            continue
        uri = f"at://{did}/app.bsky.feed.post/{rkey}"
        uris.append(uri)
        by_uri[uri] = u
    found = set()
    for i in range(0, len(uris), BATCH):
        data = await s.get_json(f"{API}/app.bsky.feed.getPosts", params=[("uris", x) for x in uris[i:i + BATCH]])
        raw = (data or {}).get("posts", [])
        await _fill_profiles(s, coll, raw)
        for p in raw:
            found.add(p["uri"])
            post = _post(p)
            if post:
                coll.add(post)
    for uri in uris:
        if uri not in found:
            coll.failed.append({"url": by_uri[uri], "reason": "Bluesky post not found or not public"})
