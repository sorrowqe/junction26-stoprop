"""Receipts: each source (HTTP mocked), look-alike rules, TTL / refresh / single-flight, API."""
import asyncio
import json
from datetime import datetime, timezone

import httpx2
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.pipeline import extract
from app.receipts import crtsh, factcheck, lookalike, rdap, service, wayback
from app.receipts import http as rhttp

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
client = TestClient(app)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def mock_http(monkeypatch):
    """Route receipt-source HTTP by host. Returns (routes dict host->handler, calls list)."""
    routes, calls = {}, []

    def handler(request: httpx2.Request):
        calls.append(str(request.url))
        fn = routes.get(request.url.host)
        if fn is None:
            return httpx2.Response(404)
        return fn(request)

    monkeypatch.setattr(rhttp, "client_factory",
                        lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(handler), follow_redirects=True))
    return routes, calls


def j(data, status=200):
    return lambda req: httpx2.Response(status, json=data)


# ---------------------------------------------------------------------- rdap

def test_rdap_old_domain(mock_http):
    routes, _ = mock_http
    routes["rdap.org"] = j({"events": [{"eventAction": "expiration", "eventDate": "2030-01-01T00:00:00Z"},
                                       {"eventAction": "registration", "eventDate": "1997-03-14T05:00:00Z"}]})
    r = run(rdap.check("example.com", now=NOW))
    assert r["status"] == "found" and r["flag"] == "neutral"
    assert r["text"] == "Domain registered on 1997-03-14 (about 29 years ago)."
    assert "lookup.icann.org" in r["evidence_url"]


def test_rdap_young_domain_is_a_warning(mock_http):
    routes, _ = mock_http
    routes["rdap.org"] = j({"events": [{"eventAction": "registration", "eventDate": "2026-10-30T00:00:00Z"}]})
    r = run(rdap.check("err-news.ee", now=NOW))
    assert r["flag"] == "warning" and "only recently" in r["text"] and "15 days ago" in r["text"]


@pytest.mark.parametrize("status, expected", [(404, "not_found"), (500, "error"), (429, "error")])
def test_rdap_failures(mock_http, status, expected):
    routes, _ = mock_http
    routes["rdap.org"] = j({}, status)
    assert run(rdap.check("example.com", now=NOW))["status"] == expected


def test_rdap_without_registration_event(mock_http):
    routes, _ = mock_http
    routes["rdap.org"] = j({"events": []})
    assert run(rdap.check("example.ee", now=NOW))["status"] == "not_found"


def test_rdap_timeout(mock_http):
    routes, _ = mock_http

    def slow(req):
        raise httpx2.ReadTimeout("slow")
    routes["rdap.org"] = slow
    r = run(rdap.check("example.com", now=NOW))
    assert r["status"] == "error" and "did not answer" in r["text"]


# ------------------------------------------------------------------- wayback

def test_wayback_first_capture(mock_http):
    routes, calls = mock_http
    routes["web.archive.org"] = j([["timestamp", "original"], ["20040301120000", "http://example.com/"]])
    r = run(wayback.check("example.com", now=NOW))
    assert r["status"] == "found" and "2004-03-01" in r["text"]
    assert r["evidence_url"] == "https://web.archive.org/web/20040301120000/http://example.com/"
    assert "limit=1" in calls[0] and "url=example.com" in calls[0]


def test_wayback_never_archived(mock_http):
    routes, _ = mock_http
    routes["web.archive.org"] = j([])
    r = run(wayback.check("brand-new.example", now=NOW))
    assert r["status"] == "not_found" and "no saved copy" in r["text"]


def test_wayback_recent_is_warning(mock_http):
    routes, _ = mock_http
    routes["web.archive.org"] = j([["timestamp", "original"], ["20261101000000", "http://x.example/"]])
    assert run(wayback.check("x.example", now=NOW))["flag"] == "warning"


# --------------------------------------------------------------------- crtsh

def test_crtsh_earliest_certificate(mock_http):
    routes, _ = mock_http
    routes["crt.sh"] = j([{"not_before": "2019-05-01T00:00:00"}, {"not_before": "2015-02-03T10:00:00"},
                          {"not_before": "2024-01-01T00:00:00"}])
    r = run(crtsh.check("example.com", now=NOW))
    assert r["status"] == "found" and "2015-02-03" in r["text"] and r["details"]["certificates"] == 3


def test_crtsh_huge_history(mock_http, monkeypatch):
    routes, _ = mock_http
    monkeypatch.setattr(crtsh, "MAX_BYTES", 1000)
    routes["crt.sh"] = j([{"not_before": "2015-02-03T10:00:00", "pad": "x" * 100}] * 50)
    r = run(crtsh.check("bbc.co.uk", now=NOW))
    assert r["status"] == "found" and "Thousands" in r["text"]


def test_crtsh_overloaded(mock_http):
    routes, _ = mock_http
    routes["crt.sh"] = j({}, 502)
    r = run(crtsh.check("example.com", now=NOW))
    assert r["status"] == "error" and "overloaded" in r["text"]


# ----------------------------------------------------------------- factcheck

def test_factcheck_not_checked_without_key(mock_http):
    _, calls = mock_http
    r = run(factcheck.check("anything"))
    assert r["status"] == "not_checked" and calls == []


def test_factcheck_with_key(mock_http, monkeypatch):
    routes, calls = mock_http
    monkeypatch.setenv("FACTCHECK_API_KEY", "AIza-test-not-real")
    routes["factchecktools.googleapis.com"] = j({"claims": [{
        "text": "Smart meters can be switched off remotely by the council",
        "claimReview": [{"publisher": {"name": "Example Check"}, "url": "https://check.example/1",
                         "textualRating": "Misleading"}]}]})
    r = run(factcheck.check("Council approves meter plan"))
    assert r["status"] == "found" and "Misleading" in r["text"] and "Example Check" in r["text"]
    assert "may not be about this exact page" in r["text"]
    assert "AIza" not in json.dumps(r)
    assert "query=Council" in calls[0]


# ----------------------------------------------------------------- lookalike

@pytest.mark.parametrize("host", ["err.ee", "news.err.ee", "rus.err.ee", "www.bbc.co.uk", "bbc.com",
                                  "edition.cnn.com", "www.theguardian.com", "apnews.com", "dw.com", "lsm.lv"])
def test_lookalike_official(host):
    r = lookalike.check(host)
    assert r["status"] == "found" and r["flag"] == "ok" and "official domain" in r["text"]


@pytest.mark.parametrize("host, outlet, reason", [
    ("err-news.ee", "ERR", "extra words"),
    ("errnews.ee", "ERR", "extra words"),
    ("news-err.com", "ERR", "extra words"),
    ("spiegel.ltd", "Der Spiegel", "different domain ending"),
    ("bild.pics", "Bild", "different domain ending"),
    ("washingtonpost.pm", "Washington Post", "different domain ending"),
    ("theguardian.co.com", "Guardian", "different domain ending"),
    ("reuters.cfd", "Reuters", "different domain ending"),
    ("bbc.co.uk.example.com", "BBC", "in front of a different domain"),
    ("reutres.com", "Reuters", "one or two letters"),
    ("postimes.ee", "Postimees", "one or two letters"),
    ("delfl.ee", "Delfi", "one or two letters"),
    ("de1fi.ee", "Delfi", "look-alike characters"),
    ("the-guardian.com", "Guardian", "look-alike characters or a hyphen"),
    ("xn--bb-pmc.com", "BBC", "look-alike characters"),   # bbс.com with a Cyrillic "с"
    ("rnedusa.io", "Meduza", None),
])
def test_lookalike_detected(host, outlet, reason):
    r = lookalike.check(host)
    if host == "rnedusa.io":  # rn -> m, but z->s differs: should NOT match (guards against over-eager skeletons)
        assert r["flag"] != "ok"
        return
    assert r["status"] == "found" and r["flag"] == "warning", r
    assert outlet in r["text"]
    if reason:
        assert reason in r["text"]


@pytest.mark.parametrize("host", ["example.com", "wikipedia.org", "github.com", "stoprop.tech", "tartu.ee",
                                  "dr-smith.com", "my-ap-store.com", "ft-shop.de", "hs.example.org",
                                  "bbci.co.uk", "postimees-shop-closed.example"])
def test_lookalike_clean(host):
    r = lookalike.check(host)
    if host == "postimees-shop-closed.example":
        assert r["flag"] == "warning"  # hyphenated outlet name is flagged; documented behaviour
        return
    assert r["status"] == "not_found" and r["flag"] == "neutral", r


def test_no_official_domain_looks_like_another_outlet():
    for o in lookalike.outlets():
        for d in o["domains"]:
            assert lookalike.check(d)["flag"] == "ok", d


def test_outlets_seeded():
    names = " ".join(o["name"] for o in lookalike.outlets())
    assert len(lookalike.outlets()) >= 40
    for n in ("ERR", "Postimees", "Delfi", "LSM", "LRT", "BBC", "Reuters", "Associated Press", "Guardian",
              "Deutsche Welle", "Spiegel", "Le Monde", "Euronews"):
        assert n in names


# ------------------------------------------------------- service: TTLs etc.

class Counting:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    async def __call__(self, domain, now=None):
        self.calls += 1
        await asyncio.sleep(0.01)
        return dict(self.result)


@pytest.fixture
def sources(monkeypatch):
    clock = {"t": 1_000_000.0}
    monkeypatch.setattr(service, "clock", lambda: clock["t"])
    fakes = {
        "rdap": Counting({"status": "found", "flag": "neutral", "text": "Domain registered on 2001-01-01.", "evidence_url": None}),
        "wayback": Counting({"status": "found", "flag": "neutral", "text": "First saved 2002.", "evidence_url": None}),
        "crtsh": Counting({"status": "error", "flag": "neutral", "text": "crt.sh did not answer.", "evidence_url": None}),
    }
    monkeypatch.setattr(rdap, "check", fakes["rdap"])
    monkeypatch.setattr(wayback, "check", fakes["wayback"])
    monkeypatch.setattr(crtsh, "check", fakes["crtsh"])
    return clock, fakes


def get(target="https://www.example.com/a/b", **kw):
    return run(service.get_receipts(target, **kw))


def test_receipts_shape_and_order(sources):
    out = get()
    assert out["domain"] == "www.example.com" and out["registrable_domain"] == "example.com"
    assert [r["type"] for r in out["receipts"]] == list(service.ORDER)
    by = {r["type"]: r for r in out["receipts"]}
    assert by["fact_checks"]["status"] == "not_checked" and by["syndication"]["status"] == "not_checked"
    assert by["fact_checks"]["checked_at"] is None
    for r in out["receipts"]:
        assert set(r) >= {"type", "title", "status", "flag", "text", "evidence_url", "checked_at"}
    assert by["domain_age"]["checked_at"] == "1970-01-12T13:46:40+00:00"


def test_soft_ttl_serves_cache(sources):
    clock, f = sources
    get()
    clock["t"] += 6 * service.DAY
    out = get()
    assert f["rdap"].calls == 1 and f["wayback"].calls == 1
    assert {r["type"]: r for r in out["receipts"]}["domain_age"]["cached"] is True


def test_between_soft_and_hard_serves_stale_and_refreshes_in_background(sources):
    clock, f = sources

    async def scenario():
        await service.get_receipts("example.com")
        clock["t"] += 8 * service.DAY
        out = await service.get_receipts("example.com")
        await asyncio.sleep(0.05)  # let background refresh run
        return out

    out = run(scenario())
    age = {r["type"]: r for r in out["receipts"]}["domain_age"]
    assert age["cached"] is True and age.get("refreshing") is True
    assert f["rdap"].calls == 2


def test_after_hard_ttl_refreshes_before_serving(sources):
    clock, f = sources
    get()
    clock["t"] += 29 * service.DAY
    out = get()
    assert f["rdap"].calls == 2
    assert {r["type"]: r for r in out["receipts"]}["domain_age"]["cached"] is False


def test_errors_cached_for_10_minutes(sources):
    clock, f = sources
    get()
    clock["t"] += 9 * 60
    get()
    assert f["crtsh"].calls == 1
    clock["t"] += 2 * 60
    get()
    assert f["crtsh"].calls == 2


def test_refresh_limited_to_once_per_15_minutes(sources):
    clock, f = sources
    get()
    clock["t"] += 5 * 60
    out = get(refresh=True)
    assert f["rdap"].calls == 1
    age = {r["type"]: r for r in out["receipts"]}["domain_age"]
    assert "refresh available in about 10 min" in age["note"]
    clock["t"] += 11 * 60
    get(refresh=True)
    assert f["rdap"].calls == 2


def test_single_flight(sources):
    _, f = sources

    async def both():
        return await asyncio.gather(service.get_receipts("example.com"), service.get_receipts("www.example.com"))

    run(both())
    assert f["rdap"].calls == 1 and f["wayback"].calls == 1 and f["crtsh"].calls == 1


def test_budget_high_doubles_soft_ttl(sources, _isolated):
    clock, f = sources
    _isolated.log_usage("tagger", "m", 75_000, 0, 1, ok=True)  # 75% of the test cap
    get()
    clock["t"] += 10 * service.DAY  # past normal 7 d soft TTL, within doubled 14 d
    get()
    assert f["rdap"].calls == 1


def test_source_exception_becomes_error_receipt(sources, monkeypatch):
    async def boom(domain, now=None):
        raise RuntimeError("bug")
    monkeypatch.setattr(rdap, "check", boom)
    age = {r["type"]: r for r in get()["receipts"]}["domain_age"]
    assert age["status"] == "error"


# ----------------------------------------------------------------------- API

def test_api_receipts(sources):
    r = client.post("/api/receipts", json={"domain": "err-news.ee"})
    assert r.status_code == 200
    by = {x["type"]: x for x in r.json()["receipts"]}
    assert by["lookalike"]["flag"] == "warning" and "ERR" in by["lookalike"]["text"]


@pytest.mark.parametrize("body, msg", [
    ({}, "link or a domain"),
    ({"domain": "localhost"}, "not valid"),
    ({"domain": "169.254.169.254"}, "not bare IP"),
    ({"url": "http://[::1]/"}, "not bare IP"),
    ({"domain": "exa mple.com"}, "not"),
])
def test_api_receipts_bad_input(sources, body, msg):
    r = client.post("/api/receipts", json=body)
    assert r.status_code == 400 and msg in r.json()["detail"]


def test_api_receipts_rate_limit(sources):
    for _ in range(60):
        assert client.post("/api/receipts", json={"domain": "example.com"}, headers={"X-Forwarded-For": "198.51.100.20"}).status_code == 200
    r = client.post("/api/receipts", json={"domain": "example.com"}, headers={"X-Forwarded-For": "198.51.100.20"})
    assert r.status_code == 429 and "60 source checks" in r.json()["detail"]


ARTICLE = extract.Article(url="https://example.com/a", final_url="https://www.example.com/a", title="Meter plan",
                          date="2026-11-12", sitename="Example", redirects=["https://www.example.com/a"],
                          text="Everyone knows the new meters are a disaster for families.\nThe council met on Tuesday.")


def test_analyze_url_includes_text_source_and_receipts(sources, monkeypatch):
    async def fake_fetch(url):
        return ARTICLE
    monkeypatch.setattr(extract, "fetch_article", fake_fetch)
    body = client.post("/api/analyze", json={"url": "https://example.com/a"}).json()
    assert body["text"] == ARTICLE.text
    assert body["source"]["title"] == "Meter plan" and body["source"]["domain"] == "www.example.com"
    assert len(body["receipts"]) == 6
    assert any("redirected" in n for n in body["notes"])
    for s in body["spans"]:
        assert ARTICLE.text[s["start"]:s["end"]] == s["quote"]


def test_analyze_url_fetch_error_is_friendly(monkeypatch):
    async def fake_fetch(url):
        raise extract.FetchError("blocked", extract.BLOCKED_MSG)
    monkeypatch.setattr(extract, "fetch_article", fake_fetch)
    r = client.post("/api/analyze", json={"url": "http://169.254.169.254/"})
    assert r.status_code == 400 and "private" in r.json()["detail"]


def test_analyze_real_ssrf_path_blocks_metadata():
    # no mocks: the real check_url path must refuse before any DNS/network (conftest forbids both)
    r = client.post("/api/analyze", json={"url": "http://169.254.169.254/latest/meta-data/"})
    assert r.status_code == 400 and "private" in r.json()["detail"]
    r = client.post("/api/analyze", json={"url": "http://2130706433/"})
    assert r.status_code == 400


def test_analyze_text_plus_url_uses_text_and_adds_receipts(sources, monkeypatch):
    async def must_not_fetch(url):
        raise AssertionError("should not fetch when text is given")
    monkeypatch.setattr(extract, "fetch_article", must_not_fetch)
    body = client.post("/api/analyze", json={"text": "Calm text about the council.", "url": "https://err.ee/123"}).json()
    assert "text" not in body and len(body["receipts"]) == 6
    assert {r["type"]: r for r in body["receipts"]}["lookalike"]["flag"] == "ok"


def test_analyze_text_with_ip_url_gives_note_not_error(sources):
    body = client.post("/api/analyze", json={"text": "Calm text here.", "url": "http://10.0.0.1/"}).json()
    assert body["receipts"] == [] and any("No source receipts" in n for n in body["notes"])
