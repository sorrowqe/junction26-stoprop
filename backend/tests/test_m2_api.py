"""End-to-end /api/analyze with a mocked LLM; budget; rate limits; innocent explanations."""
import json

from fastapi.testclient import TestClient

from app import config, ratelimit
from app.llm import budget
from app.main import app
from app.pipeline.profile import innocent_explanations
from fakellm import FakeLLM, install

client = TestClient(app)
SAMPLES = {s["id"]: s["text"] for s in json.loads((config.WEB_DIR / "static" / "samples.json").read_text(encoding="utf-8"))}


def post(text, **kw):
    r = client.post("/api/analyze", json={"text": text, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def manip_responder(user, sents):
    out = []
    for sid, s in sents.items():
        if "disaster" in s:
            out.append({"sentence_id": sid, "quote": "are a disaster", "label": "loaded_language",
                        "confidence": 0.9, "explanation": "Emotionally charged wording instead of evidence."})
        if "freezing in the dark" in s:
            out.append({"sentence_id": sid, "quote": "leaving families freezing in the dark",
                        "label": "fear_appeal", "confidence": 0.8, "explanation": "Invokes danger to families."})
            # hallucinated quote -> must be rejected by the validator
            out.append({"sentence_id": sid, "quote": "the council wants you to suffer",
                        "label": "fear_appeal", "confidence": 0.9, "explanation": "made up"})
    return json.dumps(out)


def test_llm_mode_end_to_end(monkeypatch):
    install(monkeypatch, FakeLLM(manip_responder, tokens=(800, 60)))
    body = post(SAMPLES["manipulative"], sensitivity="broad")
    assert body["mode"] == "llm" and body["mode_reason"] == "ok"
    quotes = {s["quote"]: s for s in body["spans"]}
    assert "are a disaster" in quotes and "leaving families freezing in the dark" in quotes
    assert "the council wants you to suffer" not in quotes
    assert body["stats"]["validator_rejections"] == 1
    for s in body["spans"]:
        assert SAMPLES["manipulative"][s["start"]:s["end"]] == s["quote"]
        assert s["source"] == "llm"
        # every technique is experimental until M4 -> amber, hidden by default in the UI
        assert s["experimental"] is True and s["color"] == "amber"
    assert body["cost"] == {"input_tokens": 800, "output_tokens": 60, "llm_calls": 1, "cached": False}
    assert body["innocent_explanations"]
    assert body["stats"]["model"] == "test-model"


def test_heuristic_spans_not_mixed_into_llm_mode(monkeypatch):
    install(monkeypatch, FakeLLM())  # LLM says: nothing found
    body = post(SAMPLES["manipulative"])
    assert body["mode"] == "llm" and body["spans"] == []
    assert body["innocent_explanations"] == []


def test_second_request_is_cached(monkeypatch):
    fake = install(monkeypatch, FakeLLM(manip_responder))
    first = post(SAMPLES["manipulative"])
    second = post(SAMPLES["manipulative"], sensitivity="broad")
    assert len(fake.calls) == 1
    assert second["cost"]["cached"] is True and second["cost"]["llm_calls"] == 0
    assert first["cost"]["cached"] is False


def test_sensitivity_gating_on_llm_spans(monkeypatch):
    install(monkeypatch, FakeLLM(manip_responder))
    labels = lambda b: sorted(s["confidence"] for s in b["spans"])
    assert labels(post(SAMPLES["manipulative"], sensitivity="strict")) == [0.8, 0.9]
    assert labels(post(SAMPLES["manipulative"], sensitivity="broad")) == [0.8, 0.9]
    # a low-confidence span only survives "broad"
    install(monkeypatch, FakeLLM(lambda u, s: json.dumps([{"sentence_id": "p1s1", "quote": "are a disaster",
                                                          "label": "loaded_language", "confidence": 0.45}])))
    text = "The smart meters are a disaster."
    assert post(text, sensitivity="balanced")["spans"] == []
    assert len(post(text, sensitivity="broad")["spans"]) == 1


def test_private_request_not_cached_and_not_counted(monkeypatch, _isolated):
    fake = install(monkeypatch, FakeLLM(manip_responder))
    post(SAMPLES["manipulative"], private=True)
    post(SAMPLES["manipulative"], private=True)
    assert len(fake.calls) == 2 and _isolated.cache_size() == 0
    assert post(SAMPLES["manipulative"])["stats"]["seen_before"] == 0
    assert post(SAMPLES["manipulative"])["stats"]["seen_before"] == 1


def test_llm_failure_falls_back_to_heuristics(monkeypatch):
    install(monkeypatch, FakeLLM(fail=True))
    body = post(SAMPLES["manipulative"])
    assert body["mode"] == "heuristics_only" and body["mode_reason"] == "llm_error"
    assert body["spans"] and all(s["source"] == "heuristic" for s in body["spans"])
    assert any("temporarily unavailable" in n for n in body["notes"])


def test_budget_over_heuristics_only_with_note(monkeypatch, _isolated):
    fake = install(monkeypatch, FakeLLM(manip_responder))
    _isolated.log_usage("tagger", "test-model", 90_000, 10_000, 1, ok=True)
    body = post(SAMPLES["manipulative"])
    assert fake.calls == [] and body["mode"] == "heuristics_only" and body["mode_reason"] == "budget"
    assert any("budget" in n for n in body["notes"])
    assert client.get("/api/health").json()["budget"]["mode"] == "over"


def test_budget_status_modes(monkeypatch, _isolated):
    assert budget.status()["mode"] == "ok"
    _isolated.log_usage("tagger", "m", 60_000, 10_000, 1, ok=True)
    s = budget.status()
    assert s["mode"] == "high" and s["tokens_today"] == 70_000 and s["share"] == 0.7
    _isolated.log_usage("tagger", "m", 30_000, 0, 1, ok=True)
    assert budget.status()["mode"] == "over"


def test_health_shows_usage(monkeypatch):
    install(monkeypatch, FakeLLM(tokens=(500, 50)))
    post("The council met on Tuesday and approved the plan.")
    h = client.get("/api/health").json()
    assert h["budget"]["tokens_today"] == 550 and h["budget"]["calls_today"] == 1
    assert h["llm"] == {"configured": True, "model": "test-model"}
    assert "sk-test" not in json.dumps(h)


def test_neutral_sample_still_clean_with_llm_saying_nothing(monkeypatch):
    install(monkeypatch, FakeLLM())
    body = post(SAMPLES["neutral"])
    assert [s for s in body["spans"] if s["color"] in ("red", "amber")] == []


# ---------------------------------------------------------------- rate limit

def test_rate_limit_30_per_hour_per_ip():
    for _ in range(30):
        assert client.post("/api/analyze", json={"text": "Hello there, calm text."},
                           headers={"X-Forwarded-For": "203.0.113.5"}).status_code == 200
    r = client.post("/api/analyze", json={"text": "Hello there, calm text."}, headers={"X-Forwarded-For": "203.0.113.5"})
    assert r.status_code == 429
    assert "30 analyses per hour" in r.json()["detail"] and int(r.headers["Retry-After"]) > 0
    # another client is unaffected
    assert client.post("/api/analyze", json={"text": "Hello there."},
                       headers={"X-Forwarded-For": "203.0.113.6"}).status_code == 200


def test_rate_limit_uses_rightmost_forwarded_for():
    # a client cannot dodge the limit by sending its own X-Forwarded-For: Traefik appends the real IP last
    for i in range(30):
        client.post("/api/analyze", json={"text": "Hello there."}, headers={"X-Forwarded-For": f"1.1.1.{i}, 198.51.100.7"})
    r = client.post("/api/analyze", json={"text": "Hello there."}, headers={"X-Forwarded-For": "9.9.9.9, 198.51.100.7"})
    assert r.status_code == 429


def test_rate_limiter_window_slides():
    lim = ratelimit.RateLimiter()
    for i in range(10):
        assert lim.hit("swarm", "ip", now=1000 + i)[0]
    ok, retry = lim.hit("swarm", "ip", now=1500)
    assert not ok and retry == 3600 - 500
    assert lim.hit("swarm", "ip", now=1000 + 3600 + 1)[0]


def test_invalid_requests_do_not_consume_rate_limit():
    for _ in range(40):
        client.post("/api/analyze", json={"text": "  "}, headers={"X-Forwarded-For": "203.0.113.9"})
    assert client.post("/api/analyze", json={"text": "fine"}, headers={"X-Forwarded-For": "203.0.113.9"}).status_code == 200


# ------------------------------------------------------- innocent explanations

def test_innocent_explanations_rules():
    span = lambda **kw: {"color": "amber", "confidence": 0.5, "label": "loaded_language", **kw}
    assert innocent_explanations([], "news") == []
    assert innocent_explanations([span(color="green", label="supported_claim")], "news") == []
    out = innocent_explanations([span(reported_speech=True), span(label="unsupported_claim"), span(source="heuristic")], "opinion")
    assert any("quoted from someone else" in x for x in out)
    assert any("opinion piece" in x for x in out)
    assert any("source may be given elsewhere" in x for x in out)
    assert any("Rule-based hints" in x for x in out)
    for x in out:
        for word in ("propaganda", "liar", "bot", "fake"):
            assert word not in x.lower().split()
