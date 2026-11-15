"""Trace a post: fingerprint (LLM verified + rarity fallback), exact-phrase search (Oxylabs, Bluesky), verification
of every hit, the swarm detector on confirmed copies, syndication, receipts, limits and the API.
No live network: httpx2.MockTransport with responses in the recorded/documented shapes."""
import asyncio
import copy
import json
from pathlib import Path

import httpx2
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.receipts import service as receipts_service
from app.swarm import clusters, collectors
from app.swarm.collectors import http as chttp
from app.swarm.ingest import Post
from app.trace import fingerprint, search
from app.trace import run as trace_run
from fakellm import FakeLLM, install as install_llm

FIX = Path(__file__).parent / "fixtures" / "swarm"
client = TestClient(app)

TEXT = ("City hall quietly approved the harbour dredging contract on Tuesday. The Vaskelund consortium will receive "
        "41 million euros over six years without an open tender. Residents were told about it only after the vote. "
        "Thanks for reading and sharing.")
S1 = "The Vaskelund consortium will receive 41 million euros over six years without an open tender."
S2 = "City hall quietly approved the harbour dredging contract on Tuesday."
Q1, Q2 = fingerprint.query_phrase(S1), fingerprint.query_phrase(S2)
POST_TPL = json.loads((FIX / "bluesky_quotes.json").read_text(encoding="utf-8"))["posts"][0]


def bpost(n, handle, text, created, acc_created="2023-05-01T10:00:00.000Z"):
    p = copy.deepcopy(POST_TPL)
    p["uri"] = f"at://did:plc:trace{n:04d}/app.bsky.feed.post/3t{n:05d}"
    p["author"].update({"did": f"did:plc:trace{n:04d}", "handle": handle, "createdAt": acc_created})
    p["record"] = {"$type": "app.bsky.feed.post", "createdAt": created, "text": text, "langs": ["en"]}
    return p


def bsky_hits(campaign=False):
    if campaign:   # 6 look-alike accounts, created within days, posting the text within minutes
        hits = [bpost(k, f"harbourtruth{120 + 31 * k}.bsky.social", f"{TEXT} #HarbourGate",
                      f"2026-11-12T09:0{k}:00.000Z", f"2026-11-0{1 + k % 3}T00:00:00.000Z") for k in range(6)]
    else:
        hits = [bpost(1, "localreporter.bsky.social", f"Worth reading: {S1} Source: city hall minutes.",
                      "2026-11-11T16:00:00.000Z"),
                bpost(2, "resident.bsky.social", f"{S1} Why did nobody ask us?", "2026-11-11T18:30:00.000Z")]
    hits.append(bpost(9, "quoter.bsky.social", "Interesting thread about the port, see the quoted post.",
                      "2026-11-11T19:00:00.000Z"))  # quotes it, does not copy it
    return {"posts": hits, "hitsTotal": len(hits)}


def oxy_result(url, title, desc):
    return {"title": title, "description": desc, "url": url, "content": None, "source": "web"}


def oxy(query_text):
    if Q1 in query_text:
        results = [
            oxy_result("https://www.reuters.com/world/europe/harbour-dredging-contract-2026-11-11/",
                       "Harbour dredging contract approved | Reuters",
                       f"Nov 11, 2026 — {S1} The city said ..."),
            oxy_result("https://localnews-aggregator.example/harbour", "Harbour deal",
                       f"Nov 12, 2026 — {S1}"),
            oxy_result("https://blog.example.org/post/1", "Some blog", "We discuss the harbour plans of many cities ..."),
            oxy_result("https://undated.example/item", "Copy without a date", f"... {S1} ..."),
        ]
    else:
        results = []
    return {"run_id": "1", "status": "completed", "data": results}


class Router:
    def __init__(self, campaign=False, oxy_status=200, bsky_status=200):
        self.campaign, self.oxy_status, self.bsky_status = campaign, oxy_status, bsky_status
        self.requests = []

    def __call__(self, req: httpx2.Request):
        self.requests.append(req)
        if req.url.host == "api-aistudio.oxylabs.io":
            if self.oxy_status != 200:
                return httpx2.Response(self.oxy_status, json={"error": "unauthorized"})
            return httpx2.Response(200, json=oxy(json.loads(req.content)["query"]))
        if req.url.path.endswith("searchPosts"):
            if self.bsky_status != 200:
                return httpx2.Response(self.bsky_status, json={"error": "Forbidden"})
            q = req.url.params["q"]
            return httpx2.Response(200, json=bsky_hits(self.campaign) if (Q1 in q or self.campaign) else {"posts": []})
        return httpx2.Response(404, json={})


def install(monkeypatch, router, web=True):
    monkeypatch.setattr(chttp, "client_factory", lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(router)))
    if web:
        monkeypatch.setenv("SEARCH_PROVIDER", "oxylabs")
        monkeypatch.setenv("SEARCH_API_KEY", "oxy-test-key")

    async def no_rdap(target):  # receipts: offline lookalike + a fixed domain age
        return {"domain": target, "registrable_domain": target,
                "receipts": [{"type": "domain_age", "status": "found", "text": "Domain registered on 2025-01-01."}]}
    monkeypatch.setattr(receipts_service, "domain_receipts", no_rdap)
    return router


def run(coro):
    return asyncio.run(coro)


# -------------------------------------------------------------- fingerprint

def test_heuristic_prefers_rare_specific_sentences():
    picks = fingerprint.heuristic(TEXT)
    assert S1 in picks and "Thanks for reading and sharing." not in picks
    assert all(len(fingerprint.words(p)) >= 8 for p in picks)


def test_query_phrase_cleanup():
    long = " ".join(f"word{i}" for i in range(40)) + "."
    assert len(fingerprint.query_phrase(long).split()) == 24
    assert fingerprint.query_phrase('He said “this is a total disgrace for us all”!') == "He said this is a total disgrace for us all"


def test_llm_picks_are_verified(monkeypatch):
    install_llm(monkeypatch, FakeLLM(lambda u, s: json.dumps([S1, "A sentence that is not in the text at all, invented.",
                                                              "too short"])))
    picks, meta = run(fingerprint.pick(TEXT))
    assert picks[0] == S1 and meta["llm_calls"] == 1 and meta["method"] == "mixed"
    assert "invented" not in " ".join(picks)


def test_llm_failure_falls_back(monkeypatch):
    install_llm(monkeypatch, FakeLLM(fail=True))
    picks, meta = run(fingerprint.pick(TEXT))
    assert S1 in picks and meta["method"] == "heuristic" and "not available" in meta["note"]


def test_no_key_uses_heuristic():
    picks, meta = run(fingerprint.pick(TEXT))
    assert S1 in picks and meta["llm_calls"] == 0 and "not configured" in meta["note"]


# ------------------------------------------------------------ verification

def test_snippet_dates():
    assert search.snippet_date("Nov 11, 2026 — text") == pytest.approx(1794355200.0)
    assert search.snippet_date("3 days ago — text") is not None
    assert search.snippet_date("No date here") is None


def test_confirmation_rules():
    fps = [fingerprint.norm(Q1)]
    assert trace_run.confirm_text(fps, f"look: {S1.upper()}") == [0]
    assert trace_run.confirm_text(fps, "Interesting thread about the port") == []
    assert trace_run.confirm_snippet(fps, "... will receive 41 million euros over six years without ...") == [0]
    assert trace_run.confirm_snippet(fps, "We discuss the harbour plans of many cities") == []


def test_syndication_innocent_pattern():
    wire = Post("1", "reuters.com", 100.0, S1, url="https://www.reuters.com/world/x")
    copy_ = Post("2", "blog.example", 200.0, S1, url="https://blog.example/y")
    assert clusters.syndication([copy_, wire]) == "reuters.com, a news agency"
    assert clusters.syndication([Post("1", "a", 50.0, S1, url="https://blog.example/y"), wire]) is None  # not the origin
    assert clusters.syndication([Post("1", "x", 1.0, "Press release https://www.prnewswire.com/news/abc")]).endswith("press-release service")


# ------------------------------------------------------------ end to end

def test_trace_open_web_and_bluesky(monkeypatch):
    router = install(monkeypatch, Router())
    progress = []
    r = run(trace_run.trace(text=TEXT, progress=progress.append))
    cov = {c["source"].split(" ")[0]: c for c in r["coverage"]}
    assert cov["Open"]["status"] == "searched" and cov["Bluesky"]["status"] == "searched"
    assert r["cost"]["searches"] <= trace_run.MAX_SEARCHES
    sp = r["spread"]
    # web: reuters + aggregator (dated) + undated copy confirmed; blog not confirmed; bluesky: 2 copies, 1 quote
    assert sp["copies"] == 5 and sp["unconfirmed"] == 1 and sp["related_not_copies"] == 1
    assert sp["first_seen"]["who"] == "reuters.com"
    assert [d["domain"] for d in sp["domains"]][:2] == ["reuters.com", "localnews-aggregator.example"]
    assert sp["domains"][0]["receipts"][0]["type"] == "domain_age"
    assert {a["account"] for a in sp["accounts"]} == {"localreporter.bsky.social", "resident.bsky.social"}
    assert "syndication" in r["cluster"]["innocent_patterns"] and r["verdict"] != "likely_coordinated"
    assert not any("meaning-level" in x for x in r["cluster"]["evidence_against"])   # exact copies, not paraphrase
    assert [t["who"] for t in sp["timeline"]][0] == "reuters.com"
    assert any("no date in the search results" in n for n in r["notes"])
    assert r["principle"] == "We trace where this text spread. We don't research who is behind any account."
    oxy_reqs = [q for q in router.requests if q.url.host == "api-aistudio.oxylabs.io"]
    assert oxy_reqs and all(q.headers["x-api-key"] == "oxy-test-key" for q in oxy_reqs)
    assert json.loads(oxy_reqs[0].content)["query"].startswith('"') and json.loads(oxy_reqs[0].content)["limit"] == 10
    # no profile lookups, no identity research: only search endpoints were called
    assert {q.url.path for q in router.requests} <= {"/search/instant", "/xrpc/app.bsky.feed.searchPosts"}
    assert "oxy-test-key" not in json.dumps(r)
    assert progress


def test_trace_campaign_copies_are_flagged(monkeypatch):
    install(monkeypatch, Router(campaign=True), web=False)
    r = run(trace_run.trace(text=TEXT))
    assert r["verdict"] == "likely_coordinated"
    assert {"T", "S", "A"} <= set(r["cluster"]["families"])
    assert r["cluster"]["headline"].startswith("6 accounts posted the traced text")


def test_trace_web_not_configured_and_sources_failing(monkeypatch):
    install(monkeypatch, Router(bsky_status=403), web=False)
    r = run(trace_run.trace(text=TEXT))
    cov = {c["source"].split(" ")[0]: c for c in r["coverage"]}
    assert cov["Open"]["status"] == "not_configured" and cov["Bluesky"]["status"] == "failed"
    assert r["spread"]["copies"] == 0 and r["verdict"] is None
    install(monkeypatch, Router(oxy_status=401))
    r = run(trace_run.trace(text=TEXT))
    cov = {c["source"].split(" ")[0]: c for c in r["coverage"]}
    assert cov["Open"]["status"] == "failed" and "not configured correctly" in cov["Open"]["note"]


def test_trace_from_url_stores_nothing(monkeypatch):
    thread = json.loads((FIX / "bluesky_thread.json").read_text(encoding="utf-8"))["thread"]
    node = thread["replies"][0]["post"]
    node = copy.deepcopy(node)
    node["record"]["text"] = TEXT
    base_router = Router()

    def handler(req):
        if req.url.path.endswith("getProfiles"):
            return httpx2.Response(200, json={"profiles": [{"did": node["author"]["did"], "handle": node["author"]["handle"]}]})
        if req.url.path.endswith("getPosts"):
            return httpx2.Response(200, json={"posts": [node]})
        return base_router(req)
    install(monkeypatch, handler)
    url = f"https://bsky.app/profile/{node['author']['handle']}/post/{node['uri'].rsplit('/', 1)[-1]}"
    r = run(trace_run.trace(url=url))
    assert r["source"]["platform"] == "bluesky" and r["spread"]["copies"] >= 3
    assert collectors._cache == {}   # nothing kept from a trace


# ---------------------------------------------------------------------- API

def test_api_trace_validation_and_rate_limit(monkeypatch):
    assert client.post("/api/trace", json={"text": "too short"}).status_code == 400
    r = client.post("/api/trace", json={"url": "https://x.com/a/status/1"})
    assert r.status_code == 400 and "X (Twitter)" in r.json()["detail"]
    install(monkeypatch, Router(), web=False)
    hdr = {"X-Forwarded-For": "203.0.113.90"}
    for _ in range(5):
        assert client.post("/api/trace", json={"text": TEXT}, headers=hdr).status_code == 200
    r = client.post("/api/trace", json={"text": TEXT}, headers=hdr)
    assert r.status_code == 429 and "5 traces per hour" in r.json()["detail"]


def test_api_trace_stream_with_llm_fingerprint(monkeypatch):
    install(monkeypatch, Router())
    fake = install_llm(monkeypatch, FakeLLM(lambda u, s: json.dumps([S1, S2])))
    with client.stream("POST", "/api/trace", json={"text": TEXT, "stream": True}) as r:
        lines = [json.loads(x) for x in r.iter_lines() if x.strip()]
    assert any("Searched" in x.get("progress", "") for x in lines)
    res = lines[-1]["result"]
    assert res["fingerprint_method"] == "llm" and res["cost"]["llm_calls"] == 1 and len(fake.calls) == 1
    assert [f["sentence"] for f in res["fingerprints"]] == [S1, S2]


def test_trace_page_served():
    page = client.get("/trace").text
    assert "We don't research who is behind any account" in page
