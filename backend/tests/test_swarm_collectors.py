"""Swarm link collection: Bluesky, Mastodon, YouTube collectors with recorded (anonymized) API responses,
closed-platform messages, limits, SSRF, cache, multi-link mode, campaign merging and the API.
No live network: every request goes to an httpx2.MockTransport."""
import asyncio
import copy
import json
from pathlib import Path

import httpx2
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.pipeline import extract
from app.swarm import clusters, collectors, ingest
from app.swarm.collectors import base, bluesky, mastodon, youtube
from app.swarm.collectors import http as chttp

FIX = Path(__file__).parent / "fixtures" / "swarm"
client = TestClient(app)
BSKY_URL = "https://bsky.app/profile/newsdesk.example.social/post/3rootpost0001"
MASTO_URL = "https://mastodon.example/@Project/999"
YT_URL = "https://www.youtube.com/watch?v=abcdefghijk"


def fx(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


class Router:
    """MockTransport handler: (host, path) -> response function; records every request."""

    def __init__(self, routes):
        self.routes = routes
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request):
        self.requests.append(request)
        fn = self.routes.get((request.url.host, request.url.path))
        if fn is None:
            return httpx2.Response(404, json={"error": "NotFound"})
        out = fn(request)
        return out if isinstance(out, httpx2.Response) else httpx2.Response(200, json=out)


def install(monkeypatch, routes) -> Router:
    router = Router(routes)
    monkeypatch.setattr(chttp, "client_factory", lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(router)))
    return router


def bsky_routes():
    thread, sub = fx("bluesky_thread.json"), fx("bluesky_subthread.json")
    cut_uri = sub["thread"]["post"]["uri"]

    def get_thread(req):
        uri = req.url.params["uri"]
        if uri == cut_uri:
            return sub
        if uri.endswith("/3rootpost0001"):
            return thread
        return httpx2.Response(400, json={"error": "NotFound", "message": "Post not found"})

    def get_posts(req):
        wanted = req.url.params.get_list("uris")
        nodes = [n["post"] for n in thread["thread"]["replies"] if "post" in n]
        return {"posts": [p for p in nodes if p["uri"] in wanted]}

    def get_profiles(req):
        actors = req.url.params.get_list("actors")
        known = {n["post"]["author"]["handle"]: n["post"]["author"] for n in thread["thread"]["replies"] if "post" in n}
        out = [{"did": known[a]["did"], "handle": a, "createdAt": known[a].get("createdAt")} for a in actors if a in known]
        out += [p for p in fx("bluesky_profiles.json")["profiles"] if p["did"] in actors]
        return {"profiles": out}
    h = "public.api.bsky.app"
    return {(h, "/xrpc/app.bsky.feed.getPostThread"): get_thread, (h, "/xrpc/app.bsky.feed.getQuotes"): lambda r: fx("bluesky_quotes.json"),
            (h, "/xrpc/app.bsky.actor.getProfiles"): get_profiles, (h, "/xrpc/app.bsky.feed.getPosts"): get_posts}


def masto_routes(context_status=200):
    h = "mastodon.example"
    return {(h, "/api/v1/statuses/999"): lambda r: fx("mastodon_status.json"),
            (h, "/api/v1/statuses/999/context"): (lambda r: fx("mastodon_context.json")) if context_status == 200
            else (lambda r: httpx2.Response(context_status, json={"error": "nope"}))}


def run(coro):
    return asyncio.run(coro)


def analyze(coll):
    return run(clusters.analyze_batch(ingest.Batch(posts=list(coll.posts), rows_total=len(coll.posts))))


# ---------------------------------------------------------------- classify

@pytest.mark.parametrize("url, platform", [
    (BSKY_URL, "bluesky"), ("https://bsky.app/profile/did:plc:abc/post/3abc", "bluesky"),
    (MASTO_URL, "mastodon"), ("https://fosstodon.org/@someone@mastodon.social/1234567", "mastodon"),
    ("https://mastodon.example/users/Project/statuses/999", "mastodon"),
    (YT_URL, "youtube"), ("https://youtu.be/abcdefghijk", "youtube"), ("https://www.youtube.com/shorts/abcdefghijk", "youtube"),
    ("https://www.reddit.com/r/europe/comments/1xw9jzk/title/", "reddit"),
    ("https://www.reddit.com/r/europe", "reddit"),
    ("https://redd.it/1xw9jzk", "reddit"),
    ("https://t.me/euronews_en", "telegram"),
    ("https://t.me/durov/540", "telegram"),
    ("https://news.ycombinator.com/item?id=41234567", "hackernews"),
])
def test_classify_supported(url, platform):
    assert collectors.classify(url) == platform


@pytest.mark.parametrize("url, phrase", [
    ("https://x.com/someone/status/123", "X (Twitter)"), ("https://twitter.com/a/status/1", "X (Twitter)"),
    ("https://www.facebook.com/page/posts/1", "Meta's platforms"), ("https://www.instagram.com/p/abc/", "Meta's platforms"),
    ("https://www.tiktok.com/@a/video/1", "TikTok"),
])
def test_closed_platforms_explained(url, phrase):
    with pytest.raises(chttp.CollectorError) as exc:
        collectors.classify(url)
    assert phrase in exc.value.message and "Swarm mode" in exc.value.message and exc.value.status == 400


@pytest.mark.parametrize("url", ["https://example.com/article", "https://www.youtube.com/@channel", "https://bsky.app/profile/x",
                                 "ftp://bsky.app/profile/a/post/b", ""])
def test_unsupported_links(url):
    with pytest.raises(chttp.CollectorError):
        collectors.classify(url)


def test_youtube_link_forms():
    assert youtube.parse("https://www.youtube.com/watch?v=abcdefghijk&lc=UgxComment1") == ("abcdefghijk", "UgxComment1")
    assert youtube.parse("https://m.youtube.com/live/abcdefghijk?feature=share") == ("abcdefghijk", None)
    assert youtube.parse("https://www.youtube.com/watch?v=short") is None


def test_html_to_text_keeps_mentions_and_hashtags():
    html = ('<p><span class="h-card"><a href="https://m.example/@a" class="u-url mention">@<span>a</span></a></span> '
            'hi &amp; bye<br>line2 <a href="https://m.example/tags/x" class="mention hashtag">#<span>x</span></a></p>')
    assert base.html_to_text(html) == "@a hi & bye\nline2 #x"


# ------------------------------------------------------------------ Bluesky

def test_bluesky_thread_full_collection(monkeypatch):
    router = install(monkeypatch, bsky_routes())
    progress = []
    coll, cached = run(collectors.collect_url(BSKY_URL, progress.append))
    assert not cached and coll.platform == "bluesky" and coll.complete
    # root + 6 campaign + 4 organic + no-date + cut branch + 2 branch replies + 2 quotes; notFound skipped
    assert len(coll.posts) == 17 and coll.requests == 4
    paths = [r.url.path.rsplit(".", 1)[-1] for r in router.requests]
    assert paths == ["getPostThread", "getPostThread", "getQuotes", "getProfiles"]
    assert router.requests[-1].url.params.get_list("actors") == ["did:plc:user0030"]  # only the missing date
    assert all(p.author_created_at for p in coll.posts)
    camp = [p for p in coll.posts if p.author.startswith("levywatch")]
    assert len(camp) == 6 and camp[0].url == "https://levy-truth.example/proof"
    assert coll.root["author"] == "newsdesk.example.social" and coll.root["quotes"] == 2
    assert progress and "replies" in progress[0]
    for r in router.requests:
        assert r.headers["user-agent"].startswith("StopropBot/")


def test_bluesky_campaign_detected_end_to_end(monkeypatch):
    install(monkeypatch, bsky_routes())
    coll, _ = run(collectors.collect_url(BSKY_URL))
    out = analyze(coll)
    top = out["clusters"][0]
    assert top["verdict"] == "likely_coordinated" and set(top["families"]) == {"T", "S", "A", "M"}
    assert top["author_count"] == 6 and out["summary"]["by_verdict"]["likely_coordinated"] == 1


def test_bluesky_not_found(monkeypatch):
    install(monkeypatch, bsky_routes())
    with pytest.raises(chttp.CollectorError) as exc:
        run(collectors.collect_url("https://bsky.app/profile/newsdesk.example.social/post/3doesnotexist"))
    assert exc.value.status == 404 and "not found" in exc.value.message


def test_bluesky_request_budget_keeps_partial(monkeypatch):
    install(monkeypatch, bsky_routes())
    monkeypatch.setattr(chttp, "MAX_REQUESTS", 1)
    monkeypatch.setattr(chttp.Session.__init__, "__defaults__", (1, chttp.DEADLINE_S, None))
    coll, _ = run(collectors.collect_url(BSKY_URL))
    assert not coll.complete and coll.requests == 1 and len(coll.posts) == 13
    assert any("branches were not fetched" in n for n in coll.notes)


def test_bluesky_rate_limited_midway(monkeypatch):
    routes = bsky_routes()
    routes[("public.api.bsky.app", "/xrpc/app.bsky.feed.getQuotes")] = lambda r: httpx2.Response(429)
    install(monkeypatch, routes)
    coll, _ = run(collectors.collect_url(BSKY_URL))
    assert not coll.complete and any("slow down" in n for n in coll.notes) and len(coll.posts) == 15


def test_deadline_reached_before_first_request(monkeypatch):
    install(monkeypatch, bsky_routes())
    monkeypatch.setattr(chttp.Session.__init__, "__defaults__", (40, 0.5, None))
    with pytest.raises(chttp.CollectorError) as exc:
        run(collectors.collect_url(BSKY_URL))
    assert exc.value.status == 504


def test_results_cached_for_ten_minutes(monkeypatch):
    router = install(monkeypatch, bsky_routes())
    run(collectors.collect_url(BSKY_URL))
    n = len(router.requests)
    coll, cached = run(collectors.collect_url(BSKY_URL))
    assert cached and len(router.requests) == n and len(coll.posts) == 17
    hit = collectors._cache[("thread", BSKY_URL)]
    monkeypatch.setattr(collectors.time, "monotonic", lambda: hit[0] + 1)
    assert collectors._cached(("thread", BSKY_URL)) is None


def test_post_cap(monkeypatch):
    monkeypatch.setattr(base, "MAX_POSTS", 5)
    install(monkeypatch, bsky_routes())
    coll, _ = run(collectors.collect_url(BSKY_URL))
    assert len(coll.posts) == 5 and not coll.complete


# ----------------------------------------------------------------- Mastodon

def test_mastodon_thread(monkeypatch):
    install(monkeypatch, masto_routes())
    coll, _ = run(collectors.collect_url(MASTO_URL))
    assert len(coll.posts) == 9 and coll.requests == 2
    by = {p.author: p for p in coll.posts}
    assert "Project@mastodon.example" in by and "localfan@mastodon.example" in by     # local accounts get the server
    spam = [p for p in coll.posts if p.author.startswith("exodus_now")]
    assert len(spam) == 5 and spam[0].url == "https://better-network.example/join"
    assert spam[0].text.startswith("@Project This update is a disaster") and "#Exodus" in spam[0].text
    assert all(p.author_created_at for p in coll.posts)
    out = analyze(coll)
    assert out["clusters"][0]["verdict"] == "likely_coordinated" and out["clusters"][0]["author_count"] == 5


def test_mastodon_60_reply_cap_is_disclosed(monkeypatch):
    routes = masto_routes()
    ctx = fx("mastodon_context.json")
    many = []
    for k in range(60):
        st = copy.deepcopy(ctx["descendants"][-1])
        st["id"] = st["url"] = f"https://mastodon.example/@reader/{5000 + k}"
        many.append(st)
    routes[("mastodon.example", "/api/v1/statuses/999/context")] = lambda r: {"ancestors": [], "descendants": many}
    install(monkeypatch, routes)
    coll, _ = run(collectors.collect_url(MASTO_URL))
    assert not coll.complete and any("at most 60 replies" in n and "132" in n for n in coll.notes)


@pytest.mark.parametrize("status, phrase", [(401, "not logged in"), (403, "not logged in"), (404, "not found")])
def test_mastodon_status_errors(monkeypatch, status, phrase):
    install(monkeypatch, {("mastodon.example", "/api/v1/statuses/999"): lambda r: httpx2.Response(status, json={})})
    with pytest.raises(chttp.CollectorError) as exc:
        run(collectors.collect_url(MASTO_URL))
    assert phrase in exc.value.message


def test_mastodon_context_denied_keeps_root(monkeypatch):
    install(monkeypatch, masto_routes(context_status=401))
    coll, _ = run(collectors.collect_url(MASTO_URL))
    assert len(coll.posts) == 1 and any("could not be read" in n for n in coll.notes)


def test_mastodon_host_is_ssrf_checked(monkeypatch):
    """User-supplied server: a name resolving to a private address is never connected to."""
    async def private(host, port):
        return ["10.0.0.7"]
    monkeypatch.setattr(extract, "resolver", private)
    monkeypatch.setattr(chttp, "client_factory", extract.make_client)  # the real guarded client
    with pytest.raises(chttp.CollectorError) as exc:
        run(collectors.collect_url("https://evil.example/@a/123"))
    assert exc.value.status == 400 and "private" in exc.value.message
    with pytest.raises(chttp.CollectorError) as exc:
        run(collectors.collect_url("https://intranet.lan/@a/123"))
    assert exc.value.status == 400


# ------------------------------------------------------------------ YouTube

def yt_comment(cid, author, chan, text, when):
    return {"kind": "youtube#comment", "id": cid, "snippet": {
        "authorDisplayName": f"@{author}", "authorChannelId": {"value": chan}, "textOriginal": text,
        "textDisplay": text, "publishedAt": when, "updatedAt": when, "likeCount": 0}}


def yt_routes(fail=None):
    h = "www.googleapis.com"
    spam = "This channel is lying to you, the real story is at truth-tube.example/watch, share it everywhere now"

    def threads(req):
        assert req.url.params["key"] == "AIzaTEST" and req.url.params["videoId"] == "abcdefghijk"
        if fail:
            return httpx2.Response(403, json={"error": {"code": 403, "errors": [{"reason": fail}]}})
        if req.url.params.get("pageToken") == "P2":
            items = [{"id": f"T{k}", "snippet": {"topLevelComment": yt_comment(f"T{k}", f"viewer{k}", f"UCv{k}",
                     f"Organic comment number {k} about the camera work and the music choices in this video",
                     f"2026-11-10T1{k}:00:00Z"), "totalReplyCount": 0}} for k in range(3)]
            return {"items": items}
        items = [{"id": f"S{k}", "snippet": {"topLevelComment": yt_comment(f"S{k}", f"truthseeker{40 + k}", f"UCs{k}", spam,
                  f"2026-11-11T09:0{k}:00Z"), "totalReplyCount": 0}} for k in range(5)]
        items.append({"id": "R0", "snippet": {"topLevelComment": yt_comment("R0", "asker", "UCa", "Where was this filmed?",
                      "2026-11-10T08:00:00Z"), "totalReplyCount": 7},
                      "replies": {"comments": [yt_comment("R0.1", "helper", "UCh", "Looks like Lisbon to me.", "2026-11-10T08:05:00Z")]}})
        return {"items": items, "nextPageToken": "P2"}

    def channels(req):
        ids = req.url.params["id"].split(",")
        return {"items": [{"id": i, "snippet": {"publishedAt": "2026-11-01T00:00:00Z" if i.startswith("UCs")
                                                 else "2015-06-01T00:00:00Z"}} for i in ids]}
    return {(h, "/youtube/v3/videos"): lambda r: {"items": [{"id": "abcdefghijk", "snippet": {
                "title": "A travel video", "channelTitle": "Travel Channel", "channelId": "UCowner",
                "publishedAt": "2026-11-09T12:00:00Z", "description": "Filmed on a sunny day."},
                "statistics": {"commentCount": "9"}}]},
            (h, "/youtube/v3/commentThreads"): threads,
            (h, "/youtube/v3/comments"): lambda r: {"items": [yt_comment(f"R0.{k}", f"replier{k}", f"UCr{k}",
                                                                        f"Reply {k}: I think it is Porto actually",
                                                                        f"2026-11-10T09:0{k}:00Z") for k in range(2, 4)]},
            (h, "/youtube/v3/channels"): channels}


def test_youtube_not_configured_without_key():
    with pytest.raises(chttp.CollectorError) as exc:
        run(collectors.collect_url(YT_URL))
    assert exc.value.status == 503 and "not configured" in exc.value.message


def test_youtube_thread_pages_replies_and_channels(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "AIzaTEST")
    router = install(monkeypatch, yt_routes())
    coll, _ = run(collectors.collect_url(YT_URL))
    paths = [r.url.path.rsplit("/", 1)[-1] for r in router.requests]
    assert paths == ["videos", "commentThreads", "commentThreads", "comments", "channels"]
    assert len(coll.posts) == 12 and all(p.author_created_at for p in coll.posts)
    assert coll.root["author"] == "Travel Channel" and coll.root["replies"] == 9
    assert {p.author for p in coll.posts if p.author.startswith("truthseeker")} == {f"truthseeker{40 + k}" for k in range(5)}
    blob = json.dumps(coll.summary()) + " ".join(coll.notes)
    assert "AIzaTEST" not in blob
    top = analyze(coll)["clusters"][0]
    assert top["verdict"] == "likely_coordinated" and "A" in top["families"]   # channels created within 7 days


@pytest.mark.parametrize("reason, phrase, status", [("commentsDisabled", "turned off", 404), ("quotaExceeded", "quota", 503),
                                                    ("keyInvalid", "not configured correctly", 503)])
def test_youtube_errors(monkeypatch, reason, phrase, status):
    monkeypatch.setenv("GOOGLE_API_KEY", "AIzaTEST")
    install(monkeypatch, yt_routes(fail=reason))
    with pytest.raises(chttp.CollectorError) as exc:
        run(collectors.collect_url(YT_URL))
    assert phrase in exc.value.message and exc.value.status == status and "AIzaTEST" not in exc.value.message


# --------------------------------------------------------------- multi-link

def test_multi_links_single_posts_mixed_platforms(monkeypatch):
    routes = {**bsky_routes(), **masto_routes()}
    router = install(monkeypatch, routes)
    thread = fx("bluesky_thread.json")["thread"]
    links = [f"https://bsky.app/profile/{n['post']['author']['handle']}/post/{n['post']['uri'].rsplit('/', 1)[-1]}"
             for n in thread["replies"][:6]]
    links += [MASTO_URL, "https://x.com/a/status/1", "https://bsky.app/profile/ghost.bsky.social/post/3nothere"]
    coll, _ = run(collectors.collect_urls(links))
    assert len(coll.posts) == 7 and coll.platform == "mixed"
    reasons = {f["url"]: f["reason"] for f in coll.failed}
    assert "X (Twitter)" in reasons["https://x.com/a/status/1"]
    assert reasons["https://bsky.app/profile/ghost.bsky.social/post/3nothere"] == "Bluesky account not found"
    # handles resolved in one batch, posts in one batch, one Mastodon status: no thread expansion
    assert [r.url.path.rsplit("/", 1)[-1] for r in router.requests] == ["app.bsky.actor.getProfiles", "app.bsky.feed.getPosts", "999"]


def test_multi_links_limits():
    with pytest.raises(chttp.CollectorError, match="up to 50"):
        collectors.validate_urls([f"https://bsky.app/profile/a/post/{k}" for k in range(51)])
    with pytest.raises(chttp.CollectorError, match="None of the links"):
        collectors.validate_urls(["https://x.com/a/status/1", "https://example.com/x"])


# -------------------------------------------------------------- campaigns

def test_campaign_merging_by_author_overlap():
    def item(cid, authors, verdict="likely_coordinated", start=0.0):
        return {"id": cid, "authors": [{"author": a, "role": "amplifier", "posts_in_cluster": 1, "first_post": start,
                                        "name_pattern": False} for a in authors],
                "verdict": verdict, "verdict_before_innocent_check": verdict, "families": ["T", "S"], "author_count": len(authors),
                "post_count": len(authors), "_start": start, "_end": start + 60, "_posts": [], "_tags": set(), "_rep_norm": "",
                "_wc": [], "signals": {}, "representative_text": cid, "headline": f"h {cid}", "evidence_for": ["same line", f"own {cid}"],
                "evidence_against": [], "innocent_patterns": [], "innocent_explanations": [],
                "time_span": {"start": start, "end": start + 60, "seconds": 60}, "timeline": [{"t": start, "author": authors[0], "id": cid}]}
    a = item("a", ["u1", "u2", "u3", "u4"], start=100)
    b = item("b", ["u1", "u2", "u3", "u5"], verdict="worth_watching", start=50)    # Jaccard 3/5 = 0.6 -> merged
    c = item("c", ["u1", "x2", "x3", "x4"])                                         # 1/7 -> separate
    out = clusters.merge_campaigns([a, b, c])
    kinds = sorted(o["kind"] for o in out)
    assert kinds == ["campaign", "cluster"]
    camp = next(o for o in out if o["kind"] == "campaign")
    assert camp["verdict"] == "likely_coordinated" and camp["author_count"] == 5 and len(camp["variants"]) == 2
    assert camp["evidence_for"] == ["same line", "Message 1: own a", "Message 2: own b"]
    assert camp["authors"][0]["author"] == "u1" and camp["authors"][0]["posts_in_cluster"] == 2
    assert camp["headline"].startswith("5 accounts posted 2 different messages")


# -------------------------------------------------------------------- API

def test_api_link_mode_and_source(monkeypatch):
    install(monkeypatch, bsky_routes())
    r = client.post("/api/swarm", json={"url": BSKY_URL})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"]["platform"] == "bluesky" and body["source"]["posts"] == 17 and body["source"]["mode"] == "thread"
    assert body["clusters"][0]["verdict"] == "likely_coordinated"
    again = client.post("/api/swarm", json={"url": BSKY_URL}).json()
    assert again["source"]["cached"] is True and any("reused from memory" in n for n in again["notes"])


def test_api_stream_progress_then_result(monkeypatch):
    install(monkeypatch, bsky_routes())
    with client.stream("POST", "/api/swarm", json={"url": BSKY_URL, "stream": True}) as r:
        assert r.headers["content-type"].startswith("application/x-ndjson")
        lines = [json.loads(x) for x in r.iter_lines() if x.strip()]
    assert any("Fetched" in x.get("progress", "") for x in lines)
    assert "result" in lines[-1] and lines[-1]["result"]["summary"]["posts"] == 17


def test_api_stream_error_line(monkeypatch):
    install(monkeypatch, masto_routes())
    with client.stream("POST", "/api/swarm", json={"url": "https://mastodon.example/@Project/12345", "stream": True}) as r:
        lines = [json.loads(x) for x in r.iter_lines() if x.strip()]
    assert lines[-1]["error"]["status"] == 404


def test_api_closed_platform_does_not_consume_rate_limit():
    for _ in range(12):
        r = client.post("/api/swarm", json={"url": "https://x.com/a/status/1"})
        assert r.status_code == 400 and "X (Twitter)" in r.json()["detail"]
    assert client.post("/api/swarm", json={"urls": ["https://example.com/x"]}).status_code == 400


def test_api_links_share_the_swarm_rate_limit(monkeypatch):
    install(monkeypatch, masto_routes())
    hdr = {"X-Forwarded-For": "203.0.113.77"}
    for _ in range(5):
        assert client.post("/api/swarm", json={"url": MASTO_URL}, headers=hdr).status_code == 200
    csv = "id,author,timestamp,text\n1,a,2026-11-14T10:00:00Z,hello there\n"
    for _ in range(5):
        assert client.post("/api/swarm", json={"data": csv}, headers=hdr).status_code == 200
    assert client.post("/api/swarm", json={"url": MASTO_URL}, headers=hdr).status_code == 429


def test_api_link_errors_mapped(monkeypatch):
    install(monkeypatch, {("mastodon.example", "/api/v1/statuses/999"): lambda r: httpx2.Response(401, json={})})
    r = client.post("/api/swarm", json={"url": MASTO_URL})
    assert r.status_code == 403 and "not logged in" in r.json()["detail"]
    assert client.post("/api/swarm", json={"url": YT_URL}).status_code == 503
