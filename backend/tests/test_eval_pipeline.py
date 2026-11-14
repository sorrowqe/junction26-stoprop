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
    per = {"loaded_language": {"predicted": 20, "precision": 0.8}, "fear_appeal": {"predicted": 6, "precision": 0.5},
           "bandwagon": {"predicted": 2, "precision": 1.0}, "name_calling": {"predicted": 9, "precision": 0.9}}
    monkeypatch.setattr(re_.taxonomy, "TECHNIQUES_PATH", path)
    upd = re_.update_dev_precision(per, path)
    assert upd == {"loaded_language": 0.8, "fear_appeal": 0.5, "name_calling": 0.9}
    import yaml
    data = {t["label"]: t for t in yaml.safe_load(path.read_text(encoding="utf-8"))["techniques"]}
    assert data["loaded_language"]["dev_precision"] == 0.8 and data["loaded_language"]["experimental"] is False
    assert data["loaded_language"]["dev_predictions"] == 20
    assert data["name_calling"]["experimental"] is True    # 0.9 precision but only 9 predictions (< 15)
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


# ------------------------------------------------- bake-off subset + credits

def _items():
    out = []
    for n in range(15):
        out.append({"id": f"sem-{n}", "source": "semeval", "clean_set": None})
        out.append({"id": f"none-{n}", "source": "semeval", "clean_set": "semeval_none"})
        out.append({"id": f"syn-{n}", "source": "synthetic", "clean_set": None})
    out += [{"id": f"hn-{n}", "source": "synthetic", "clean_set": "synthetic_hard_negative"} for n in range(4)]
    return out


def test_bakeoff_subset_is_fixed_and_mixed(tmp_path):
    items, pairs = _items(), [{"pair_id": f"sym-{n:02d}"} for n in range(1, 12)]
    sub = re_.make_bakeoff_subset(items, pairs)
    assert sub == re_.make_bakeoff_subset(list(reversed(items)), pairs)  # order-independent
    ids = sub["items"]
    assert len(ids) == 26 and len(sub["pairs"]) == 2
    assert sum(i.startswith("sem-") for i in ids) == 10 and sum(i.startswith("syn-") for i in ids) == 8
    assert sum(i.startswith("none-") for i in ids) == 6 and sum(i.startswith("hn-") for i in ids) == 2
    path = tmp_path / "subset.json"
    got, got_pairs = re_.load_bakeoff(items, pairs, path)
    assert path.exists() and {i["id"] for i in got} == set(ids) and len(got_pairs) == 2
    # once written, the file wins even if the split grows
    more = items + [{"id": "sem-new", "source": "semeval", "clean_set": None}]
    assert {i["id"] for i in re_.load_bakeoff(more, pairs, path)[0]} == set(ids)


def test_credit_rates_accumulate_and_guess(tmp_path):
    path = tmp_path / "rates.json"
    re_.save_credit_rate("m1", {"a": {"llm_calls": 2, "credits": 1.0}}, path)
    re_.save_credit_rate("m1", {"a": {"llm_calls": 2, "credits": 0.2}}, path)
    rates = re_.read_credit_rates(path)
    assert rates["m1"]["credits_per_call"] == 0.3 and rates["m1"]["calls_measured"] == 4
    assert re_.projected_rate("m1", rates)[0] == 0.3
    rate, how = re_.projected_rate("m2", rates)
    assert rate == 0.3 and "NOT measured" in how
    assert re_.projected_rate("m2", {})[0] is None


def _fake_analyze(calls, credits=0.5, mode="llm"):
    async def fake(text, **kw):
        calls.append(text)
        return {"mode": mode, "mode_reason": "ok" if mode == "llm" else "llm_error", "spans": [],
                "stats": {"validator_rejections": 0},
                "cost": {"llm_calls": 1, "input_tokens": 10, "output_tokens": 5, "credits": credits}}
    return fake


@pytest.mark.parametrize("credits, mode, stops", [(0.5, "llm", False), (1.0, "llm", True), (0.1, "heuristics_only", True)])
def test_cost_guard_checks_first_analysis_alone(monkeypatch, credits, mode, stops):
    import asyncio
    from app import analyze
    calls = []
    monkeypatch.setattr(analyze, "analyze_text", _fake_analyze(calls, credits, mode))
    texts = {f"t{n}": f"text {n}" for n in range(5)}
    if stops:
        with pytest.raises(re_.CostGuardStop):
            asyncio.run(re_.predict(texts, "broad", guard_rate=0.3))
        assert calls == ["text 0"]
    else:
        preds, runs = asyncio.run(re_.predict(texts, "broad", guard_rate=0.3))
        assert len(calls) == 5 and len(runs) == 5


def test_limit_run_writes_sample_not_latest(monkeypatch, tmp_path):
    dev = tmp_path / "gold.jsonl"
    dev.write_text(json.dumps({"id": "x", "source": "semeval", "lang": "en", "text": "Calm text here.", "spans": [],
                               "clean_set": "semeval_none"}) + "\n", encoding="utf-8")
    for name, value in (("DEV", dev), ("SYM", tmp_path / "none.jsonl"), ("EVAL_DB", tmp_path / "eval.db"),
                        ("RESULTS", tmp_path / "results"), ("CREDIT_RATES", tmp_path / "rates.json"),
                        ("TECHNIQUES_YAML", tmp_path / "never.yaml")):
        monkeypatch.setattr(re_, name, value)
    monkeypatch.setattr(re_, "_load_env_local", lambda: None)
    monkeypatch.setenv("ROUTER_API_KEY", "sk-test-not-real")
    from app import analyze
    monkeypatch.setattr(analyze, "analyze_text", _fake_analyze([], credits=0.2))
    assert re_.main(["--yes", "--limit", "1"]) == 0
    assert (tmp_path / "results" / "sample.json").exists()
    assert not (tmp_path / "results" / "latest.json").exists() and not (tmp_path / "never.yaml").exists()
    assert re_.read_credit_rates(tmp_path / "rates.json")["test-model"]["credits_per_call"] == 0.2


# ------------------------------------------------------------------ metrics

def test_api_metrics_and_page():
    client = TestClient(app)
    body = client.get("/api/metrics").json()
    assert {"latest", "bakeoff", "calibration", "self_consistency", "ai_review", "gold_report", "languages"} <= set(body)
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


def test_reviewer_stats_kappa_paradox():
    a = {"rows": [{"n": 1, "verdict": "y"}, {"n": 2, "verdict": "y"}], "unmarked": []}
    b = {"rows": [{"n": 1, "verdict": "y"}, {"n": 2, "verdict": "n"}], "unmarked": [{"quote": "x", "label": "y"}]}
    s = ar.reviewer_stats({"i1": [a, b]})
    assert s["raw_agreement"] == 0.5 and s["kappa"] == 0.0
    assert s["accept_rate"] == {"gemini-3.5-flash-eu": 1.0, "mistral-medium-3.5-eu": 0.5}
    assert s["items_with_unmarked_techniques"] == {"gemini-3.5-flash-eu": 0, "mistral-medium-3.5-eu": 1}


def test_yes_refuses_when_already_reviewed(monkeypatch, tmp_path, capsys):
    gold = tmp_path / "g.csv"
    goldcsv.write_csv(gold, goldcsv.GOLD_COLUMNS, [gold_row(ai_ok="y", ai_reasons="x")])
    sym = tmp_path / "s.csv"
    goldcsv.write_csv(sym, goldcsv.SYMMETRY_COLUMNS, [])
    monkeypatch.setattr(ar, "GOLD", gold)
    monkeypatch.setattr(ar, "SYM", sym)
    monkeypatch.setattr(ar.Reviewer, "__call__", lambda *a, **k: (_ for _ in ()).throw(AssertionError("called")))
    assert ar.main(["--yes"]) == 2
    assert "already have review results" in capsys.readouterr().out


# ------------------------------------------------- calibration + self-consistency

import calibrate as cal  # noqa: E402
import self_consistency as scons  # noqa: E402


def test_wilson_interval():
    assert cal.wilson(0, 0) is None
    lo, hi = cal.wilson(1, 21)
    assert lo == 0.008 and hi == 0.227
    assert cal.wilson(0, 21)[0] == 0.0 and cal.wilson(21, 21)[1] == 1.0


def test_choose_thresholds_levels_differ_even_when_targets_are_loose():
    curve = [{"t": 0.0, "fpr": 0.048, "predicted": 86}, {"t": 0.8, "fpr": 0.048, "predicted": 86},
             {"t": 0.85, "fpr": 0.048, "predicted": 59}, {"t": 0.9, "fpr": 0.0, "predicted": 23}]
    ch = cal.choose_thresholds(curve)
    assert (ch["broad"]["t"], ch["balanced"]["t"], ch["strict"]["t"]) == (0.0, 0.85, 0.9)
    assert ch["strict"]["target_met"] and ch["balanced"]["target_met"]


def test_choose_thresholds_binding_targets_and_unmet():
    curve = [{"t": 0.0, "fpr": 0.40, "predicted": 100}, {"t": 0.6, "fpr": 0.12, "predicted": 70},
             {"t": 0.8, "fpr": 0.07, "predicted": 40}, {"t": 0.9, "fpr": 0.06, "predicted": 10}]
    ch = cal.choose_thresholds(curve)
    assert ch["balanced"] == {"t": 0.6, "target": 0.15, "target_met": True}
    assert ch["strict"] == {"t": 0.9, "target": 0.05, "target_met": False}  # honest: target not reachable


def test_filter_emulates_gate():
    preds = {"x": [{"source": "llm", "confidence": 0.85}, {"source": "llm", "confidence": 0.9},
                   {"source": "heuristic", "confidence": 0.3}]}
    assert [s["confidence"] for s in cal.filt(preds, 0.9, "strict")["x"]] == [0.9]          # rules dropped at strict
    assert [s["confidence"] for s in cal.filt(preds, 0.85, "balanced")["x"]] == [0.85, 0.9, 0.3]


def test_regate_resets_old_values_and_writes_thresholds(tmp_path):
    path = tmp_path / "t.yaml"
    path.write_text("version: 1\ntechniques:\n"
                    "  - label: loaded_language\n    min_confidence: {strict: 0.8, balanced: 0.6, broad: 0.4}\n"
                    "    dev_precision: 0.9\n    experimental: false\n"
                    "  - label: fear_appeal\n    min_confidence: {strict: 0.8, balanced: 0.6, broad: 0.4}\n"
                    "    dev_precision: 0.8\n    experimental: false\n", encoding="utf-8")
    cal.write_thresholds({"strict": {"t": 0.9}, "balanced": {"t": 0.85}, "broad": {"t": 0.0}}, path)
    updates = cal.regate({"loaded_language": {"predicted": 16, "precision": 0.75}}, path)
    text = path.read_text(encoding="utf-8")
    assert updates == {"loaded_language": 0.75}
    assert text.count("min_confidence: {strict: 0.9, balanced: 0.85, broad: 0.0}") == 2
    # fear_appeal had an old (other model) value: reset to unmeasured + experimental
    assert "dev_precision: 0.75\n    dev_predictions: 16\n    experimental: false" in text
    assert "dev_precision: null\n    experimental: true" in text
    cal.r.taxonomy.load.cache_clear()


def test_self_flip_compares_flagged_label_sets():
    s = lambda label, color="red", rs=False: {"label": label, "color": color, "reported_speech": rs}
    first = {"a": [s("loaded_language")], "b": [s("fear_appeal")], "c": [s("doubt_casting", rs=True)]}
    second = {"a": [s("loaded_language")], "b": [s("bandwagon")], "c": []}
    out = scons.self_flip(first, second)
    assert (out["texts"], out["flipped"], out["self_flip_rate"]) == (3, 1, 0.333)


def test_self_consistency_plan_makes_no_calls(monkeypatch, tmp_path, capsys):
    sym = tmp_path / "sym.jsonl"
    sym.write_text(json.dumps({"pair_id": "sym-01", "lang": "en", "a": {"text": "Calm text."}, "b": {"text": "Calm."}}) + "\n",
                   encoding="utf-8")
    monkeypatch.setattr(re_, "SYM", sym)
    monkeypatch.setattr(re_, "DEV", tmp_path / "none.jsonl")
    monkeypatch.setattr(scons, "SELF_DB", tmp_path / "self.db")
    assert scons.main([]) == 0
    assert "1 uncached LLM calls" in capsys.readouterr().out
