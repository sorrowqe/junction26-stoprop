"""Reddit public threads & subreddits via Reddit's public Atom feeds (`.rss`), XXE-safe.

Supports:
- Thread URL (`reddit.com/r/<sub>/comments/<id>/...` or `redd.it/<id>`): fetches post + up to 100 comments.
- Subreddit URL (`reddit.com/r/<sub>`): fetches up to 100 newest posts across authors.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

from app.swarm.collectors.base import Collected, html_to_text, make_post
from app.swarm.collectors.http import BudgetExhausted, CollectorError, HTTPStatus, RateLimited, Session

HOSTS = {"reddit.com", "www.reddit.com", "old.reddit.com", "new.reddit.com", "np.reddit.com", "redd.it"}
_THREAD_RE = re.compile(r"^/(?:r/[A-Za-z0-9_]+/)?comments/([a-z0-9]{5,10})(?:/|$)", re.IGNORECASE)
_SUB_RE = re.compile(r"^/r/([A-Za-z0-9_]{2,50})(?:/(?:new|hot|rising|top))?/?$", re.IGNORECASE)
_SHORT_RE = re.compile(r"^/([a-z0-9]{5,10})/?$", re.IGNORECASE)
_EXT_HREF = re.compile(r'<a\s[^>]*href="(https?://[^"]+)"', re.IGNORECASE)
_ATOM_NS = "{http://www.w3.org/2005/Atom}"
_IGNORE_AUTHORS = {"[deleted]", "[removed]", "automoderator"}


def parse(url: str) -> tuple[str, str] | None:
    try:
        parts = urlsplit((url or "").strip())
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if host not in HOSTS or parts.scheme not in ("http", "https"):
        return None
    if host == "redd.it":
        m = _SHORT_RE.match(parts.path)
        return ("thread", m.group(1).lower()) if m else None
    if m := _THREAD_RE.match(parts.path):
        return ("thread", m.group(1).lower())
    if m := _SUB_RE.match(parts.path):
        return ("subreddit", m.group(1))
    return None


def _external_link(raw_html: str) -> str | None:
    for m in _EXT_HREF.finditer(raw_html or ""):
        u = m.group(1)
        if "reddit.com" not in u.lower() and "redd.it" not in u.lower():
            return u
    return None


def parse_atom_feed(xml_text: str) -> tuple[str | None, list[dict]]:
    if "<!DOCTYPE" in xml_text.upper() or "<!ENTITY" in xml_text.upper():
        raise CollectorError("Unexpected XML declaration from Reddit.", 502, "bad_xml")
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        raise CollectorError("Reddit sent a feed we could not parse.", 502, "bad_xml") from None
    feed_title = (root.findtext(f"{_ATOM_NS}title") or "").strip() or None
    entries: list[dict] = []
    for entry in root.findall(f"{_ATOM_NS}entry"):
        eid = (entry.findtext(f"{_ATOM_NS}id") or "").strip()
        author_el = entry.find(f"{_ATOM_NS}author/{_ATOM_NS}name")
        author = (author_el.text or "").strip().lstrip("/") if author_el is not None and author_el.text else ""
        if author.startswith("u/"):
            author = author[2:]
        updated = (entry.findtext(f"{_ATOM_NS}updated") or entry.findtext(f"{_ATOM_NS}published") or "").strip()
        title = (entry.findtext(f"{_ATOM_NS}title") or "").strip()
        content_raw = entry.findtext(f"{_ATOM_NS}content") or ""
        link_el = entry.find(f"{_ATOM_NS}link")
        permalink = link_el.attrib.get("href") if link_el is not None else None
        body_text = re.sub(
            r"\s*submitted by\s+/u/\S+\s*(?:\[link\])?\s*(?:\[comments\])?\s*$",
            "", html_to_text(content_raw), flags=re.I,
        ).strip()
        entries.append({
            "id": eid or permalink or title, "author": author, "created": updated,
            "title": title, "body": body_text, "ext_url": _external_link(content_raw), "permalink": permalink,
        })
    return feed_title, entries


async def collect_thread(url: str, s: Session) -> Collected:
    parsed = parse(url)
    if not parsed:
        raise CollectorError("Supported Reddit links: a post (reddit.com/r/…/comments/…) or a subreddit (reddit.com/r/…).", 400)
    kind, ident = parsed
    coll = Collected("reddit", url)
    feed_url = (f"https://www.reddit.com/comments/{ident}.rss" if kind == "thread"
                else f"https://www.reddit.com/r/{ident}/new.rss")
    try:
        xml_text = await s.get_text(feed_url, params={"limit": "100"})
    except RateLimited:
        raise CollectorError("Reddit asked us to slow down; please try again in a minute.", 429, "rate_limited") from None
    except HTTPStatus as exc:
        if exc.status == 404:
            raise CollectorError("That Reddit thread or subreddit was not found (private, banned, or wrong link).", 404, "not_found") from None
        if exc.status == 403:
            raise CollectorError("Reddit blocked automated access to that page right now.", 502, "blocked") from None
        raise CollectorError(f"Reddit answered with HTTP {exc.status}.", 502) from None
    except BudgetExhausted:
        raise CollectorError("Collecting from Reddit took too long.", 504, "timeout") from None

    feed_title, entries = parse_atom_feed(xml_text)
    for idx, e in enumerate(entries):
        if kind == "thread" and idx == 0:
            full_root = f"{e['title']}\n{e['body']}".strip() if e["body"] else e["title"]
            coll.root = {"author": f"u/{e['author']}" if e["author"] else "reddit", "text": full_root[:500],
                         "url": e["permalink"] or url, "created": e["created"]}
        if not e["author"] or e["author"].lower() in _IGNORE_AUTHORS:
            continue
        text = e["body"] if kind == "thread" and idx > 0 else f"{e['title']}\n{e['body']}".strip()
        p = make_post(f"rd:{e['id']}", f"u/{e['author']}", e["created"], text,
                      url=e["ext_url"] or e["permalink"], platform="reddit")
        if p and not coll.add(p):
            break

    if kind == "subreddit" and coll.root is None:
        coll.root = {"author": f"r/{ident}", "text": feed_title or f"Newest posts in r/{ident}", "url": f"https://www.reddit.com/r/{ident}/"}
    if len(entries) >= 95:
        coll.notes.append("Reddit's public feed shares up to ~100 posts/comments per request.")
    s.progress(f"Fetched {len(coll.posts)} Reddit items from {len({p.author.lower() for p in coll.posts})} accounts…")
    return coll


async def collect_single(urls: list[str], s: Session, coll: Collected) -> None:
    for u in urls:
        if not s.can_request():
            coll.failed.append({"url": u, "reason": "request or time limit reached"})
            continue
        parsed = parse(u)
        if not parsed:
            coll.failed.append({"url": u, "reason": "unsupported Reddit link"})
            continue
        kind, ident = parsed
        try:
            if kind == "subreddit":
                sub_coll = await collect_thread(u, s)
                for p in sub_coll.posts:
                    coll.add(p)
            else:
                xml_text = await s.get_text(f"https://www.reddit.com/comments/{ident}.rss", params={"limit": "5"})
                _, entries = parse_atom_feed(xml_text)
                if entries and entries[0]["author"] and entries[0]["author"].lower() not in _IGNORE_AUTHORS:
                    e = entries[0]
                    p = make_post(f"rd:{e['id']}", f"u/{e['author']}", e["created"],
                                  f"{e['title']}\n{e['body']}".strip(), url=e["ext_url"] or e["permalink"], platform="reddit")
                    if p:
                        coll.add(p)
                else:
                    coll.failed.append({"url": u, "reason": "Reddit post not found or author deleted"})
        except CollectorError as exc:
            coll.failed.append({"url": u, "reason": exc.message})
