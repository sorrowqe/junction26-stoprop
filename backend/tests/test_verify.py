"""Claim verification: claim extraction (verbatim attribution), outlet mapping, GDELT search (documented ArtList shape;
the 429 page is the real one GDELT returned on 2026-11-14), verbatim evidence check, verdict rules, the
"unverifiable attribution" span, cache, limits and the API. No live network, LLM via FakeLLM."""
import asyncio
import json

import httpx2
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.pipeline import extract
from app.swarm.collectors import http as chttp
from app.verify import claims as claims_mod
from app.verify import judge as judge_mod
from app.verify import news
from app.verify import run as vrun
from fakellm import FakeLLM, install as install_llm

client = TestClient(app)
DELFI = "Russia is attacking Estonia with drones according to Delfi news"
GDELT_429 = ("Please limit requests to one every 5 seconds or contact kalev.leetaru5@gmail.com for larger queries. "
             "All high-traffic users should switch to our ngrams dataset.")
CLAIM = {"claim": "Russia is attacking Estonia with drones.", "entities": ["Russia", "Estonia", "drones"],
         "query": "Russia Estonia drones attack", "cited_source": "Delfi news", "cited_domain": "delfi.ee",
         "attribution": "according to Delfi news"}


def art(url, title, domain, seen="20261112T101500Z"):
    return {"url": url, "url_mobile": "", "title": title, "seendate": seen, "socialimage": "", "domain": domain,
            "language": "English", "sourcecountry": "Estonia"}


class Gdelt:
    """MockTransport for GDELT: answers per query; records requests."""

    def __init__(self, cited=(), others=(), status=200, body=None):
        self.cited, self.others, self.status, self.body = list(cited), list(others), status, body
        self.queries = []

    def __call__(self, req: httpx2.Request):
        assert req.url.host == "api.gdeltproject.org" and req.url.path == "/api/v2/doc/doc"
        q = req.url.params["query"]
        self.queries.append(dict(req.url.params))
        if self.status != 200:
            return httpx2.Response(self.status, text=GDELT_429)
        if self.body is not None:
            return httpx2.Response(200, text=self.body)
        return httpx2.Response(200, json={"articles": self.cited if "domain:" in q else self.others})


def install(monkeypatch, gdelt, pages=None):
    monkeypatch.setattr(chttp, "client_factory", lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(gdelt)))

    async def no_wait(_s):
        return None
    monkeypatch.setattr(news.asyncio, "sleep", no_wait)
    pages = pages or {}

    async def fetch(url):
        if url not in pages:
            raise extract.FetchError("blocked", "no", 403)
        return extract.Article(url=url, final_url=url, title=pages[url][0], date="2026-11-12", sitename="",
                               text=pages[url][1], redirects=[])
    monkeypatch.setattr(extract, "fetch_article", fetch)
    return gdelt


def llm(claims=None, stances=None):
    """Extraction -> claims JSON; judge (user message starts with 'Claim:') -> stances JSON."""
    def responder(user, _s):
        if user.startswith("Claim:"):
            return json.dumps(stances or [])
        return json.dumps(claims if claims is not None else [CLAIM])
    return FakeLLM(responder, tokens=(400, 80))


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------------ units

def test_outlet_mapping():
    assert claims_mod.outlet_domains("Delfi news", None) == (["delfi.ee", "delfi.lt", "delfi.lv"],
                                                             "Delfi Estonia / Delfi Latvia / Delfi Lithuania")
    assert claims_mod.outlet_domains("ERR", None)[0] == ["err.ee"]
    assert claims_mod.outlet_domains("Tallinn Weekly Gazette", "tallinnweekly.example")[0] == ["tallinnweekly.example"]
    assert claims_mod.outlet_domains("Some Blog", "not a domain")[0] == []


def test_claim_cleanup_requires_verbatim_attribution():
    made_up = dict(CLAIM, attribution="as reported by Delfi")      # not in the text
    out = claims_mod.clean(DELFI, [CLAIM, made_up, dict(CLAIM, claim="Second claim here."), dict(CLAIM, claim="Third."),
                                   dict(CLAIM, claim="Fourth.")])
    assert len(out) == 3                                             # max 3, duplicates removed
    assert out[0]["attribution"] == "according to Delfi news" and out[0]["attribution_span"] == [40, 63]
    assert DELFI[40:63] == "according to Delfi news"
    assert claims_mod.clean(DELFI, [made_up])[0]["attribution"] is None


def test_gdelt_query_and_dates():
    assert news._gdelt_query("Russia Estonia drones", ["delfi.ee"]) == "Russia Estonia drones domain:delfi.ee"
    assert news._gdelt_query("x y", ["delfi.ee", "delfi.lt"]) == "x y (domain:delfi.ee OR domain:delfi.lt)"
    assert news._seendate("20261112T101500Z") == pytest.approx(1794478500.0)


@pytest.mark.parametrize("stances, verdict", [
    ([], "cited_source_not_found"),
    ([("delfi.ee", "supports")], "corroborated"),
    ([("delfi.ee", "supports"), ("err.ee", "contradicts")], "contradicted"),
])
def test_decide_with_cited_source(stances, verdict):
    judged = [{"domain": d, "stance": st} for d, st in stances]
    claim = {"cited_domains": ["delfi.ee", "delfi.lt"]}
    v = vrun.decide(claim, {"needed": True, "searched": True, "error": None, "results": 4}, judged, [])
    assert v["verdict"] == verdict


def test_cited_outlet_absent_from_index_is_not_a_fabrication():
    """Live lesson 2026-11-14: a true ERR report was not in the index yet. Zero articles from the outlet on the topic
    must never become 'cited source not found'."""
    claim = {"cited_domains": ["err.ee"]}
    att = {"needed": True, "searched": True, "error": None, "results": 0}
    v = vrun.decide(claim, att, [], [])
    assert v["verdict"] == "not_checked" and "too recent" in v["text"]
    v = vrun.decide(claim, att, [{"domain": "postimees.ee", "stance": "supports"}], [])
    assert v["verdict"] == "corroborated" and "could not be checked" in v["text"]


def test_query_cleanup():
    assert news.clean_keywords("Estonia Luxembourg 1-0 UEFA Nations League") == "Estonia Luxembourg UEFA Nations League"
    assert news.any_of(["Saatse", "boot bypass", "Estonia"]) == '(Saatse OR "boot bypass" OR Estonia)'


def test_decide_without_cited_source():
    c = {"cited_domains": []}
    att = {"needed": False, "searched": False, "error": None}
    same_family = [{"domain": "delfi.ee", "stance": "supports"}, {"domain": "err.ee", "stance": "supports"},
                   {"domain": "err.ee", "stance": "supports"}]
    v = vrun.decide(c, att, same_family, [])
    assert v["verdict"] == "corroborated" and v["independent_outlets"] == 2
    assert vrun.decide(c, att, [], [])["verdict"] == "no_reports_found"
    assert "too recent" in vrun.decide(c, att, [], [])["text"]
    v = vrun.decide(dict(c, _search_error="busy"), att, [], [])
    assert v["verdict"] == "not_checked"
    # the attribution search itself failed: never claim the source was not found
    v = vrun.decide({"cited_domains": ["delfi.ee"]}, {"needed": True, "searched": False, "error": "busy"}, [], [])
    assert v["verdict"] == "not_checked"


def test_judge_discards_quotes_not_in_article(monkeypatch):
    articles = [{"url": "https://err.ee/1", "title": "Drones seen over Narva", "domain": "err.ee", "ts": 1.0, "read": True,
                 "text": "Several drones were seen over Narva on Tuesday, the border guard said."},
                {"url": "https://x.ee/2", "title": "Drones attack Estonia", "domain": "x.ee", "ts": 1.0, "read": False, "text": ""}]
    install_llm(monkeypatch, llm(stances=[
        {"article": 1, "stance": "supports", "quote": "Russia launched 40 drones at Tallinn"},   # invented
        {"article": 2, "stance": "supports", "quote": "Drones attack Estonia"}]))                  # headline only
    judged, meta = run(judge_mod.judge(CLAIM, articles))
    assert [a["stance"] for a in judged] == ["unrelated", "supports"] and meta["discarded"] == 1
    assert judged[1]["quote_verified"] is True


# -------------------------------------------------------------- end to end

def test_delfi_example_cited_source_not_found(monkeypatch):
    others = [art("https://news.err.ee/1", "Estonia raises air defence readiness", "err.ee")]
    page = ("Estonia raises air defence readiness", "The defence forces raised readiness after drone sightings in Latvia.")
    delfi = [art(f"https://www.delfi.ee/a/{k}", f"Estonia news item {k}", "delfi.ee") for k in range(12)]
    gd = install(monkeypatch, Gdelt(cited=delfi, others=others), pages={others[0]["url"]: page})
    fake = install_llm(monkeypatch, llm(stances=[]))
    r = run(vrun.verify(DELFI))
    c = r["claims"][0]
    assert c["verdict"] == "cited_source_not_found" and c["cited_domains"] == ["delfi.ee", "delfi.lt", "delfi.lv"]
    assert c["text"].startswith("delfi.ee, delfi.lt, delfi.lv has recent articles on this topic")
    span = r["spans"][0]
    assert span["label"] == "unverifiable_attribution" and span["color"] == "red"
    assert DELFI[span["start"]:span["end"]] == "according to Delfi news"
    assert gd.queries[0]["query"].startswith("(Russia OR Estonia OR drones) (domain:delfi.ee")
    assert gd.queries[0]["timespan"] == "30d"
    assert gd.queries[1]["timespan"] == "14d" and "domain:" not in gd.queries[1]["query"]
    assert r["cost"]["llm_calls"] == 2 and r["cost"]["searches"] == 2 and len(fake.calls) == 2
    judge_prompt = fake.user_messages[1]
    assert judge_prompt.count("[Article") == 2 + 8   # 2 read in full (1 delfi + 1 other) + 8 delfi headlines by title
    assert any("Fact Check" in n for n in r["notes"])
    # cached for an hour: no new calls
    again = run(vrun.verify(DELFI))
    assert again["cached"] is True and len(fake.calls) == 2 and len(gd.queries) == 2


def test_cited_source_confirmed_and_independent_outlet(monkeypatch):
    cited = [art("https://www.delfi.ee/a/1", "Drones over Estonia", "delfi.ee")]
    others = [art("https://www.delfi.lt/a/2", "Drones over Estonia (LT)", "delfi.lt"),
              art("https://news.err.ee/3", "Drone attack reported", "err.ee")]
    pages = {"https://www.delfi.ee/a/1": ("Drones over Estonia", "Russian drones attacked targets in eastern Estonia overnight."),
             "https://news.err.ee/3": ("Drone attack reported", "The ministry confirmed Russian drones attacked Estonia.")}
    install(monkeypatch, Gdelt(cited=cited, others=others), pages=pages)
    install_llm(monkeypatch, llm(stances=[
        {"article": 1, "stance": "supports", "quote": "Russian drones attacked targets in eastern Estonia"},
        {"article": 2, "stance": "supports", "quote": "Russian drones attacked Estonia"}]))
    r = run(vrun.verify(DELFI))
    c = r["claims"][0]
    assert c["verdict"] == "corroborated" and c["independent_outlets"] == 1    # delfi.lt is the same outlet family
    assert r["spans"] == [] and {e["domain"] for e in c["evidence"]} == {"delfi.ee", "err.ee"}


def test_contradicted(monkeypatch):
    cited = [art("https://www.delfi.ee/a/9", "No drone attack, army says", "delfi.ee")]
    install(monkeypatch, Gdelt(cited=cited, others=[]),
            pages={cited[0]["url"]: ("No drone attack, army says", "The army said reports of a drone attack on Estonia are false.")})
    install_llm(monkeypatch, llm(stances=[{"article": 1, "stance": "contradicts",
                                           "quote": "reports of a drone attack on Estonia are false"}]))
    r = run(vrun.verify(DELFI))
    c = r["claims"][0]
    assert c["verdict"] == "contradicted" and "reports the opposite" in c["text"] and r["spans"] == []
    assert c["evidence"][0]["quote"] == "reports of a drone attack on Estonia are false"


def test_zero_articles_from_cited_outlet_no_span(monkeypatch):
    install(monkeypatch, Gdelt(cited=[], others=[]))
    install_llm(monkeypatch, llm())
    r = run(vrun.verify(DELFI))
    assert r["claims"][0]["verdict"] == "not_checked" and r["spans"] == []


def test_search_unavailable_means_not_checked(monkeypatch):
    install(monkeypatch, Gdelt(status=429))
    install_llm(monkeypatch, llm())
    r = run(vrun.verify(DELFI))
    c = r["claims"][0]
    assert c["verdict"] == "not_checked" and "busy" in c["text"] and r["spans"] == []


def test_gdelt_error_page_with_200_is_not_results(monkeypatch):
    install(monkeypatch, Gdelt(body="Your search contained a phrase that was too short."))
    install_llm(monkeypatch, llm())
    assert run(vrun.verify(DELFI))["claims"][0]["verdict"] == "not_checked"


def test_requests_are_spaced_five_seconds(monkeypatch):
    install(monkeypatch, Gdelt())
    waits = []

    async def sleep(sec):
        waits.append(sec)
    monkeypatch.setattr(news.asyncio, "sleep", sleep)
    install_llm(monkeypatch, llm())
    run(vrun.verify(DELFI))
    assert waits and all(w > 5 for w in waits)


def test_no_claims_and_no_key():
    with pytest.raises(vrun.VerifyError):
        run(vrun.verify(DELFI))   # no ROUTER_API_KEY in tests


# -------------------------------------------------------------------- API

def test_api_verify_stream_and_rate_limit(monkeypatch):
    install(monkeypatch, Gdelt())
    install_llm(monkeypatch, llm())
    with client.stream("POST", "/api/verify", json={"text": DELFI, "stream": True}) as r:
        lines = [json.loads(x) for x in r.iter_lines() if x.strip()]
    assert any("Searching news" in x.get("progress", "") for x in lines)
    assert lines[-1]["result"]["claims"][0]["verdict"] == "not_checked"   # no Delfi articles in this mock
    assert client.post("/api/verify", json={"text": "hi"}).status_code == 400
    hdr = {"X-Forwarded-For": "203.0.113.60"}
    for k in range(10):
        assert client.post("/api/verify", json={"text": f"{DELFI} {k}"}, headers=hdr).status_code == 200
    r = client.post("/api/verify", json={"text": DELFI}, headers=hdr)
    assert r.status_code == 429 and "10 claim checks per hour" in r.json()["detail"]


def test_api_verify_without_ai_key():
    r = client.post("/api/verify", json={"text": DELFI})
    assert r.status_code == 503 and "needs the AI" in r.json()["detail"]


def test_supported_claim_display_name_and_new_label():
    labels = {t["label"]: t for t in client.get("/api/techniques").json()["labels"]}
    assert labels["supported_claim"]["name"] == "Cites a source (not yet verified)"
    assert labels["unverifiable_attribution"]["color"] == "red"
    from app.pipeline import tagger
    assert "unverifiable_attribution" not in tagger.system_prompt() and "Claim with a source" in tagger.system_prompt()
