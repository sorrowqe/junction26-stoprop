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

from app.pipeline import extract
from app.receipts import domain as dom
from app.receipts import service as receipts_service
from app.swarm import clusters, collectors, ingest, textreuse
from app.swarm.collectors import bluesky as bsky
from app.swarm.collectors.base import make_post
from app.swarm.collectors.http import CollectorError, Session
from app.swarm.ingest import Post
from app.trace import fingerprint, search

MAX_SEARCHES = 6
DEADLINE_S = 30.0
MAX_RECEIPT_DOMAINS = 8
CONFIRM_WORDS = 6
MIN_QUERY_WORDS = 3
MAX_LISTED = 200
PRINCIPLE = "We trace where this text spread. We don't research who is behind any account."


class TraceError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message, self.status = message, status


def _windows(fp_norm: str, n: int = CONFIRM_WORDS) -> set[str]:
    ws = fp_norm.split()
    k = min(n, max(2, len(ws)))
    return {" ".join(ws[i:i + k]) for i in range(max(1, len(ws) - k + 1))}


def confirm_text(fp_norms: list[str], text: str, source_norm: str = "") -> list[int]:
    """Indexes of fingerprints found word for word in a post's text (or the whole post copied)."""
    body = fingerprint.norm(text)
    hits = [i for i, f in enumerate(fp_norms) if f and f in body]
    if not hits and source_norm and body:
        if textreuse.jaccard(textreuse.shingles(body), textreuse.shingles(source_norm)) >= textreuse.JACCARD:
            hits = list(range(len(fp_norms)))
    return hits


def confirm_snippet(fp_norms: list[str], snippet: str, *, topic_mode: bool = False) -> list[int]:
    """Search snippets are truncated: a run of >= 6 consecutive fingerprint words must appear (or >=60% of topic words)."""
    body = fingerprint.norm(snippet)
    if topic_mode:
        body_words = set(body.split())
        out = []
        for i, f in enumerate(fp_norms):
            kws = [w for w in f.split() if len(w) >= 3]
            if kws and sum(1 for w in kws if w in body_words) >= max(2, (len(kws) * 3 + 4) // 5):
                out.append(i)
        return out
    return [i for i, f in enumerate(fp_norms) if any(w in body for w in _windows(f))]


async def _source(text: str | None, url: str | None) -> dict:
    if url:
        platform = None
        try:
            platform = collectors.classify(url)
        except CollectorError as exc:
            if exc.code == "closed_platform":
                raise TraceError(exc.message, exc.status) from None
        if platform is not None:
            try:
                coll, _ = await collectors.collect_urls([url], cache=False)
            except CollectorError as exc:
                raise TraceError(exc.message, exc.status) from None
            if not coll.posts:
                raise TraceError("That post has no text to trace.", 404)
            p = coll.posts[0]
            return {"text": p.text, "platform": p.platform, "url": url, "id": p.id, "author": p.author, "ts": p.ts,
                    "author_created_at": p.author_created_at, "link": p.url}
        try:
            art = await extract.fetch_article(url)
        except extract.FetchError as exc:
            raise TraceError(exc.message, exc.status) from None
        reg = dom.registrable(dom.normalize_host(art.final_url))
        return {"text": art.text[:fingerprint.MAX_INPUT_CHARS], "platform": "web", "url": url,
                "id": art.final_url, "author": reg, "ts": None, "author_created_at": None, "link": art.final_url}
    text = (text or "").strip()
    if len(fingerprint.words(text)) < MIN_QUERY_WORDS:
        raise TraceError("Please enter at least 3 words (a claim, headline, or post text) or a public link.")
    return {"text": text[:fingerprint.MAX_INPUT_CHARS], "platform": None, "url": None, "id": None, "author": None, "ts": None}


async def trace(text: str | None = None, url: str | None = None, progress=None, *, include_forums: bool = False) -> dict:
    t0 = time.monotonic()
    say = progress or (lambda m: None)
    say("Reading the link…" if url else "Choosing distinctive phrases…")
    src = await _source(text, url)
    if url:
        say("Choosing distinctive phrases…")
    src_words = fingerprint.words(src["text"])
    topic_mode = src["platform"] is None and len(src_words) < 10 and not src["text"].rstrip().endswith((".", "!", "?"))
    if len(src_words) < fingerprint.MIN_WORDS or topic_mode:
        fps = [src["text"].strip()]
        fmeta = {"method": "query", "llm_calls": 0, "credits": 0.0, "note": None}
    else:
        fps, fmeta = await fingerprint.pick(src["text"])
    if not fps:
        raise TraceError("No searchable phrase was found in that text.")
    queries = [fingerprint.query_phrase(f) for f in fps]
    fp_norms = [fingerprint.norm(q) for q in queries]
    source_norm = fingerprint.norm(fingerprint.clean(src["text"]))
    notes: list[str] = [PRINCIPLE]
    if fmeta.get("note"):
        notes.append(fmeta["note"])

    web_on = search.web_configured()
    coverage = {
        "web": {"source": "Open web & news (search engine results)", "status": "not_configured", "searches": 0,
                "results": 0, "confirmed": 0, "note": None},
        "bluesky": {"source": "Bluesky (public post search)", "status": "searched", "searches": 0, "results": 0,
                    "confirmed": 0, "note": None},
    }
    if include_forums:
        coverage["reddit"] = {"source": "Reddit (public threads)", "status": "searched" if web_on else "not_configured",
                              "searches": 0, "results": 0, "confirmed": 0, "note": None}
        coverage["telegram"] = {"source": "Telegram (public channels)", "status": "searched" if web_on else "not_configured",
                                "searches": 0, "results": 0, "confirmed": 0, "note": None}
        coverage["hackernews"] = {"source": "Hacker News (public search)", "status": "searched", "searches": 0,
                                  "results": 0, "confirmed": 0, "note": None}
    coverage["mastodon"] = {"source": "Mastodon", "status": "not_available", "searches": 0, "results": 0,
                            "confirmed": 0, "note": search.MASTODON_NOTE}

    if not web_on:
        coverage["web"]["note"] = "Open-web search is not configured on this server, so only public social search ran."
    raw_web: list[tuple[int, dict]] = []
    raw_bsky: list[tuple[int, dict]] = []
    raw_reddit: list[tuple[int, dict]] = []
    raw_telegram: list[tuple[int, dict]] = []
    raw_hn: list[tuple[int, dict]] = []

    max_req = (MAX_SEARCHES * 3) if include_forums else MAX_SEARCHES
    async with Session(max_requests=max_req, deadline_s=DEADLINE_S) as s:
        async def one(kind: str, i: int, q: str):
            label = {"web": "the open web", "bluesky": "Bluesky", "reddit": "Reddit",
                     "telegram": "Telegram", "hackernews": "Hacker News"}[kind]
            exact = not topic_mode
            try:
                if kind == "web":
                    hits = await search.web(s, q, exact=exact)
                elif kind == "bluesky":
                    hits = await search.bluesky(s, q, exact=exact)
                elif kind == "reddit":
                    hits = await search.reddit_posts(s, q, exact=exact)
                elif kind == "telegram":
                    hits = await search.telegram_posts(s, q, exact=exact)
                else:
                    hits = await search.hackernews_posts(s, q, exact=exact)
            except search.SearchUnavailable as exc:
                coverage[kind]["status"], coverage[kind]["note"] = "failed", str(exc)
                return
            except Exception as exc:  # noqa: BLE001 - budget/time/network: keep the other searches
                coverage[kind]["status"] = "partial"
                coverage[kind]["note"] = getattr(exc, "message", None) or "Some searches did not finish in time."
                return
            coverage[kind]["searches"] += 1
            coverage[kind]["results"] += len(hits)
            {"web": raw_web, "bluesky": raw_bsky, "reddit": raw_reddit,
             "telegram": raw_telegram, "hackernews": raw_hn}[kind].extend((i, h) for h in hits)
            say(f"Searched {label} for phrase {i + 1}: {len(hits)} results…")

        jobs = []
        for i, q in enumerate(queries):
            if web_on:
                jobs.append(one("web", i, q))
            jobs.append(one("bluesky", i, q))
            if include_forums and i == 0:
                if web_on:
                    jobs.append(one("reddit", i, q))
                    jobs.append(one("telegram", i, q))
                jobs.append(one("hackernews", i, q))
        await asyncio.gather(*jobs[:max_req])
        if web_on and coverage["web"]["status"] == "not_configured":
            coverage["web"]["status"] = "searched"
        searches_used = s.requests

    # --- verify every hit (the search proposes, code verifies)
    copies: dict[str, dict] = {}
    related = 0
    for i, p in raw_bsky:
        if p["uri"] == src.get("id"):
            continue
        ptxt = (p.get("record") or {}).get("text", "")
        hits = confirm_snippet(fp_norms, ptxt, topic_mode=True) if topic_mode else confirm_text(fp_norms, ptxt, source_norm)
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
    import re as _re
    for i, rd_hit in raw_reddit:
        m = _re.search(r"reddit\.com/r/([A-Za-z0-9_]+)", rd_hit["url"], _re.I)
        sub = f"r/{m.group(1)}" if m else "reddit"
        body_txt = f"{rd_hit['title']} {rd_hit['snippet']}".strip()
        hits = confirm_snippet(fp_norms, body_txt, topic_mode=topic_mode)
        if not hits:
            related += 1
            continue
        post = (Post(id=rd_hit["url"], author=sub, ts=rd_hit["ts"], text=body_txt, url=rd_hit["url"], platform="reddit")
                if rd_hit["ts"] is not None else None)
        item = copies.setdefault(rd_hit["url"], {
            "platform": "reddit", "account": sub, "url": rd_hit["url"],
            "text": rd_hit["snippet"][:600], "ts": rd_hit["ts"], "confirmed": True, "fingerprints": set(),
            **({"_post": post} if post else {}),
        })
        item["fingerprints"].update(hits)

    for i, tg_hit in raw_telegram:
        m = _re.search(r"(?:t\.me|telegram\.me)/(?:s/)?([A-Za-z0-9_]{4,64})", tg_hit["url"], _re.I)
        chan = f"@{m.group(1)}" if m else "@telegram"
        body_txt = f"{tg_hit['title']} {tg_hit['snippet']}".strip()
        hits = confirm_snippet(fp_norms, body_txt, topic_mode=topic_mode)
        if not hits:
            related += 1
            continue
        post = (Post(id=tg_hit["url"], author=chan, ts=tg_hit["ts"], text=body_txt, url=tg_hit["url"], platform="telegram")
                if tg_hit["ts"] is not None else None)
        item = copies.setdefault(tg_hit["url"], {
            "platform": "telegram", "account": chan, "url": tg_hit["url"],
            "text": tg_hit["snippet"][:600], "ts": tg_hit["ts"], "confirmed": True, "fingerprints": set(),
            **({"_post": post} if post else {}),
        })
        item["fingerprints"].update(hits)

    for i, hn_item in raw_hn:
        author = hn_item.get("author") or ""
        body_txt = f"{hn_item.get('title') or hn_item.get('story_title') or ''}\n{hn_item.get('comment_text') or hn_item.get('story_text') or ''}".strip()
        hits = (confirm_snippet(fp_norms, body_txt, topic_mode=True) if topic_mode
                else (confirm_text(fp_norms, body_txt, source_norm) or confirm_snippet(fp_norms, body_txt)))
        if not hits or not author:
            related += 1
            continue
        oid = str(hn_item["objectID"])
        purl = f"https://news.ycombinator.com/item?id={oid}"
        post = make_post(f"hn:{oid}", f"hn/{author}", hn_item.get("created_at"), body_txt, url=purl, platform="hackernews")
        if post is None:
            continue
        item = copies.setdefault(f"hn:{oid}", {
            "platform": "hackernews", "account": f"hn/{author}", "url": purl,
            "text": body_txt[:600], "ts": post.ts, "confirmed": True, "fingerprints": set(), "_post": post,
        })
        item["fingerprints"].update(hits)

    unconfirmed = 0
    for i, r in raw_web:
        if src.get("url") and r["url"].rstrip("/") == src["url"].rstrip("/"):
            continue
        hits = confirm_snippet(fp_norms, f"{r['title']} {r['snippet']}", topic_mode=topic_mode)
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
    for kind in coverage:
        if kind != "mastodon":
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
        if "_post" in c:
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
        if c["platform"] != "web" and c.get("account"):
            label = c["account"] if c["platform"] == "bluesky" else f"{c['account']} ({c['platform']})"
            a = accounts[label]
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
    timeline_pts = sorted(
        ([{"t": origin["ts"], "platform": origin["platform"] or "source", "who": origin["who"],
           "url": origin["url"], "snippet": src["text"][:160], "is_source": True}] if origin else [])
        + [{"t": c["ts"], "platform": c["platform"], "who": c.get("account") or c.get("domain"),
            "url": c["url"], "snippet": (c.get("title") or c.get("text") or "")[:160], "is_source": False}
           for c in dated],
        key=lambda x: x["t"],
    )[:MAX_LISTED]
    spread_activity = None
    if len(timeline_pts) >= 2:
        t_start, t_end = timeline_pts[0]["t"], timeline_pts[-1]["t"]
        span = max(t_end - t_start, 60.0)
        n_bins = 20
        step = span / n_bins
        sbins = [{"t0": round(t_start + k * step, 1), "t1": round(t_start + (k + 1) * step, 1),
                  "total": 0, "by_platform": {}} for k in range(n_bins)]
        for pt in timeline_pts:
            bi = min(n_bins - 1, max(0, int((pt["t"] - t_start) / step)))
            sbins[bi]["total"] += 1
            pl = pt["platform"]
            sbins[bi]["by_platform"][pl] = sbins[bi]["by_platform"].get(pl, 0) + 1
        spread_activity = {"start": t_start, "end": t_start + span, "bin_seconds": round(step, 1),
                           "peak": max(b["total"] for b in sbins), "bins": sbins}

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
            "timeline": timeline_pts,
            "activity": spread_activity,
        },
        "verdict": main["verdict"] if main else None,
        "cluster": main,
        "copies": [{k: (sorted(v) if isinstance(v, set) else v) for k, v in c.items() if not k.startswith("_")}
                   for c in listed],
        "notes": notes + ([n for n in analysis["notes"] if not n.startswith("Processed in memory")] if analysis else []),
        "cost": {"llm_calls": fmeta["llm_calls"], "credits": round(fmeta["credits"], 4), "searches": searches_used},
        "elapsed_ms": round((time.monotonic() - t0) * 1000),
    }
