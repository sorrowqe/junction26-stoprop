"""M — amplification (CLAUDE.md §5): the same non-mainstream URL, hashtag or mention pushed by >= 5
cluster authors. Links to mainstream outlets (config/outlets.yaml) never count."""
from __future__ import annotations

import re
from collections import defaultdict
from urllib.parse import parse_qsl, urlencode, urlsplit

from app.receipts import domain as dom
from app.receipts.lookalike import official_domains

MIN_AUTHORS = 5
_URL = re.compile(r"https?://[^\s<>\"'()]+|www\.[^\s<>\"'()]+", re.I)
_HASHTAG = re.compile(r"(?<![\w#])#(\w{2,})")
_MENTION = re.compile(r"(?<![\w@])@(\w{2,})")


def normalize_url(url: str) -> str | None:
    u = url.strip().rstrip(".,;:!?)")
    if not re.match(r"https?://", u, re.I):
        u = "http://" + u
    try:
        parts = urlsplit(u)
        host = dom.normalize_host(parts.hostname or "")
    except ValueError:
        return None
    if not host:
        return None
    host = host[4:] if host.startswith("www.") else host
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith(("utm_", "fbclid", "gclid"))])
    path = parts.path.rstrip("/")
    return f"{host}{path}" + (f"?{query}" if query else "")


def is_mainstream(normalized_url: str) -> bool:
    host = normalized_url.split("/", 1)[0]
    try:
        reg = dom.registrable(host)
    except ValueError:
        return False
    return reg in official_domains()


def items(post) -> dict[str, set[str]]:
    urls = {n for n in (normalize_url(u) for u in _URL.findall(post.text) + ([post.url] if post.url else [])) if n}
    return {"url": urls,
            "hashtag": {h.lower() for h in _HASHTAG.findall(post.text)},
            "mention": {m.lower() for m in _MENTION.findall(post.text)}}


def amplification(cluster_posts: list) -> dict:
    authors: dict[tuple[str, str], set[str]] = defaultdict(set)
    for p in cluster_posts:
        for kind, values in items(p).items():
            for v in values:
                authors[(kind, v)].add(p.author.lower())
    pushed, mainstream = [], []
    for (kind, value), who in sorted(authors.items(), key=lambda kv: -len(kv[1])):
        if len(who) < MIN_AUTHORS:
            continue
        if kind == "url" and is_mainstream(value):
            mainstream.append({"kind": kind, "value": value, "authors": len(who)})
        else:
            pushed.append({"kind": kind, "value": value, "authors": len(who)})
    return {"present": bool(pushed), "pushed": pushed[:5], "mainstream": mainstream[:5]}
