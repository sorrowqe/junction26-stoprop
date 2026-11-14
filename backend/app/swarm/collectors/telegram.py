"""Telegram public channels & posts via Telegram's public web preview (`https://t.me/s/<channel>`).

No login, no API key. Supports single channel (`t.me/<channel>`), single post (`t.me/<channel>/<id>`),
and multi-channel batches (`t.me/chan1`, `t.me/chan2`, ...) to detect cross-channel Telegram campaigns.
"""
from __future__ import annotations

import re

from app.swarm.collectors.base import Collected, html_to_text, make_post
from app.swarm.collectors.http import BudgetExhausted, CollectorError, HTTPStatus, RateLimited, Session

HOSTS = {"t.me", "telegram.me", "telegram.dog"}
_RESERVED = {"joinchat", "addstickers", "addemoji", "iv", "proxy", "socks", "share", "login", "c"}
_URL_RE = re.compile(
    r"^https?://(?:www\.)?(?:t\.me|telegram\.(?:me|dog))/(?:s/)?([A-Za-z0-9_]{4,64})(?:/(\d+))?/?(?:[?#].*)?$",
    re.IGNORECASE,
)
_BLOCK_SPLIT = re.compile(r'<div class="tgme_widget_message\b[^"]*"[^>]*\bdata-post="([^"]+)"')
_TEXT_RE = re.compile(r'<div class="tgme_widget_message_text\b[^"]*"[^>]*>([\s\S]*?)</div>')
_TIME_RE = re.compile(r'<time[^>]+datetime="([^"]+)"')
_FWD_RE = re.compile(r'class="tgme_widget_message_forwarded_from_name"[^>]*href="https://t\.me/([^"/?#]+)"')
_EXT_HREF = re.compile(r'<a\s[^>]*href="(https?://[^"]+)"', re.IGNORECASE)
MAX_PAGES = 4


def parse(url: str) -> tuple[str, int | None] | None:
    m = _URL_RE.match((url or "").strip())
    if not m or m.group(1).lower() in _RESERVED:
        return None
    return m.group(1), (int(m.group(2)) if m.group(2) else None)


def _ext_link(html_chunk: str) -> str | None:
    for m in _EXT_HREF.finditer(html_chunk):
        u = m.group(1)
        if not any(h in u.lower() for h in ("//t.me/", "//telegram.me/", "//telesco.pe/")):
            return u
    return None


def parse_preview_html(page_html: str, default_channel: str) -> list[dict]:
    parts = _BLOCK_SPLIT.split(page_html or "")
    items: list[dict] = []
    for i in range(1, len(parts) - 1, 2):
        data_post, chunk = parts[i], parts[i + 1]
        chan, _, num_s = data_post.partition("/")
        chan = chan or default_channel
        tm, tx, fwd = _TIME_RE.search(chunk), _TEXT_RE.search(chunk), _FWD_RE.search(chunk)
        text = html_to_text(tx.group(1)) if tx else ""
        items.append({
            "id": f"tg:{data_post}",
            "num": int(num_s) if num_s.isdigit() else None,
            "channel": chan,
            "forwarded_from": fwd.group(1) if fwd else None,
            "created": tm.group(1) if tm else None,
            "text": text,
            "link": _ext_link(tx.group(1) if tx else "") or f"https://t.me/{data_post}",
            "post_url": f"https://t.me/{data_post}",
        })
    return items


async def _fetch_page(s: Session, channel: str, before: int | None = None) -> list[dict]:
    html_doc = await s.get_text(
        f"https://t.me/s/{channel}",
        params={"before": str(before)} if before else None,
        headers={"User-Agent": "Mozilla/5.0 (compatible; StopropBot/0.3; +https://stoprop.tech/methodology)"},
    )
    return parse_preview_html(html_doc, channel)


async def collect_thread(url: str, s: Session) -> Collected:
    parsed = parse(url)
    if not parsed:
        raise CollectorError("That Telegram link is not a public channel or post (e.g. https://t.me/channel).", 400)
    channel, target_id = parsed
    coll = Collected("telegram", url)
    before = (target_id + 10) if target_id else None
    seen_nums: list[int] = []
    try:
        for page in range(MAX_PAGES):
            if not s.can_request():
                break
            items = await _fetch_page(s, channel, before=before)
            if not items:
                if page == 0:
                    raise CollectorError(
                        f"Telegram channel @{channel} has no public web preview (private group, invite link, or does not exist).",
                        404, "not_found",
                    )
                break
            for it in items:
                author = f"@{it['channel']}"
                p = make_post(it["id"], author, it["created"], it["text"], url=it["link"], platform="telegram")
                if target_id and it["num"] == target_id and coll.root is None:
                    coll.root = {"author": author, "text": it["text"], "url": it["post_url"], "created": it["created"]}
                if p and not coll.add(p):
                    break
                if it["num"]:
                    seen_nums.append(it["num"])
            s.progress(f"Fetched {len(coll.posts)} Telegram posts from @{channel}…")
            oldest = min(seen_nums) if seen_nums else None
            if not oldest or oldest <= 1 or len(items) < 5:
                break
            before = oldest
    except (BudgetExhausted, RateLimited, HTTPStatus) as exc:
        if isinstance(exc, HTTPStatus) and not coll.posts:
            raise CollectorError(f"Telegram answered with HTTP {exc.status}.", 502) from None
        coll.complete = False
        coll.notes.append("Telegram pagination stopped early; showing posts collected so far.")

    if coll.root is None and coll.posts:
        newest = coll.posts[-1]
        coll.root = {"author": f"@{channel}", "text": newest.text[:400], "url": f"https://t.me/{channel}", "created": None}
    if len({p.author.lower() for p in coll.posts}) == 1 and coll.posts:
        coll.notes.append(
            f"All {len(coll.posts)} posts come from a single broadcast channel (@{channel}). "
            "To detect cross-channel Telegram coordination, paste 2 or more Telegram channel links (one per line), "
            "or paste a specific post's text to trace where it spread across the web."
        )
    return coll


async def collect_single(urls: list[str], s: Session, coll: Collected) -> None:
    by_channel: dict[str, list[tuple[str, int | None]]] = {}
    for u in urls:
        p = parse(u)
        if p:
            by_channel.setdefault(p[0].lower(), []).append((u, p[1]))
    for chan, reqs in by_channel.items():
        if not s.can_request():
            coll.failed.extend({"url": u, "reason": "request or time limit reached"} for u, _ in reqs)
            continue
        wanted_ids = {mid for _, mid in reqs if mid is not None}
        want_whole_channel = any(mid is None for _, mid in reqs)
        before = (max(wanted_ids) + 5) if (wanted_ids and not want_whole_channel) else None
        try:
            items = await _fetch_page(s, chan, before=before)
        except (CollectorError, HTTPStatus, RateLimited) as exc:
            reason = exc.message if isinstance(exc, CollectorError) else "Telegram did not answer"
            coll.failed.extend({"url": u, "reason": reason} for u, _ in reqs)
            continue
        if not items:
            coll.failed.extend({"url": u, "reason": f"@{chan} has no public web preview"} for u, _ in reqs)
            continue
        matched_ids = set()
        for it in items:
            if want_whole_channel or (it["num"] in wanted_ids):
                matched_ids.add(it["num"])
                post = make_post(it["id"], f"@{it['channel']}", it["created"], it["text"], url=it["link"], platform="telegram")
                if post:
                    coll.add(post)
        for u, mid in reqs:
            if mid is not None and mid not in matched_ids:
                coll.failed.append({"url": u, "reason": f"Telegram message {chan}/{mid} not found in recent preview"})
