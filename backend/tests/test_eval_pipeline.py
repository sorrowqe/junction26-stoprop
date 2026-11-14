"""AI review consensus, sheet_to_jsonl decisions, run_eval metrics, /api/metrics. No model calls."""
import json

import pytest
from fastapi.testclient import TestClient

import ai_review as ar
import goldcsv
import run_eval as re_
import sheet_to_jsonl as sj
from app.main import app

TEXT = "The council met on Tuesday. Critics called the plan a reckless scam for everyone."


def gold_row(**kw):
    base = {"id": "syn-001", "source": "synthetic", "lang": "en", "genre": "news", "kind": "manipulative",
            "topic": "energy", "direction": "a", "text": TEXT, "quote": "a reckless scam", "label": "loaded_language",
            "start": TEXT.index("a reckless scam"), "end": TEXT.index("a reckless scam") + 15,
            "reported_speech": "n", "ok": "", "notes": "", "ai_ok": "", "ai_reasons": ""}
    base.update(kw)
    return base


# ---------------------------------------------------------------- ai_review

def test_load_items_english_only_and_symmetry_grouped():
    gold = [gold_row(), gold_row(id="syn-060", lang="et"), gold_row(id="syn-002", label="none", quote="", start="", end="",
                                                                    kind="hard_negative", notes="fair_opinion")]
    sym = [{"pair_id": "sym-01", "variant": v, "lang": "en", "text": f"text {v}", "quote": "", "label": "none"} for v in "ab"]
    sym.append({"pair_id": "sym-13", "variant": "a", "lang": "et", "text": "x", "quote": "", "label": "none"})
    items = ar.load_items(gold, sym)
    assert {i["item_id"] for i in items} == {"syn-001", "syn-002", "sym-01"}
    s = next(i for i in items if i["item_id"] == "sym-01")
    assert s["kind"] == "symmetry_neutral" and [v["variant"] for v in s["variants"]] == ["a", "b"]
    payload = ar.item_payload(s)
    assert set(payload["variants"]) == {"a", "b"} and payload["rows"][1]["variant"] == "b"


def review(item_id, verdicts, rs=None, unmarked=(), symmetric=None):
    out = {"item_id": item_id, "rows": [{"n": n, "verdict": v, "reported_speech": (rs or {}).get(n, "n"), "reason": f"r{n}"}
                                        for n, v in verdicts.items()], "unmarked": list(unmarked)}
    if symmetric:
        out["symmetric"] = symmetric
    return out


def one_item(kind="manipulative", rows=None):
    gold = rows or [gold_row(kind=kind)]
    return ar.load_items(gold, [])[0], gold


def test_consensus_agree_and_disagree():
    item, _ = one_item()
    ref = ("gold", 0)
    assert ar.consensus(item, review("syn-001", {1: "y"}), review("syn-001", {1: "y"}))["rows"][ref]["ai_ok"] == "y"
    assert ar.consensus(item, review("syn-001", {1: "n"}), review("syn-001", {1: "n"}))["rows"][ref]["ai_ok"] == "n"
    assert ar.consensus(item, review("syn-001", {1: "y"}), review("syn-001", {1: "n"}))["rows"][ref]["ai_ok"] == ""
    assert ar.consensus(item, review("syn-001", {1: "y"}), None)["rows"][ref]["ai_ok"] == ""


def test_reported_speech_corrected_only_if_both_agree():
    item, _ = one_item()
    ref = ("gold", 0)
    both = ar.consensus(item, review("syn-001", {1: "y"}, rs={1: "y"}), review("syn-001", {1: "y"}, rs={1: "y"}))
    assert both["rows"][ref]["rs_fix"] == "y" and "corrected n->y" in both["rows"][ref]["ai_reasons"]
    one = ar.consensus(item, review("syn-001", {1: "y"}, rs={1: "y"}), review("syn-001", {1: "y"}, rs={1: "n"}))
    assert one["rows"][ref]["rs_fix"] is None


def test_hard_negative_with_unmarked_technique():
    item, _ = one_item(rows=[gold_row(kind="hard_negative", label="none", quote="", start="", end="")])
    ref = ("gold", 0)
    u = [{"quote": "reckless scam", "label": "loaded_language"}]
    assert ar.consensus(item, review("syn-001", {1: "y"}, unmarked=u), review("syn-001", {1: "y"}, unmarked=u))["rows"][ref]["ai_ok"] == "n"
    assert ar.consensus(item, review("syn-001", {1: "y"}, unmarked=u), review("syn-001", {1: "y"}))["rows"][ref]["ai_ok"] == ""
    # for manipulative items an unmarked technique is informational only
    m_item, _ = one_item()
    assert ar.consensus(m_item, review("syn-001", {1: "y"}, unmarked=u), review("syn-001", {1: "y"}, unmarked=u))["rows"][("gold", 0)]["ai_ok"] == "y"


def test_symmetry_asymmetric_pair():
    sym = [{"pair_id": "sym-01", "variant": v, "lang": "en", "text": f"text {v}", "quote": "x", "label": "name_calling"} for v in "ab"]
    item = ar.load_items([], sym)[0]
    a = review("sym-01", {1: "y", 2: "y"}, symmetric="n")
    b = review("sym-01", {1: "y", 2: "y"}, symmetric="n")
    rows = ar.consensus(item, a, b)["rows"]
    assert {d["ai_ok"] for d in rows.values()} == {"n"}
    b2 = review("sym-01", {1: "y", 2: "y"}, symmetric="y")
    assert {d["ai_ok"] for d in ar.consensus(item, a, b2)["rows"].values()} == {""}


def test_cohen_kappa():
    assert ar.cohen_kappa([("y", "y"), ("n", "n"), ("y", "y"), ("n", "n")]) == 1.0
    assert ar.cohen_kappa([("y", "n"), ("n", "y")]) == -1.0
    assert ar.cohen_kappa([("y", "y"), ("y", "y"), ("y", "n"), ("n", "n")]) == 0.5
    assert ar.cohen_kappa([]) is None


def test_apply_writes_columns_and_report_keeps_other_languages():
    gold = [gold_row(), gold_row(id="syn-070", lang="ru", text="Это текст.", quote="", label="none", start="", end="")]
    items = ar.load_items(gold, [])
    results = {"syn-001": [review("syn-001", {1: "y"}, rs={1: "y"}), review("syn-001", {1: "y"}, rs={1: "y"})]}
    rep = ar.apply(items, results, gold, [])
    assert gold[0]["ai_ok"] == "y" and gold[0]["reported_speech"] == "y" and "gemini" in gold[0]["ai_reasons"]
    assert gold[1]["ai_ok"] == "" and gold[1]["reported_speech"] == "n"  # RU row untouched
    assert rep["per_label"]["loaded_language"]["accepted"] == 1 and rep["reported_speech_corrections"] == 1


def test_parse_reply_and_credit_fields():
    assert ar.parse_reply('```json\n{"items": [{"item_id": "a"}]}\n```') == {"a": {"item_id": "a"}}
    found = ar._find_credits({"x-credits-charged": "0.25", "content-type": "json"}, {"usage": {"cost": 0.1}})
    assert found == {"header:x-credits-charged": "0.25", "body:usage.cost": 0.1}
    assert ar.credits_value({"body:usage.credits_charged": "1.5"}) == 1.5


def test_ai_review_dry_run_makes_no_calls(monkeypatch, capsys):
    monkeypatch.setattr(ar.Reviewer, "__call__", lambda *a, **k: (_ for _ in ()).throw(AssertionError("called")))
    assert ar.main([]) == 0
    out = capsys.readouterr().out
    assert "Dry run only" in out and "gemini-3.5-flash-eu" in out and "mistral-medium-3.5-eu" in out


def test_first_batch_only_two_calls(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(ar, "CACHE_DIR", tmp_path)
    monkeypatch.setenv("ROUTER_API_KEY", "sk-test")
    calls = []

    def fake(self_msgs):
        calls.append(1)
        return {"text": json.dumps({"items": []}), "input_tokens": 2000, "output_tokens": 500,
                "credits": 0.02, "credit_fields": {"header:x-credits": "0.02"}, "finish_reason": "stop"}
    monkeypatch.setattr(ar.Reviewer, "_router_call", lambda self, msgs: fake(msgs))
    assert ar.main(["--first-batch"]) == 0
    out = capsys.readouterr().out
    assert len(calls) == 2 and "projected total credits" in out and "Stop here" in out


# ------------------------------------------------------------ sheet_to_jsonl

@pytest.mark.parametrize("row, expected", [
    ({"ok": "y", "ai_ok": "n", "source": "synthetic"}, "y"),
    ({"ok": "n", "ai_ok": "y", "source": "synthetic"}, "n"),
    ({"ok": "", "ai_ok": "y", "source": "synthetic"}, "y"),
    ({"ok": "", "ai_ok": "", "source": "synthetic"}, ""),
    ({"ok": "", "ai_ok": "", "source": "semeval"}, "y"),
    ({"ok": "n", "ai_ok": "", "source": "semeval"}, "n"),
])
def test_row_decision(row, expected):
    assert sj.row_decision(row) == expected


def test_build_items_rules():
    rows = [
        gold_row(ai_ok="y"),
        gold_row(id="syn-002", ai_ok="y"), gold_row(id="syn-002", ai_ok="n", quote="Critics", label="name_calling",
                                                    start=TEXT.index("Critics"), end=TEXT.index("Critics") + 7),
        gold_row(id="syn-003", ai_ok=""),
        gold_row(id="syn-004", lang="et", ai_ok="y"),
        gold_row(id="syn-005", ai_ok="y", start=0, end=5),  # broken offsets
        {**gold_row(id="sem-1", source="semeval", label="none", quote="", start="", end="", kind="semeval")},
        gold_row(id="syn-006", ai_ok="y", kind="hard_negative", notes="quoted_loaded", reported_speech="y"),
    ]
    items, rep = sj.build_items(rows, "en")
    ids = {i["id"] for i in items}
    assert ids == {"syn-001", "sem-1", "syn-006"}
    assert rep["per_source"]["synthetic"] == {"accepted": 2, "dropped": 1, "excluded": 1, "invalid": 1}
    assert rep["not_evaluated_language_rows"] == 1 and rep["problems"]
    clean = {i["id"]: i["clean_set"] for i in items}
    assert clean == {"syn-001": None, "sem-1": "semeval_none", "syn-006": "synthetic_hard_negative"}
    rs = next(i for i in items if i["id"] == "syn-006")["spans"][0]
    assert rs["reported_speech"] is True


def test_quote_search_for_human_rows_without_offsets():
    items, rep = sj.build_items([gold_row(id="h-1", source="human", start="", end="")], "en")
    assert items[0]["spans"][0]["start"] == TEXT.index("a reckless scam")
    items, rep = sj.build_items([gold_row(id="h-2", source="human", start="", end="", quote="not there")], "en")
    assert items == [] and "quote not found" in rep["problems"][0]


def test_symmetry_pair_needs_both_variants():
    def srow(pid, v, ai):
        return {"pair_id": pid, "variant": v, "lang": "en", "topic": "war", "text": f"{pid} {v}", "quote": "",
                "label": "none", "ok": "", "ai_ok": ai}
    rows = [srow("sym-01", "a", "y"), srow("sym-01", "b", "y"), srow("sym-02", "a", "y"), srow("sym-02", "b", ""),
            srow("sym-03", "a", "n"), srow("sym-03", "b", "y"), srow("sym-13", "a", "y")]
    rows[-1]["lang"] = "ru"
    pairs, counts = sj.build_symmetry(rows, "en")
    assert [p["pair_id"] for p in pairs] == ["sym-01"]
    assert counts == {"accepted": 1, "excluded": 1, "dropped": 1}


def test_split_is_stable():
    assert sj.is_dev("syn-001") == sj.is_dev("syn-001")
    share = sum(sj.is_dev(f"x-{i}") for i in range(1000)) / 1000
    assert 0.65 < share < 0.75


# ----------------------------------------------------------------- run_eval

def span(s, e, label, rs=False, color="amber"):
    return {"start": s, "end": e, "label": label, "reported_speech": rs, "color": color}


def test_overlap_rule():
    assert re_.overlap_ok(span(0, 10, "x"), span(5, 12, "x"))      # 5 of shorter 7 >= 50%
    assert not re_.overlap_ok(span(0, 10, "x"), span(8, 20, "x"))  # 2 of 10
    assert not re_.overlap_ok(span(0, 5, "x"), span(5, 9, "x"))


def test_span_scores_strict_vs_lenient():
    items = [{"id": "a", "source": "semeval", "text": "t", "spans": [span(0, 10, "loaded_language", rs=None)]},
             {"id": "b", "source": "synthetic", "text": "t", "spans": [span(0, 10, "fear_appeal", rs=True)]}]
    preds = {"a": [span(0, 9, "loaded_language", rs=True), span(20, 30, "name_calling")],
             "b": [span(0, 10, "fear_appeal", rs=True)]}
    strict = re_.span_scores(items, preds, "strict")
    lenient = re_.span_scores(items, preds, "lenient")
    # strict: our reported-speech predictions don't count -> SemEval gold inside quotes becomes a miss;
    # synthetic gold marked reported_speech=y is not expected at all
    assert strict["per_label"]["loaded_language"] == {"precision": None, "recall": 0.0, "f1": None, "predicted": 0,
                                                      "gold": 1, "tp_pred": 0, "tp_gold": 0}
    assert "fear_appeal" not in strict["per_label"]
    assert lenient["per_label"]["loaded_language"]["recall"] == 1.0
    assert lenient["per_label"]["fear_appeal"]["f1"] == 1.0
    assert strict["micro"]["predicted"] == 1 and strict["micro"]["precision"] == 0.0


def test_clean_fpr_sets_and_allowed_gold():
    items = [
        {"id": "s1", "source": "semeval", "text": "w " * 100, "clean_set": "semeval_none", "spans": []},
        {"id": "s2", "source": "semeval", "text": "w " * 100, "clean_set": "semeval_none", "spans": []},
        {"id": "h1", "source": "synthetic", "text": "w " * 50, "clean_set": "synthetic_hard_negative",
         "spans": [span(0, 10, "supported_claim", color="green"), span(20, 30, "fear_appeal", rs=True)]},
    ]
    preds = {"s1": [span(0, 5, "unsupported_claim")], "s2": [],
             "h1": [span(0, 10, "supported_claim", color="green"), span(20, 30, "fear_appeal", rs=True),
                    span(40, 50, "loaded_language", color="red")]}
    out = re_.clean_fpr(items, preds)
    assert out["semeval_none"] == {"paragraphs": 2, "flagged_any": 1, "fpr_any": 0.5, "flagged_techniques": 0,
                                   "fpr_techniques": 0.0, "flags_per_1000_words": 5.0}
    assert out["synthetic_hard_negative"]["flagged_techniques"] == 1


def test_symmetry_flip_rate():
    pairs = [{"pair_id": "p1"}, {"pair_id": "p2"}]
    preds = {"p1:a": [span(0, 5, "name_calling")], "p1:b": [span(0, 5, "name_calling")],
             "p2:a": [span(0, 5, "name_calling")], "p2:b": [span(0, 5, "name_calling", rs=True)]}
    s = re_.symmetry(pairs, preds)
    assert s["flipped"] == 1 and s["flip_rate"] == 0.5


def test_update_dev_precision_keeps_comments(tmp_path, monkeypatch):
    src = (re_.TECHNIQUES_YAML).read_text(encoding="utf-8")
    path = tmp_path / "techniques.yaml"
    path.write_text(src, encoding="utf-8")
    per = {"loaded_language": {"predicted": 10, "precision": 0.8}, "fear_appeal": {"predicted": 6, "precision": 0.5},
           "bandwagon": {"predicted": 2, "precision": 1.0}}
    monkeypatch.setattr(re_.taxonomy, "TECHNIQUES_PATH", path)
    upd = re_.update_dev_precision(per, path)
    assert upd == {"loaded_language": 0.8, "fear_appeal": 0.5}
    import yaml
    data = {t["label"]: t for t in yaml.safe_load(path.read_text(encoding="utf-8"))["techniques"]}
    assert data["loaded_language"]["dev_precision"] == 0.8 and data["loaded_language"]["experimental"] is False
    assert data["fear_appeal"]["experimental"] is True and data["bandwagon"]["dev_precision"] is None
    assert path.read_text(encoding="utf-8").startswith("# Technique taxonomy v1")
    re_.taxonomy.load.cache_clear()


def test_run_eval_plan_only_makes_no_calls(monkeypatch, tmp_path, capsys):
    dev = tmp_path / "gold.jsonl"
    dev.write_text(json.dumps({"id": "x", "source": "semeval", "lang": "en", "text": "Calm text here.", "spans": [],
                               "clean_set": "semeval_none"}) + "\n", encoding="utf-8")
    monkeypatch.setattr(re_, "DEV", dev)
    monkeypatch.setattr(re_, "SYM", tmp_path / "none.jsonl")
    monkeypatch.setattr(re_, "EVAL_DB", tmp_path / "eval.db")
    assert re_.main([]) == 0
    out = capsys.readouterr().out
    assert "1 uncached LLM calls" in out and "Plan only" in out


# ------------------------------------------------------------------ metrics

def test_api_metrics_and_page():
    client = TestClient(app)
    body = client.get("/api/metrics").json()
    assert {"latest", "ai_review", "gold_report", "languages"} <= set(body)
    langs = {l["lang"]: l["evaluated"] for l in body["languages"]}
    assert langs == {"en": True, "et": False, "ru": False, "other": False}
    page = client.get("/metrics").text
    assert "consensus only, not human-verified" in page and "lenient" in page and "English only" in page


def test_gold_csv_has_review_columns():
    assert goldcsv.GOLD_COLUMNS[-2:] == ["ai_ok", "ai_reasons"]


def test_truncated_reply_is_not_cached(monkeypatch, tmp_path):
    monkeypatch.setattr(ar, "CACHE_DIR", tmp_path)
    rev = ar.Reviewer("gemini-3.5-flash-eu", call=lambda msgs: {
        "text": '{"items": [{"item_id"', "input_tokens": 2000, "output_tokens": 8000, "credits": 5.0,
        "credit_fields": {}, "finish_reason": "length"})
    rev([{"role": "user", "content": "x"}])
    assert rev.truncated == 1 and list(tmp_path.glob("*.json")) == []


def test_gemini_gets_low_reasoning():
    assert ar.MODEL_OPTIONS["gemini-3.5-flash-eu"] == {"reasoning_effort": "low"}
    assert "mistral-medium-3.5-eu" not in ar.MODEL_OPTIONS
