"""Hacker News public threads via Algolia's official HN API (`https://hn.algolia.com/api/v1/items/<id>`)."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

from app.swarm.collectors.base import Collected, html_to_text, make_post
from app.swarm.collectors.http import CollectorError, HTTPStatus, Session

API = "https://hn.algolia.com/api/v1"
_ID_RE = re.compile(r"^\d{1,12}$")


def parse(url: str) -> str | None:
    try:
        parts = urlsplit((url or "").strip())
    except ValueError:
        return None
    if (parts.hostname or "").lower() != "news.ycombinator.com" or parts.path != "/item":
        return None
    item_id = (parse_qs(parts.query).get("id") or [None])[0]
    return item_id if item_id and _ID_RE.match(item_id) else None


def _iso(epoch) -> str | None:
    if isinstance(epoch, (int, float)):
        return datetime.fromtimestamp(epoch, timezone.utc).isoformat()
    return None


def _walk_children(node: dict, coll: Collected) -> None:
    for ch in node.get("children") or []:
        if not isinstance(ch, dict):
            continue
        author = ch.get("author") or ""
        text = html_to_text(ch.get("text") or "")
        created = ch.get("created_at") or _iso(ch.get("created_at_i"))
        cid = ch.get("id")
        if author and text and cid:
            p = make_post(f"hn:{cid}", author, created, text,
                          url=f"https://news.ycombinator.com/item?id={cid}", platform="hackernews")
            if p and not coll.add(p):
                return
        _walk_children(ch, coll)


async def collect_thread(url: str, s: Session) -> Collected:
    item_id = parse(url)
    if not item_id:
        raise CollectorError("That Hacker News link does not point to an item (`news.ycombinator.com/item?id=…`).", 400)
    coll = Collected("hackernews", url)
    try:
        data = await s.get_json(f"{API}/items/{item_id}")
    except HTTPStatus as exc:
        if exc.status == 404:
            raise CollectorError("That Hacker News item was not found.", 404, "not_found") from None
        raise CollectorError(f"Hacker News API answered with HTTP {exc.status}.", 502) from None
    if not isinstance(data, dict) or not data.get("id"):
        raise CollectorError("That Hacker News item was not found.", 404, "not_found")
    title = (data.get("title") or "").strip()
    body = html_to_text(data.get("text") or "")
    root_text = f"{title}\n{body}".strip()
    created = data.get("created_at") or _iso(data.get("created_at_i"))
    author = data.get("author") or "hn"
    coll.root = {"author": author, "text": root_text[:500], "url": data.get("url") or url, "created": created}
    p = make_post(f"hn:{data['id']}", author, created, root_text, url=data.get("url") or url, platform="hackernews")
    if p:
        coll.add(p)
    _walk_children(data, coll)
    s.progress(f"Fetched {len(coll.posts)} Hacker News comments from {len({x.author.lower() for x in coll.posts})} accounts…")
    return coll


async def collect_single(urls: list[str], s: Session, coll: Collected) -> None:
    for u in urls:
        if not s.can_request():
            coll.failed.append({"url": u, "reason": "request or time limit reached"})
            continue
        item_id = parse(u)
        if not item_id:
            coll.failed.append({"url": u, "reason": "invalid Hacker News link"})
            continue
        try:
            data = await s.get_json(f"{API}/items/{item_id}")
            if isinstance(data, dict) and data.get("author"):
                txt = f"{data.get('title') or ''}\n{html_to_text(data.get('text') or '')}".strip()
                p = make_post(f"hn:{data['id']}", data["author"], data.get("created_at") or _iso(data.get("created_at_i")),
                              txt, url=data.get("url") or u, platform="hackernews")
                if p:
                    coll.add(p)
        except HTTPStatus as exc:
            coll.failed.append({"url": u, "reason": f"HTTP {exc.status}"})
