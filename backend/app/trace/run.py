"""Trace a post: start from ONE post, find where the same text spread, run the swarm detector on the copies.

We investigate the message, not the messenger:
- public data only: exact-phrase search results (open web via Oxylabs, Bluesky's public search);
- no identity research, no profile lookups, no linking of accounts across platforms: an account is only the
  name the search API returned with the post; a web page is only its domain;
- nothing is stored or cached (domain receipts reuse the public-domain cache of /source);
- max 6 searches and 30 s per trace, 5 traces per hour per IP (API).
"""
from __future__ import annotations

import asyncio
import time
import uuid
from collections import Counter, defaultdict

from app.receipts import service as receipts_service
from app.swarm import clusters, collectors, ingest, textreuse
from app.swarm.collectors import bluesky as bsky
from app.swarm.collectors.http import CollectorError, Session
from app.swarm.ingest import Post
from app.trace import fingerprint, search

MAX_SEARCHES = 6
DEADLINE_S = 30.0
MAX_RECEIPT_DOMAINS = 8
CONFIRM_WORDS = 6
MAX_LISTED = 200
PRINCIPLE = "We trace where this text spread. We don't research who is behind any account."


class TraceError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message, self.status = message, status


def _windows(fp_norm: str, n: int = CONFIRM_WORDS) -> set[str]:
    ws = fp_norm.split()
    return {" ".join(ws[i:i + n]) for i in range(max(1, len(ws) - n + 1))}


def confirm_text(fp_norms: list[str], text: str, source_norm: str = "") -> list[int]:
    """Indexes of fingerprints found word for word in a post's text (or the whole post copied)."""
    body = fingerprint.norm(text)
    hits = [i for i, f in enumerate(fp_norms) if f and f in body]
    if not hits and source_norm and body:
        if textreuse.jaccard(textreuse.shingles(body), textreuse.shingles(source_norm)) >= textreuse.JACCARD:
            hits = list(range(len(fp_norms)))
    return hits


def confirm_snippet(fp_norms: list[str], snippet: str) -> list[int]:
    """Search snippets are truncated: a run of >= 6 consecutive fingerprint words must appear."""
    body = fingerprint.norm(snippet)
    return [i for i, f in enumerate(fp_norms) if any(w in body for w in _windows(f))]


async def _source(text: str | None, url: str | None) -> dict:
    if url:
        try:
            collectors.classify(url)
            coll, _ = await collectors.collect_urls([url], cache=False)
        except CollectorError as exc:
            raise TraceError(exc.message, exc.status) from None
        if not coll.posts:
            raise TraceError("That post has no text to trace.", 404)
        p = coll.posts[0]
        return {"text": p.text, "platform": p.platform, "url": url, "id": p.id, "author": p.author, "ts": p.ts,
                "author_created_at": p.author_created_at, "link": p.url}
    text = (text or "").strip()
    if len(fingerprint.words(text)) < fingerprint.MIN_WORDS:
        raise TraceError("Please paste a post of at least 8 words, or a link to a public post.")
    return {"text": text[:fingerprint.MAX_INPUT_CHARS], "platform": None, "url": None, "id": None, "author": None, "ts": None}


async def trace(text: str | None = None, url: str | None = None, progress=None) -> dict:
    t0 = time.monotonic()
    say = progress or (lambda m: None)
    say("Reading the post…" if url else "Choosing distinctive sentences…")
    src = await _source(text, url)
    if url:
        say("Choosing distinctive sentences…")
    fps, fmeta = await fingerprint.pick(src["text"])
    if not fps:
        raise TraceError("No sentence of at least 8 words was found to search for.")
    queries = [fingerprint.query_phrase(f) for f in fps]
    fp_norms = [fingerprint.norm(q) for q in queries]
    source_norm = fingerprint.norm(fingerprint.clean(src["text"]))
    notes: list[str] = [PRINCIPLE]
    if fmeta.get("note"):
        notes.append(fmeta["note"])

    coverage = {
        "web": {"source": "Open web (search engine results via Oxylabs)", "status": "not_configured", "searches": 0,
                "results": 0, "confirmed": 0, "note": None},
        "bluesky": {"source": "Bluesky (public post search)", "status": "searched", "searches": 0, "results": 0,
                    "confirmed": 0, "note": None},
        "mastodon": {"source": "Mastodon", "status": "not_available", "searches": 0, "results": 0, "confirmed": 0,
                     "note": search.MASTODON_NOTE},
    }
    web_on = search.web_configured()
    if not web_on:
        coverage["web"]["note"] = "Open-web search is not configured on this server, so only Bluesky was searched."
    raw_web: list[tuple[int, dict]] = []
    raw_bsky: list[tuple[int, dict]] = []

    async with Session(max_requests=MAX_SEARCHES, deadline_s=DEADLINE_S) as s:
        async def one(kind: str, i: int, q: str):
            try:
                hits = await (search.web(s, q) if kind == "web" else search.bluesky(s, q))
            except search.SearchUnavailable as exc:
                coverage[kind]["status"], coverage[kind]["note"] = "failed", str(exc)
                return
            except Exception as exc:  # noqa: BLE001 - budget/time/network: keep the other searches
                coverage[kind]["status"] = "partial"
                coverage[kind]["note"] = getattr(exc, "message", None) or "Some searches did not finish in time."
                return
            coverage[kind]["searches"] += 1
            coverage[kind]["results"] += len(hits)
            (raw_web if kind == "web" else raw_bsky).extend((i, h) for h in hits)
            say(f"Searched {'the web' if kind == 'web' else 'Bluesky'} for sentence {i + 1}: {len(hits)} results…")
        jobs = []
        for i, q in enumerate(queries):
            if web_on:
                jobs.append(one("web", i, q))
            jobs.append(one("bluesky", i, q))
        await asyncio.gather(*jobs[:MAX_SEARCHES])
        if web_on and coverage["web"]["status"] == "not_configured":
            coverage["web"]["status"] = "searched"
        searches_used = s.requests

    # --- verify every hit (the search proposes, code verifies)
    copies: dict[str, dict] = {}
    related = 0
    for i, p in raw_bsky:
        if p["uri"] == src.get("id"):
            continue
        hits = confirm_text(fp_norms, (p.get("record") or {}).get("text", ""), source_norm)
        if not hits:
            related += 1
            continue
        post = bsky._post(p)
        if post is None:
            continue
        item = copies.setdefault(p["uri"], {"platform": "bluesky", "account": post.author, "url": bsky.web_url(p),
                                             "text": post.text[:600], "ts": post.ts, "confirmed": True,
                                             "fingerprints": set(), "_post": post})
        item["fingerprints"].update(hits)
    unconfirmed = 0
    for i, r in raw_web:
        if src.get("url") and r["url"].rstrip("/") == src["url"].rstrip("/"):
            continue
        hits = confirm_snippet(fp_norms, f"{r['title']} {r['snippet']}")
        item = copies.get(r["url"])
        if item is None:
            item = copies[r["url"]] = {"platform": "web", "domain": r["domain"], "url": r["url"], "title": r["title"],
                                       "text": r["snippet"][:600], "ts": r["ts"], "confirmed": bool(hits),
                                       "fingerprints": set(hits)}
        else:
            item["fingerprints"].update(hits)
            item["confirmed"] = item["confirmed"] or bool(hits)
    for item in copies.values():
        if item["platform"] == "web" and not item["confirmed"]:
            unconfirmed += 1
    for kind in ("web", "bluesky"):
        coverage[kind]["confirmed"] = sum(1 for c in copies.values() if c["platform"] == kind and c["confirmed"])
    if related:
        notes.append(f"{related} Bluesky search results mention or quote the post without copying its text; they are "
                     "not counted as copies.")
    if unconfirmed:
        notes.append(f"{unconfirmed} web results did not show the sentence in their search snippet; they are listed "
                     "as unconfirmed and left out of the analysis.")

    # --- the swarm detector on the confirmed, dated copies (+ the source post when its time is known)
    batch_posts: list[Post] = []
    if src.get("ts"):
        batch_posts.append(Post(id=src["id"] or "source", author=src["author"] or "source", ts=src["ts"], text=src["text"],
                                url=src.get("link"), author_created_at=src.get("author_created_at"),
                                platform=src["platform"]))
    for key, c in copies.items():
        if not c["confirmed"] or c["ts"] is None:
            continue
        if c["platform"] == "bluesky":
            batch_posts.append(c["_post"])
        else:
            batch_posts.append(Post(id=key, author=c["domain"], ts=c["ts"], text=f"{c['title']} {c['text']}", url=c["url"],
                                    platform="web"))
    analysis = None
    if len({p.author.lower() for p in batch_posts}) >= 2:
        batch = ingest.Batch(posts=batch_posts, rows_total=len(batch_posts))
        analysis = await clusters.analyze_batch(batch, groups=[list(range(len(batch_posts)))])
    undated = sum(1 for c in copies.values() if c["confirmed"] and c["ts"] is None)
    if undated:
        notes.append(f"{undated} confirmed web copies have no date in the search results, so they are listed but not "
                     "placed on the timeline or used for timing analysis.")

    # --- receipts for hosting domains (domain age + lookalike only)
    domains = Counter(c["domain"] for c in copies.values() if c["platform"] == "web" and c["confirmed"])
    receipts: dict[str, list] = {}
    if domains:
        say(f"Checking {min(len(domains), MAX_RECEIPT_DOMAINS)} websites (domain age, look-alike names)…")
        top = [d for d, _ in domains.most_common(MAX_RECEIPT_DOMAINS)]
        left = max(3.0, DEADLINE_S - (time.monotonic() - t0))
        try:
            async with asyncio.timeout(min(10.0, left)):
                got = await asyncio.gather(*(receipts_service.domain_receipts(d) for d in top), return_exceptions=True)
        except TimeoutError:
            got = []
            notes.append("Website checks did not finish in time.")
        for d, r in zip(top, got):
            if isinstance(r, dict):
                receipts[d] = r["receipts"]

    # --- spread view
    confirmed = [c for c in copies.values() if c["confirmed"]]
    dated = sorted((c for c in confirmed if c["ts"] is not None), key=lambda c: c["ts"])
    origin = None
    if src.get("ts"):
        origin = {"platform": src["platform"], "who": src["author"], "url": src["url"], "ts": src["ts"], "is_source": True}
    first = dated[0] if dated else None
    if first and (origin is None or first["ts"] < origin["ts"]):
        first_seen = {"platform": first["platform"], "who": first.get("account") or first.get("domain"), "url": first["url"],
                      "ts": first["ts"], "is_source": False}
    else:
        first_seen = origin
    accounts = defaultdict(lambda: {"copies": 0, "first": None})
    for c in confirmed:
        if c["platform"] == "bluesky":
            a = accounts[c["account"]]
            a["copies"] += 1
            a["first"] = c["ts"] if a["first"] is None else min(a["first"], c["ts"])
    sites = defaultdict(lambda: {"pages": 0, "first": None})
    for c in confirmed:
        if c["platform"] == "web":
            d = sites[c["domain"]]
            d["pages"] += 1
            if c["ts"] is not None:
                d["first"] = c["ts"] if d["first"] is None else min(d["first"], c["ts"])
    main = (analysis or {}).get("clusters", [None])[0] if analysis and analysis.get("clusters") else None
    listed = sorted(copies.values(), key=lambda c: (not c["confirmed"], c["ts"] is None, c["ts"] or 0))[:MAX_LISTED]
    return {
        "id": "t_" + uuid.uuid4().hex[:10],
        "principle": PRINCIPLE,
        "source": {k: src.get(k) for k in ("platform", "url", "author", "ts")} | {"text": src["text"][:1000]},
        "fingerprints": [{"sentence": f, "query": q, "copies": sum(1 for c in confirmed if i in c["fingerprints"])}
                         for i, (f, q) in enumerate(zip(fps, queries))],
        "fingerprint_method": fmeta["method"],
        "coverage": list(coverage.values()),
        "spread": {
            "copies": len(confirmed), "unconfirmed": unconfirmed, "related_not_copies": related,
            "first_seen": first_seen,
            "accounts": sorted(({"account": k, **v} for k, v in accounts.items()), key=lambda x: x["first"] or 0),
            "domains": sorted(({"domain": k, **v, "receipts": receipts.get(k)} for k, v in sites.items()),
                              key=lambda x: (x["first"] is None, x["first"] or 0)),
            "timeline": sorted(([{"t": origin["ts"], "platform": origin["platform"], "who": origin["who"],
                                  "url": origin["url"], "is_source": True}] if origin else [])
                               + [{"t": c["ts"], "platform": c["platform"], "who": c.get("account") or c.get("domain"),
                                   "url": c["url"], "is_source": False} for c in dated], key=lambda x: x["t"])[:MAX_LISTED],
        },
        "verdict": main["verdict"] if main else None,
        "cluster": main,
        "copies": [{k: (sorted(v) if isinstance(v, set) else v) for k, v in c.items() if not k.startswith("_")}
                   for c in listed],
        "notes": notes + ([n for n in analysis["notes"] if not n.startswith("Processed in memory")] if analysis else []),
        "cost": {"llm_calls": fmeta["llm_calls"], "credits": round(fmeta["credits"], 4), "searches": searches_used},
        "elapsed_ms": round((time.monotonic() - t0) * 1000),
    }
