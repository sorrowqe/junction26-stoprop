"""Tests for Oxylabs AI Studio scraping fallback, Brave Search API, snippet-backed claim verification,
social post URL shortcut, SSRF guards before proxy scraping, WHOIS fallback (.ee), and syndication receipts."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import httpcore2
import httpx2
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.pipeline import extract, oxylabs, websearch
from app.receipts import rdap, syndication
from app.receipts import http as rhttp
from app.swarm.collectors import http as chttp
from app.trace import run as trace_run
from app.verify import run as vrun
from fakellm import FakeLLM, install as install_llm

client = TestClient(app)
PUBLIC_IP = "93.184.216.34"
NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)

LONG_MD = (
    "# Harbourton Council Approves Emergency Port Plan\n\n"
    "Skip to main content\n\n"
    "Home | World | Politics | Economy | Opinion\n\n"
    "The **Harbourton** city council voted [late Tuesday](https://example.com/vote) to approve an emergency "
    "harbour dredging contract worth forty-one million euros over six years without holding an open public tender. "
    "Opposition members called the rushed vote an unacceptable giveaway while port officials said winter silting "
    "left no time for a six-month procurement round.\n"
)


def run(coro):
    return asyncio.run(coro)


def test_markdown_to_text_preserves_verbatim_words():
    text, title = oxylabs.markdown_to_text(LONG_MD)
    assert title == "Harbourton Council Approves Emergency Port Plan"
    assert "Skip to main content" not in text
    assert "Home | World | Politics" not in text
    assert "The Harbourton city council voted late Tuesday to approve an emergency" in text
    assert "https://example.com/vote" not in text
    _, nested_title = oxylabs.markdown_to_text("# [![Photo](https://img.example/x.jpg) Real Headline Text](https://example.com/a)\nBody")
    assert nested_title == "Real Headline Text"


def test_oxylabs_scrape_polling_cache_and_daily_cap(monkeypatch):
    monkeypatch.setenv("SEARCH_PROVIDER", "oxylabs")
    monkeypatch.setenv("SEARCH_API_KEY", "oxy-secret-key")
    monkeypatch.setenv("OXYLABS_DAILY_CAP", "1")
    monkeypatch.setattr(oxylabs, "POLL_INTERVAL_S", 0.01)
    calls = []
    polls = [0]

    def handler(req: httpx2.Request):
        calls.append((req.method, str(req.url), req.headers.get("x-api-key")))
        if req.url.path == "/scrape":
            return httpx2.Response(200, json={"run_id": "job-42"})
        if req.url.path == "/scrape/run/data":
            polls[0] += 1
            if polls[0] == 1:
                return httpx2.Response(202, json={"status": "running"})
            return httpx2.Response(200, json={"status": "completed", "error_code": None, "data": LONG_MD})
        return httpx2.Response(404)

    monkeypatch.setattr(oxylabs, "client_factory", lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))
    text1, title1 = run(oxylabs.scrape_markdown("https://protected.example/story"))
    assert "forty-one million euros" in text1 and title1.startswith("Harbourton")
    assert all(k == "oxy-secret-key" for _, _, k in calls)
    # Second call for same URL comes from 6h SQLite cache -> 0 new HTTP requests
    n_before = len(calls)
    text2, _ = run(oxylabs.scrape_markdown("https://protected.example/story"))
    assert text2 == text1 and len(calls) == n_before
    # Different URL hits daily cap (1) -> raises cap error without touching network
    with pytest.raises(oxylabs.OxylabsError) as exc:
        run(oxylabs.scrape_markdown("https://protected.example/other"))
    assert exc.value.code == "cap" and len(calls) == n_before


def test_extract_falls_back_to_oxylabs_on_403_but_blocks_ssrf(monkeypatch):
    monkeypatch.setenv("SEARCH_PROVIDER", "oxylabs")
    monkeypatch.setenv("SEARCH_API_KEY", "oxy-secret-key")
    monkeypatch.setattr(oxylabs, "POLL_INTERVAL_S", 0.01)
    oxy_urls = []

    def oxy_handler(req: httpx2.Request):
        if req.url.path == "/scrape":
            oxy_urls.append(json.loads(req.content)["url"])
            return httpx2.Response(200, json={"run_id": "j1"})
        return httpx2.Response(200, json={"status": "completed", "data": LONG_MD})

    monkeypatch.setattr(oxylabs, "client_factory", lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(oxy_handler)))

    dns = {"blocked-news.example": [PUBLIC_IP], "rebind-private.example": ["10.0.0.8"]}

    async def resolver(host, port):
        return dns[host]

    class Block403Net(httpcore2.AsyncNetworkBackend):
        async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
            raw = b"HTTP/1.1 403 Forbidden\r\nContent-Type: text/html\r\nContent-Length: 9\r\n\r\nForbidden"
            return httpcore2.AsyncMockStream([raw])

        async def sleep(self, seconds):
            await asyncio.sleep(seconds)

    monkeypatch.setattr(extract, "resolver", resolver)
    monkeypatch.setattr(extract, "client_factory", lambda: extract.make_client(inner_backend=Block403Net()))

    art = run(extract.fetch_article("https://blocked-news.example/article"))
    assert art.via == "oxylabs" and "forty-one million euros" in art.text
    assert oxy_urls == ["https://blocked-news.example/article"]

    # Private DNS target must NEVER reach Oxylabs
    with pytest.raises(extract.FetchError) as exc:
        run(extract.fetch_article("https://rebind-private.example/secret"))
    assert exc.value.code == "blocked"
    assert oxy_urls == ["https://blocked-news.example/article"]


def test_social_url_shortcut_and_closed_platform_in_extract(monkeypatch):
    def handler(req: httpx2.Request):
        if req.url.path.endswith("getPosts"):
            return httpx2.Response(200, json={"posts": [{
                "uri": "at://did:plc:abc/app.bsky.feed.post/3xyz",
                "cid": "cid1",
                "author": {"did": "did:plc:abc", "handle": "reporter.bsky.social", "createdAt": "2024-01-01T00:00:00Z"},
                "record": {"text": "Breaking: city council approves the emergency port contract tonight.",
                           "createdAt": "2026-11-14T09:30:00Z"},
            }]})
        if req.url.path.endswith("getProfiles"):
            return httpx2.Response(200, json={"profiles": [{"did": "did:plc:abc", "handle": "reporter.bsky.social"}]})
        return httpx2.Response(404)

    monkeypatch.setattr(chttp, "client_factory", lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))
    art = run(extract.fetch_article("https://bsky.app/profile/reporter.bsky.social/post/3xyz"))
    assert art.via == "social_api" and "emergency port contract" in art.text
    assert art.sitename == "Bluesky" and "reporter.bsky.social" in (art.title or "")

    with pytest.raises(extract.FetchError) as exc:
        run(extract.fetch_article("https://x.com/someone/status/123456"))
    assert exc.value.code == "closed_platform" and "X (Twitter)" in exc.value.message


def test_verify_with_brave_extra_snippets(monkeypatch):
    monkeypatch.setenv("SEARCH_PROVIDER", "oxylabs")  # BRAVE_API_KEY automatically upgrades search to Brave
    monkeypatch.setenv("BRAVE_API_KEY", "bsa-test-key")
    monkeypatch.setenv("SEARCH_API_KEY", "oxy-test-key")
    monkeypatch.setattr(websearch, "BRAVE_MIN_INTERVAL_S", 0.0)

    claim = {"claim": "Estonia opened a new cyber training centre in Tartu.",
             "entities": ["Estonia", "Tartu", "cyber"],
             "query": "Estonia Tartu cyber training centre",
             "cited_source": "ERR", "cited_domain": "err.ee",
             "attribution": "according to ERR"}

    def search_handler(req: httpx2.Request):
        assert req.url.host == "api.search.brave.com"
        assert req.headers.get("x-subscription-token") == "bsa-test-key"
        q = req.url.params.get("q", "")
        if "site:err.ee" in q:
            return httpx2.Response(200, json={"results": [{
                "url": "https://news.err.ee/999/cyber-tartu",
                "title": "Estonia opens cyber range in Tartu",
                "description": "Officials inaugurated the facility on Thursday.",
                "page_age": "2026-11-13T10:00:00",
                "extra_snippets": ["Estonia opened a new cyber training centre in Tartu on Thursday morning."],
            }]})
        return httpx2.Response(200, json={"results": [{
            "url": "https://www.reuters.com/world/europe/estonia-cyber-2026-11-13/",
            "title": "Baltic cyber defence expands",
            "description": "Estonia opened a new cyber training centre in Tartu, Reuters reported.",
            "page_age": "2026-11-13T12:00:00",
            "extra_snippets": [],
        }]})

    monkeypatch.setattr(chttp, "client_factory", lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(search_handler)))
    monkeypatch.setenv("OXYLABS_DAILY_CAP", "0")

    async def blocked_fetch(_url):
        raise extract.FetchError("http_status", "HTTP 403", 502)

    monkeypatch.setattr(extract, "fetch_article", blocked_fetch)
    install_llm(monkeypatch, FakeLLM(lambda user, _s: json.dumps(
        [{"article": 1, "stance": "supports", "quote": "Estonia opened a new cyber training centre in Tartu"},
         {"article": 2, "stance": "supports", "quote": "Estonia opened a new cyber training centre in Tartu"}]
        if user.startswith("Claim:") else [claim]
    )))

    res = run(vrun.verify("Estonia opened a new cyber training centre in Tartu according to ERR."))
    c = res["claims"][0]
    assert c["verdict"] == "corroborated" and c["independent_outlets"] == 1
    assert "bsa-test-key" not in json.dumps(res)


def test_brave_error_falls_back_to_oxylabs_instant_search(monkeypatch):
    monkeypatch.setenv("SEARCH_PROVIDER", "auto")
    monkeypatch.setenv("BRAVE_API_KEY", "bsa-broken")
    monkeypatch.setenv("SEARCH_API_KEY", "oxy-backup")
    monkeypatch.setattr(websearch, "BRAVE_MIN_INTERVAL_S", 0.0)

    def handler(req: httpx2.Request):
        if req.url.host == "api.search.brave.com":
            return httpx2.Response(503, json={"error": "down"})
        if req.url.host == "api-aistudio.oxylabs.io" and req.url.path == "/search/instant":
            return httpx2.Response(200, json={"status": "completed", "data": [{
                "url": "https://news.err.ee/123/test",
                "title": "Fallback result",
                "description": "Nov 13, 2026 — Fallback worked cleanly.",
                "content": None,
            }]})
        return httpx2.Response(404)

    monkeypatch.setattr(chttp, "client_factory", lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))

    async def go():
        async with chttp.Session() as s:
            return await websearch.search_web(s, "Estonia", kind="news", days=7, limit=5)

    hits = run(go())
    assert len(hits) == 1 and hits[0]["domain"] == "err.ee" and hits[0]["ts"] is not None


def test_whois_date_parsing_and_rdap_fallback_for_ee(monkeypatch):
    ee_whois = "Domain:\nname:       err.ee\nregistered: 2010-07-04 04:34:46 +03:00\nchanged:    2024-01-01\n"
    dt = rdap.parse_whois_date(ee_whois)
    assert dt is not None and dt.year == 2010 and dt.month == 7 and dt.day == 4

    fi_whois = "domain.............: yle.fi\ncreated............: 15.03.1996 00:00:00\n"
    dt2 = rdap.parse_whois_date(fi_whois)
    assert dt2 is not None and dt2.year == 1996 and dt2.month == 3 and dt2.day == 15

    monkeypatch.setattr(rhttp, "client_factory",
                        lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(lambda _r: httpx2.Response(404))))

    async def fake_whois(domain: str):
        return dt if domain == "err.ee" else None

    monkeypatch.setattr(rdap, "whois_lookup", fake_whois)
    res = run(rdap.check("err.ee", now=NOW))
    assert res["status"] == "found" and "2010-07-04" in res["text"] and res["details"]["source"] == "whois"


def test_syndication_receipt_and_health_status(monkeypatch):
    monkeypatch.setenv("SEARCH_PROVIDER", "auto")
    monkeypatch.setenv("BRAVE_API_KEY", "bsa-key")
    monkeypatch.setenv("SEARCH_API_KEY", "oxy-key")
    monkeypatch.setattr(websearch, "BRAVE_MIN_INTERVAL_S", 0.0)

    phrase = "The Harbourton city council approved forty-one million euros without an open tender"

    def handler(req: httpx2.Request):
        return httpx2.Response(200, json={"web": {"results": [
            {"url": "https://origin.example/story", "title": "Origin", "description": phrase},
            {"url": "https://mirror1.example/copy", "title": "Copy 1", "description": f"Yesterday: {phrase}."},
            {"url": "https://mirror2.org/post", "title": "Copy 2", "description": phrase},
        ]}})

    monkeypatch.setattr(chttp, "client_factory", lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))
    syn = run(syndication.check("origin.example", phrase))
    assert syn["status"] == "found" and syn["details"]["count"] == 2
    assert "mirror1.example" in syn["text"]

    h = client.get("/api/health").json()
    assert h["search"] == {"provider": "brave", "configured": True}
    assert h["scraper"]["configured"] is True and h["scraper"]["daily_cap"] == 150
    assert "bsa-key" not in json.dumps(h) and "oxy-key" not in json.dumps(h)


def test_telegram_cross_channel_and_reddit_and_hn_collectors(monkeypatch):
    msg = "The emergency harbour contract was secretly signed tonight without a vote or public tender."

    def tg_html(chan: str, minute: int) -> str:
        return (
            f'<div class="tgme_widget_message" data-post="{chan}/101">'
            f'<div class="tgme_widget_message_text">{msg} <a href="https://shady-leak.example/doc">leak</a></div>'
            f'<time datetime="2026-11-14T10:0{minute}:00+00:00"></time></div>'
        )

    reddit_atom = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom"><title>Thread</title>'
        '<entry><id>t3_abc12</id><author><name>/u/op_user</name></author>'
        '<updated>2026-11-14T09:00:00+00:00</updated><title>Harbour discussion</title>'
        '<link href="https://www.reddit.com/r/europe/comments/abc12/x/"/>'
        '<content type="html">&lt;p&gt;What do people think about the port contract?&lt;/p&gt;</content></entry>'
        + "".join(
            f'<entry><id>t1_c{k}</id><author><name>/u/portbot{100 + k}</name></author>'
            f'<updated>2026-11-14T09:0{k}:00+00:00</updated><title>reply</title>'
            f'<link href="https://www.reddit.com/r/europe/comments/abc12/x/c{k}/"/>'
            f'<content type="html">&lt;p&gt;{msg}&lt;/p&gt;</content></entry>'
            for k in range(1, 5)
        )
        + "</feed>"
    )

    def handler(req: httpx2.Request):
        if req.url.host == "t.me":
            chan = req.url.path.rsplit("/", 1)[-1]
            idx = {"chan_alpha": 1, "chan_beta": 2, "chan_gamma": 3}.get(chan, 4)
            return httpx2.Response(200, text=tg_html(chan, idx), headers={"Content-Type": "text/html"})
        if req.url.host == "www.reddit.com":
            return httpx2.Response(200, text=reddit_atom, headers={"Content-Type": "application/atom+xml"})
        if req.url.host == "hn.algolia.com" and req.url.path == "/api/v1/items/999":
            return httpx2.Response(200, json={
                "id": 999, "author": "founder", "title": "Show HN", "created_at_i": 1794650000,
                "children": [{"id": 1001, "author": "commenter1", "text": "<p>Great tool.</p>", "created_at_i": 1794650100}],
            })
        return httpx2.Response(404)

    monkeypatch.setattr(chttp, "client_factory", lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))

    r_tg = client.post("/api/swarm", json={"urls": ["https://t.me/chan_alpha", "https://t.me/chan_beta", "https://t.me/chan_gamma"]})
    assert r_tg.status_code == 200 and r_tg.json()["clusters"][0]["author_count"] == 3

    r_rd = client.post("/api/swarm", json={"url": "https://www.reddit.com/r/europe/comments/abc12/harbour/"})
    assert r_rd.status_code == 200 and r_rd.json()["summary"]["posts"] == 5
    from app.swarm.collectors import reddit
    with pytest.raises(chttp.CollectorError):
        reddit.parse_atom_feed('<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><feed/>')

    r_hn = client.post("/api/swarm", json={"url": "https://news.ycombinator.com/item?id=999"})
    assert r_hn.status_code == 200 and r_hn.json()["summary"]["posts"] == 2


def test_trace_redirect_short_query_and_multi_platform_spread(monkeypatch):
    redir = client.get("/trace", follow_redirects=False)
    assert redir.status_code == 302 and redir.headers["location"] == "/swarm"

    monkeypatch.setenv("SEARCH_PROVIDER", "brave")
    monkeypatch.setenv("BRAVE_API_KEY", "bsa-key")
    monkeypatch.setattr(websearch, "BRAVE_MIN_INTERVAL_S", 0.0)

    def handler(req: httpx2.Request):
        if req.url.host == "api.search.brave.com":
            q = req.url.params.get("q", "")
            if "site:reddit.com" in q:
                return httpx2.Response(200, json={"web": {"results": [{
                    "url": "https://www.reddit.com/r/europe/comments/r1/drone_radar/",
                    "title": "Estonia drone radar deployment announced",
                    "description": "Discussion on Estonia drone radar deployment.",
                    "page_age": "2026-11-14T08:10:00",
                }]}})
            if "site:t.me" in q:
                return httpx2.Response(200, json={"web": {"results": [{
                    "url": "https://t.me/balticnews/77",
                    "title": "Baltic News Telegram",
                    "description": "Estonia drone radar deployment announced on Tuesday.",
                    "page_age": "2026-11-14T08:15:00",
                }]}})
            return httpx2.Response(200, json={"web": {"results": [{
                "url": "https://clone-news.example/p", "title": "Estonia drone radar report",
                "description": "Estonia drone radar deployment announced on Tuesday.",
                "page_age": "2026-11-14T08:00:00",
            }]}})
        if req.url.host == "api.bsky.app":
            return httpx2.Response(200, json={"posts": []})
        if req.url.host == "hn.algolia.com":
            return httpx2.Response(200, json={"hits": [{
                "objectID": "555", "author": "techreader", "created_at": "2026-11-14T08:30:00Z",
                "comment_text": "Discussion of the Estonia drone radar deployment announced today.",
            }]})
        return httpx2.Response(404)

    monkeypatch.setattr(chttp, "client_factory", lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))

    async def no_rdap(target):
        return {"domain": target, "registrable_domain": target, "receipts": []}

    from app.receipts import service as receipts_service
    monkeypatch.setattr(receipts_service, "domain_receipts", no_rdap)

    r = client.post("/api/trace", json={"text": "Estonia drone radar", "include_forums": True})
    assert r.status_code == 200
    body = r.json()
    assert body["fingerprint_method"] == "query" and body["cost"]["llm_calls"] == 0
    assert body["spread"]["copies"] == 4  # web + reddit + telegram + hackernews
    assert body["spread"]["activity"]["peak"] >= 1 and len(body["spread"]["activity"]["bins"]) == 20
    assert body["spread"]["timeline"][0]["snippet"]
    cov = {c["source"].split(" ")[0]: c for c in body["coverage"]}
    assert cov["Reddit"]["confirmed"] == 1 and cov["Telegram"]["confirmed"] == 1 and cov["Hacker"]["confirmed"] == 1


def test_swarm_activity_histogram_and_burst_timeline_telemetry():
    csv_path = client.get("/static/swarm_sample.csv")
    r = client.post("/api/swarm", json={"data": csv_path.text})
    assert r.status_code == 200
    body = r.json()
    act = body["summary"]["activity"]
    assert act is not None and len(act["bins"]) == 24 and act["peak_posts"] >= 5
    assert sum(b["total"] for b in act["bins"]) == body["summary"]["posts"]
    assert any(b["likely"] > 0 for b in act["bins"])
    likely = next(c for c in body["clusters"] if c["verdict"] == "likely_coordinated")
    assert likely["burst_windows"] and likely["burst_windows"][0]["posts"] >= 3
    pt0 = likely["timeline"][0]
    assert "snippet" in pt0 and "in_burst" in pt0 and "name_pattern" in pt0


def test_source_upgrades_certspotter_watchdogs_context_and_pagecheck(monkeypatch):
    from app.receipts import crtsh, factcheck, lookalike, pagecheck

    # 1. Dictionary false-positive fix + satire + state-media context
    assert lookalike.check("lemonade.com")["status"] == "not_found"
    sat = lookalike.check("theonion.com")
    assert sat["flag"] == "warning" and "satire" in sat["text"].lower()
    st = lookalike.check("rt.com")
    assert st["flag"] == "warning" and "state-funded" in st["text"].lower()

    # 2. CertSpotter fallback when crt.sh fails with 503
    def ct_handler(req: httpx2.Request):
        if req.url.host == "crt.sh":
            return httpx2.Response(503)
        if req.url.host == "api.certspotter.com":
            return httpx2.Response(200, json=[{"not_before": "2024-05-01T00:00:00Z"}, {"not_before": "2025-01-10T00:00:00Z"}])
        return httpx2.Response(404)

    monkeypatch.setattr(rhttp, "client_factory", lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(ct_handler)))
    ct_res = run(crtsh.check("err.ee", now=NOW))
    assert ct_res["status"] == "found" and ct_res["details"]["source"] == "certspotter" and "2024-05-01" in ct_res["text"]

    # 3. Brave watchdog & IFCN fact-check fallback (EUvsDisinfo + Propastop)
    monkeypatch.setenv("SEARCH_PROVIDER", "brave")
    monkeypatch.setenv("BRAVE_API_KEY", "bsa-key")
    monkeypatch.setattr(websearch, "BRAVE_MIN_INTERVAL_S", 0.0)

    def brave_fc(req: httpx2.Request):
        return httpx2.Response(200, json={"web": {"results": [
            {"url": "https://euvsdisinfo.eu/report/example/", "title": "Disinfo: Fabricated port claim", "description": "Debunked."},
            {"url": "https://www.propastop.org/2026/clone-check/", "title": "Clone site network exposed", "description": "Analysis."},
        ]}})

    monkeypatch.setattr(chttp, "client_factory", lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(brave_fc)))
    fc_res = run(factcheck.check("rt.com"))
    assert fc_res["status"] == "found" and fc_res["flag"] == "neutral" and len(fc_res["details"]["items"]) == 2
    assert fc_res["details"]["items"][1]["publisher"] == "Propastop (Estonia)"

    # 4. Live page transparency & brand impersonation check
    impersonator_html = (
        '<html><head><title>Breaking Story | ERR</title>'
        '<meta property="og:site_name" content="ERR">'
        '<meta name="author" content="Jane Doe">'
        '<meta property="article:published_time" content="2026-11-12T10:00:00Z"></head>'
        '<body><a href="/about">About us</a><a href="mailto:ed@example.org">Contact</a>'
        '<article><p>The Harbourton municipal council approved a forty-one million euro port dredging contract '
        'without holding a public tender on Tuesday evening, documents show.</p></article></body></html>'
    )
    info = pagecheck.analyze_html(impersonator_html, "https://baltic-daily24.org/news/port-deal",
                                  "https://baltic-daily24.org/news/port-deal", [])
    assert info["is_article"] is True and info["author"] == "Jane Doe" and info["published_date"] == "2026-11-12"
    assert info["signals"]["about_link"] is True and info["signals"]["contact_link"] is True and info["signals"]["policy_link"] is False
    assert info["brand_warning"] is not None and "err.ee" in info["brand_warning"]["official_domain"]
    assert info["sample_quote"] and "forty-one million" in info["sample_quote"]
