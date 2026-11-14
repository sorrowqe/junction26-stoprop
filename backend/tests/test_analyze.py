import json

from fastapi.testclient import TestClient

from app import config, taxonomy
from app.analyze import MAX_WORDS, truncate_words
from app.main import app

client = TestClient(app)
SAMPLES = {s["id"]: s["text"] for s in json.loads((config.WEB_DIR / "static" / "samples.json").read_text(encoding="utf-8"))}

SPAN_KEYS = {"start", "end", "quote", "label", "color", "confidence", "explanation", "reported_speech", "experimental"}


def analyze(text, **kw):
    r = client.post("/api/analyze", json={"text": text, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def test_response_schema():
    body = analyze(SAMPLES["manipulative"])
    for key in ("id", "lang", "genre", "spans", "profile", "mode", "notes", "cost"):
        assert key in body
    assert body["id"].startswith("r_")
    assert body["mode"] == "heuristics_only"
    assert body["cost"] == {"input_tokens": 0, "output_tokens": 0, "llm_calls": 0, "credits": 0.0, "cached": False}
    assert body["mode_reason"] == "no_key"
    for key in ("words", "flagged_share", "density_per_1000", "counts", "summary"):
        assert key in body["profile"]
    for s in body["spans"]:
        assert SPAN_KEYS <= set(s)
        assert SAMPLES["manipulative"][s["start"]:s["end"]] == s["quote"]


def test_heuristic_spans_are_not_validated_and_coloured_by_type():
    spans = analyze(SAMPLES["manipulative"], sensitivity="balanced")["spans"]
    assert len(spans) >= 3
    colors = {t["label"]: t["color"] for t in taxonomy.load()["techniques"]}
    for s in spans:
        assert s["color"] == colors[s["label"]] and s["experimental"] is True and s["confidence"] < 0.5


def test_neutral_sample_is_clean():
    """The canary: neutral wire text must not light up."""
    body = analyze(SAMPLES["neutral"])
    assert [s for s in body["spans"] if s["color"] in ("red", "amber")] == []
    assert body["genre"] == "news"
    assert body["profile"]["flagged_share"] == 0.0


def test_opinion_sample_has_no_default_flags():
    body = analyze(SAMPLES["opinion"])
    assert body["genre"] == "opinion"
    assert body["profile"]["counts"] == {}


def test_strict_drops_heuristic_spans():
    assert analyze(SAMPLES["manipulative"], sensitivity="strict")["spans"] == []


def test_reported_speech_flag_end_to_end():
    text = 'Officials were calm. "Everyone knows this plan failed," the opposition leader said.'
    spans = analyze(text, sensitivity="balanced")["spans"]
    assert spans and all(s["reported_speech"] for s in spans)


def test_ru_and_et_do_not_crash_and_detect_language():
    assert analyze("Все знают, что эта реформа провалилась. Министр заявил об этом в понедельник.")["lang"] == "ru"
    assert analyze("Kõik teavad, et see plaan on läbi kukkunud. Minister ütles seda esmaspäeval.")["lang"] == "et"


def test_emoji_offsets_are_code_points():
    text = "🎉🎉 Everyone knows this is true."
    s = analyze(text, sensitivity="balanced")["spans"][0]
    assert text[s["start"]:s["end"]] == "Everyone knows"


def test_empty_text_rejected():
    r = client.post("/api/analyze", json={"text": "   "})
    assert r.status_code == 400
    assert "paste" in r.json()["detail"].lower()


def test_nothing_given_rejected():
    r = client.post("/api/analyze", json={})
    assert r.status_code == 400
    assert "text or a link" in r.json()["detail"]


def test_too_long_rejected():
    r = client.post("/api/analyze", json={"text": "a " * 60_000})
    assert r.status_code == 413


def test_bad_sensitivity_rejected():
    assert client.post("/api/analyze", json={"text": "hi there", "sensitivity": "max"}).status_code == 422


def test_truncation_is_prefix_with_note():
    text = " ".join(f"w{i}" for i in range(MAX_WORDS + 50))
    cut, truncated = truncate_words(text)
    assert truncated and text.startswith(cut) and len(cut.split()) == MAX_WORDS
    assert truncate_words("just a few words") == ("just a few words", False)
    body = analyze(text)
    assert any("truncated" in n for n in body["notes"])
    assert body["profile"]["words"] == MAX_WORDS


def test_techniques_endpoint():
    body = client.get("/api/techniques").json()
    labels = {t["label"] for t in body["labels"]}
    assert len(labels) == 13
    assert {"loaded_language", "unsupported_claim", "supported_claim", "balance"} <= labels
    for t in body["labels"]:
        assert t["definition"] and t["name"] and t["color"] in ("red", "amber", "green", "blue")


def test_gate_llm_spans_by_confidence_and_colour():
    base = {"start": 0, "end": 4, "quote": "abcd", "label": "fear_appeal", "source": "llm", "reported_speech": False}
    spans = [dict(base, confidence=0.9), dict(base, confidence=0.5), dict(base, confidence=0.3), dict(base, label="nope", confidence=0.9)]
    assert [s["confidence"] for s in taxonomy.gate(spans, "strict")] == [0.9]
    balanced = taxonomy.gate(spans, "balanced")
    assert [s["confidence"] for s in balanced] == [0.9]
    broad = taxonomy.gate(spans, "broad")  # broad = no confidence filter (calibration 2026-11-14)
    assert [s["confidence"] for s in broad] == [0.9, 0.5, 0.3]
    # colour comes from the label type only; not yet validated is a separate flag (dashed in the UI)
    assert all(s["color"] == "red" and s["experimental"] for s in broad)


def test_validated_needs_precision_and_15_predictions():
    assert taxonomy.validated({"dev_precision": 0.8, "dev_predictions": 15})
    assert not taxonomy.validated({"dev_precision": 0.86, "dev_predictions": 7})
    assert not taxonomy.validated({"dev_precision": 0.6, "dev_predictions": 40})
    assert not taxonomy.validated({"dev_precision": None, "dev_predictions": None})


def test_static_files_served():
    assert client.get("/static/samples.json").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/style.css").status_code == 200


def test_pages_served_with_nav():
    for path in ("/", "/source", "/swarm", "/metrics", "/methodology", "/api"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert 'aria-label="Main"' in r.text and 'href="/swarm"' in r.text, path


def test_openapi_docs_enabled():
    assert client.get("/docs").status_code == 200
    assert "/api/analyze" in client.get("/openapi.json").json()["paths"]


def test_no_extension_leftovers():
    import pathlib
    root = config.REPO_ROOT
    assert not (root / "extension").exists()
    assert "EXTENSION_ORIGINS" not in (root / ".env.example").read_text(encoding="utf-8")


def test_v2_response_fields():
    body = analyze("Everyone knows the plan failed.")
    assert "innocent_explanations" in body and body["receipts"] == []


def test_language_coverage_english_evaluated():
    body = analyze("Everyone knows the plan failed and the council is to blame for it.")
    assert body["lang"] == "en" and body["language_evaluated"] is True
    assert body["sensitivity"] == "strict" and body["language_note"] is None  # site default


def test_unevaluated_language_defaults_to_strict_with_note():
    body = analyze("Kõik teavad, et see plaan on läbi kukkunud. Minister ütles seda esmaspäeval.")
    assert body["lang"] == "et" and body["language_evaluated"] is False
    assert body["sensitivity"] == "strict" and body["spans"] == []  # strict drops rule-based hints
    assert "only been measured for English" in body["language_note"] and "Estonian" in body["language_note"]
    # an explicit choice is respected
    explicit = analyze("Kõik teavad, et see plaan on läbi kukkunud. Minister ütles seda esmaspäeval.", sensitivity="broad")
    assert explicit["sensitivity"] == "broad" and explicit["spans"]


def test_other_language_note():
    body = analyze("Die Regierung hat heute einen neuen Plan vorgestellt, der die Energiepreise senken soll.")
    assert body["language_evaluated"] is False and "this language" in body["language_note"]
