"""Mastodon (and compatible servers): the public REST API of the server in the link.

Thread mode: GET /api/v1/statuses/{id} + /api/v1/statuses/{id}/context (descendants). Many servers
share at most 60 replies with visitors who are not logged in; we say so when that cap is hit.
Account creation dates come inline with every status (remote accounts: date only).
The server host is user-supplied, so every request goes through the SSRF-safe client (http.Session).
"""
from __future__ import annotations

import re

from app.swarm.collectors.base import Collected, html_to_text, make_post
from app.swarm.collectors.http import BudgetExhausted, CollectorError, HTTPStatus, RateLimited, Session

URL_RE = re.compile(r"^https://([a-z0-9.-]+\.[a-z]{2,})(?::443)?/(?:(?:deck/)?@[^/?#]+|users/[^/?#]+/statuses|web/statuses)"
                    r"/(\d{1,20})/?(?:[?#].*)?$", re.I)
UNAUTH_CONTEXT_CAP = 60
_HREF = re.compile(r'<a\s[^>]*href="([^"]+)"[^>]*>', re.I)


def parse(url: str) -> tuple[str, str] | None:
    m = URL_RE.match(url.strip())
    return (m.group(1).lower(), m.group(2)) if m else None


def _link(status: dict) -> str | None:
    card = status.get("card") or {}
    if card.get("url"):
        return card["url"]
    for tag in _HREF.finditer(status.get("content") or ""):
        anchor = tag.group(0)
        if "mention" not in anchor and "hashtag" not in anchor:
            return tag.group(1)
    return None


def _post(status: dict, instance: str):
    acc = status.get("account") or {}
    acct = acc.get("acct") or acc.get("username") or ""
    if acct and "@" not in acct:
        acct = f"{acct}@{instance}"
    text = html_to_text(status.get("content") or "")
    if status.get("spoiler_text"):
        text = f"{status['spoiler_text']}\n{text}"
    return make_post(status.get("url") or status.get("uri") or status["id"], acct, status.get("created_at"), text,
                     url=_link(status), author_created=acc.get("created_at"), platform="mastodon")


async def _status(s: Session, instance: str, sid: str) -> dict:
    try:
        return await s.get_json(f"https://{instance}/api/v1/statuses/{sid}")
    except HTTPStatus as exc:
        if exc.status == 404:
            raise CollectorError("That Mastodon post was not found on this server (deleted, private, or the link is "
                                 "wrong).", 404, "not_found") from None
        if exc.status in (401, 403):
            raise CollectorError("This Mastodon server does not share posts with visitors who are not logged in, so "
                                 "Stoprop cannot read it.", 403, "no_public_access") from None
        raise CollectorError(f"The Mastodon server answered with an error (HTTP {exc.status}).", 502) from None


async def collect_thread(url: str, s: Session) -> Collected:
    instance, sid = parse(url)
    coll = Collected("mastodon", url)
    root = await _status(s, instance, sid)
    if not isinstance(root, dict) or "id" not in root:
        raise CollectorError("That link does not look like a Mastodon post.", 400, "not_mastodon")
    coll.root = {"author": (root.get("account") or {}).get("acct"), "text": html_to_text(root.get("content") or ""),
                 "url": root.get("url"), "created": root.get("created_at"), "replies": root.get("replies_count")}
    post = _post(root, instance)
    if post:
        coll.add(post)
    try:
        ctx = await s.get_json(f"https://{instance}/api/v1/statuses/{sid}/context")
        desc = (ctx or {}).get("descendants") or []
        for st in desc:
            p = _post(st, instance)
            if p and not coll.add(p):
                break
        s.progress(f"Fetched {len(desc)} replies from {len({p.author.lower() for p in coll.posts})} accounts…")
        if len(desc) == UNAUTH_CONTEXT_CAP and (root.get("replies_count") or 0) > UNAUTH_CONTEXT_CAP:
            coll.complete = False
            coll.notes.append(f"This Mastodon server shares at most {UNAUTH_CONTEXT_CAP} replies with visitors who are "
                              f"not logged in; the post has about {root['replies_count']} direct replies.")
    except HTTPStatus as exc:
        coll.complete = False
        coll.notes.append(f"The replies could not be read (HTTP {exc.status}); only the post itself is shown.")
    except (BudgetExhausted, RateLimited):
        coll.complete = False
        coll.notes.append("The server asked us to slow down; showing what was collected so far.")
    return coll


async def collect_single(urls: list[str], s: Session, coll: Collected) -> None:
    for u in urls:
        if not s.can_request():
            coll.failed.append({"url": u, "reason": "not fetched (request or time limit for one run)"})
            continue
        instance, sid = parse(u)
        try:
            st = await _status(s, instance, sid)
        except CollectorError as exc:
            coll.failed.append({"url": u, "reason": exc.message})
            continue
        except RateLimited:
            coll.failed.append({"url": u, "reason": "the server asked us to slow down"})
            continue
        p = _post(st, instance) if isinstance(st, dict) and "id" in st else None
        if p:
            coll.add(p)
        else:
            coll.failed.append({"url": u, "reason": "not a Mastodon post"})
