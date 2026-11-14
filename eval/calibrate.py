"""Calibrate the sensitivity levels on the DEV split from cached tagger outputs (0 model calls).

  python eval/calibrate.py            # compute, print, write eval/results/calibration.json + techniques.yaml
  python eval/calibrate.py --dry-run  # compute and print only

Targets (technique false-positive rate on the human-labelled SemEval "no technique" paragraphs; amber claim
labels are not counted there because SemEval does not annotate claims; on the synthetic hard negatives every
red/amber flag counts, claims included):
  strict   <= 5 %
  balanced <= 15 %
  broad    = no confidence filter
Rule per level: the LOWEST min_confidence that meets the target AND removes at least one scored prediction
compared with the next looser level (so the three levels really differ). One threshold for all labels.
If no candidate meets a target, the highest candidate is used and the result says target_met = false.

Validated (CLAUDE.md §1 rule 6): per-label precision with NO confidence filter (broad; the most data, and
stricter levels only remove lower-confidence spans) >= 0.70 on >= 15 predictions; precision is recorded for
labels with >= 5 predictions; every other label is reset to "not yet validated". Reports P/R/F1 and FPRs with 95 % Wilson intervals, the symmetry flip rate and
the self-flip rate (eval/self_consistency.py) per level. Calibrated AND measured on dev, so these numbers
are optimistic; the locked test split (run_eval.py --final) is the check.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_eval as r  # noqa: E402
import self_consistency as sc  # noqa: E402

OUT = r.RESULTS / "calibration.json"
TARGETS = {"strict": 0.05, "balanced": 0.15}
DEFAULT_LEVEL = "strict"


def wilson(k: int, n: int, z: float = 1.96) -> list[float] | None:
    if not n:
        return None
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return [round(max(0.0, (c - h) / d), 3), round(min(1.0, (c + h) / d), 3)]


def filt(preds: dict[str, list[dict]], t: float, level: str) -> dict[str, list[dict]]:
    """Emulate taxonomy.gate for one global threshold: model spans need confidence >= t; rule-based spans
    (only present where the model failed) follow heuristics.drop_at."""
    drop_heur = level in r.taxonomy.load().get("heuristics", {}).get("drop_at", [])
    return {k: [s for s in v if (s.get("source") == "heuristic" and not drop_heur)
                or (s.get("source") != "heuristic" and s.get("confidence", 0.0) >= t)] for k, v in preds.items()}


def choose_thresholds(curve: list[dict]) -> dict:
    """curve: [{t, fpr, predicted}] sorted by t ascending, curve[0]["t"] == 0.0 (broad)."""
    chosen = {"broad": {"t": 0.0, "target": None, "target_met": True}}
    prev = curve[0]
    for level in ("balanced", "strict"):
        target = TARGETS[level]
        pick = next((c for c in curve if c["fpr"] <= target and c["predicted"] < prev["predicted"]), None)
        met = pick is not None
        if pick is None:
            pick = curve[-1]
        chosen[level] = {"t": pick["t"], "target": target, "target_met": met}
        prev = pick
    return chosen


def level_report(items, pairs, preds, first_a, second_a, t: float, level: str) -> dict:
    p = filt(preds, t, level)
    out = {"min_confidence": t}
    for name, its in [("all", items)] + sorted(r.by_source(items).items()):
        m = r.span_scores(its, p, "strict")["micro"]
        out.setdefault("scores", {})[name] = {
            "precision": m["precision"], "precision_ci": wilson(m["tp_pred"], m["predicted"]),
            "recall": m["recall"], "recall_ci": wilson(m["tp_gold"], m["gold"]), "f1": m["f1"],
            "predicted": m["predicted"], "gold": m["gold"]}
    fpr = r.clean_fpr(items, p)
    sem = fpr.get("semeval_none", {})
    syn = fpr.get("synthetic_hard_negative", {})
    out["fpr_semeval_none"] = {"flagged": sem.get("flagged_techniques"), "paragraphs": sem.get("paragraphs"),
                               "rate": sem.get("fpr_techniques"),
                               "ci": wilson(sem.get("flagged_techniques", 0), sem.get("paragraphs", 0)),
                               "counts": "techniques only (SemEval does not annotate claims)"}
    out["fpr_synthetic_hard_negative"] = {"flagged": syn.get("flagged_any"), "paragraphs": syn.get("paragraphs"),
                                          "rate": syn.get("fpr_any"),
                                          "ci": wilson(syn.get("flagged_any", 0), syn.get("paragraphs", 0)),
                                          "counts": "any red/amber flag, claims included"}
    sym = r.symmetry(pairs, p)
    out["symmetry"] = {"pairs": sym["pairs"], "flipped": sym["flipped"], "rate": sym["flip_rate"],
                       "ci": wilson(sym["flipped"], sym["pairs"])}
    if second_a:
        s = sc.self_flip(filt(first_a, t, level), filt(second_a, t, level))
        out["self_flip"] = {"texts": s["texts"], "flipped": s["flipped"], "rate": s["self_flip_rate"],
                            "ci": wilson(s["flipped"], s["texts"])}
    return out


def regate(per_label: dict, path: Path | None = None) -> dict:
    """Reset every label to dev_precision null / experimental true, then apply measured precision
    (>= MIN_PRED_FOR_PRECISION predictions). Text edit keeps comments."""
    path = path or r.TECHNIQUES_YAML
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if not re.match(r"\s+dev_predictions:", ln)]
    for i, line in enumerate(lines):
        if re.match(r"\s+dev_precision:", line):
            lines[i] = re.sub(r"dev_precision:.*", "dev_precision: null", line)
        elif re.match(r"\s+experimental:", line):
            lines[i] = re.sub(r"experimental:.*", "experimental: true", line)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    r.taxonomy.load.cache_clear()
    return r.update_dev_precision(per_label, path)


def write_thresholds(chosen: dict, path: Path | None = None) -> None:
    path = path or r.TECHNIQUES_YAML
    fmt = lambda x: f"{x:.2f}".rstrip("0").rstrip(".") if x else "0.0"
    new = (f"min_confidence: {{strict: {fmt(chosen['strict']['t'])}, balanced: {fmt(chosen['balanced']['t'])}, "
           f"broad: {fmt(chosen['broad']['t'])}}}")
    text = path.read_text(encoding="utf-8")
    path.write_text(re.sub(r"min_confidence: \{[^}]*\}", new, text), encoding="utf-8", newline="\n")
    r.taxonomy.load.cache_clear()


def cached_predictions(model: str):
    from app.store import Store, set_store
    os.environ["MODEL_TAGGER"] = model
    items, pairs = r.load(False, r.LANG)
    texts = r.texts_to_run(items, pairs)
    set_store(Store(r.EVAL_DB))
    missing = r.plan_calls(texts, model)
    r._cache_only_mode()
    preds, runs = asyncio.run(r.predict(texts, "broad"))
    a = sc.a_texts(pairs)
    first_a = {k: preds[k] for k in a}
    second_a = {}
    if sc.SELF_DB.exists():
        set_store(Store(sc.SELF_DB))
        if r.plan_calls(a, model) == 0:
            second_a, _ = asyncio.run(r.predict(a, "broad"))
    return items, pairs, preds, runs, first_a, second_a, missing


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--model", default=None, help="default: the model of eval/results/latest.json")
    args = ap.parse_args(argv)
    latest = json.loads((r.RESULTS / "latest.json").read_text(encoding="utf-8"))
    model = args.model or latest["model"]
    items, pairs, preds, runs, first_a, second_a, missing = cached_predictions(model)
    failures = sum(x["mode"] != "llm" for x in runs.values())
    print(f"Model {model}: {len(runs)} cached analyses ({failures} without model output -> rules, as in the run; "
          f"{missing} uncached)  self-consistency texts: {len(second_a)}")

    confs = sorted({s["confidence"] for v in preds.values() for s in v if s.get("source") != "heuristic"})
    curve = []
    for t in [0.0] + [c for c in confs if c > 0]:
        p = filt(preds, t, "broad")
        curve.append({"t": t, "fpr": r.clean_fpr(items, p).get("semeval_none", {}).get("fpr_techniques", 0.0),
                      "predicted": r.span_scores(items, p, "strict")["micro"]["predicted"]})
    chosen = choose_thresholds(curve)
    levels = {lvl: {**level_report(items, pairs, preds, first_a, second_a, chosen[lvl]["t"], lvl),
                    "target_fpr_semeval_none": chosen[lvl]["target"], "target_met": chosen[lvl]["target_met"]}
              for lvl in ("strict", "balanced", "broad")}
    for lvl, v in levels.items():
        a, f, s = v["scores"]["all"], v["fpr_semeval_none"], v["symmetry"]
        print(f"  {lvl:8} t={v['min_confidence']:<5} P={a['precision']} {a['precision_ci']} R={a['recall']} "
              f"{a['recall_ci']} F1={a['f1']}  FPR(SemEval none)={f['flagged']}/{f['paragraphs']} {f['ci']}  "
              f"flip {s['flipped']}/{s['pairs']}  self-flip {v.get('self_flip', {}).get('flipped')}/"
              f"{v.get('self_flip', {}).get('texts')}  target_met={v['target_met']}")
    per_label = r.span_scores(items, filt(preds, 0.0, "broad"), "strict")["per_label"]
    shown = {lbl: m["precision"] for lbl, m in per_label.items()
             if m["predicted"] >= r.MIN_PRED_FOR_PRECISION and m["precision"] is not None}
    print(f"  shown by default (precision >= {r.PRECISION_THRESHOLD}, >= {r.MIN_PRED_FOR_PRECISION} predictions): "
          f"{sorted(k for k, v in shown.items() if v >= r.PRECISION_THRESHOLD) or 'none'}  measured: {shown}")
    if args.dry_run:
        return 0
    write_thresholds(chosen)
    updates = regate(per_label)
    out = {"model": model, "split": "dev", "language": r.LANG, "default_level": DEFAULT_LEVEL,
           "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"), "git_commit": r.git_commit(),
           "rule": "lowest min_confidence meeting the SemEval-none technique FPR target that also removes at least "
                   "one scored prediction versus the next looser level; one threshold for all labels",
           "caveat": "calibrated and measured on the same dev split, so optimistic; the locked test split is the check",
           "analyses": len(runs), "model_failures": failures, "curve": curve, "levels": levels,
           "dev_precision_basis": "broad (no confidence filter), labels with >= 5 predictions",
           "dev_precision": updates}
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {OUT} and thresholds/gating in {r.TECHNIQUES_YAML.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
