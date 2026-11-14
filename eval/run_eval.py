"""Evaluation harness (CLAUDE.md §7) - ENGLISH ONLY for now.

  python eval/run_eval.py                          # plan: texts, LLM calls, token estimate (no calls)
  python eval/run_eval.py --yes                    # dev split with MODEL_TAGGER (DEV key from .env.local)
  python eval/run_eval.py --yes --limit 10         # small sample while developing (writes sample.json only)
  python eval/run_eval.py --bakeoff                # plan the bake-off: fixed ~30-paragraph subset, every model
  python eval/run_eval.py --yes --bakeoff [--models a,b,c]   # run it (writes bakeoff.json)
  python eval/run_eval.py --yes --final            # LOCKED test split (announce the call count first)

Metrics, all reported per source (synthetic / semeval / human):
- span P/R/F1 per label; match = same label and overlap >= 50% of the shorter span.
  strict  = spans marked reported_speech (by us, or reported_speech=y in gold) do not count (principle 3);
  lenient = they count (SemEval's definition: it also annotates techniques inside quotes).
- clean-text FPR on two clean sets reported separately: SemEval paragraphs labelled "none" (human-labelled
  real news) and the synthetic English hard negatives. "any" = >= 1 red/amber span, "techniques" = >= 1
  technique span (claims excluded), plus flags per 1000 words.
- symmetry flip rate on English pairs (predicted label sets of a and b differ).
- validator rejection rate, abstention rate, mean tokens + latency per analysis, LLM failures.
Predictions = every gated span at the chosen sensitivity, INCLUDING experimental ones (otherwise nothing
would be measured while everything is experimental).
Outputs: full single-model dev run -> eval/results/latest.json (shown on /metrics) and dev_precision /
experimental updates in backend/config/techniques.yaml (labels with >= 5 predictions); --limit -> sample.json
(never shown, never gates); bake-off / multi-model -> bakeoff.json (+ bakeoff_<model>.json).
Credits: every run records the router's credits per call per model in eval/results/credit_rates.json; the plan
projects credits from it, and a run stops after its first analysis if that costs > 2x the projection.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "backend"))

from app import taxonomy  # noqa: E402

DEV = ROOT / "eval" / "data" / "dev" / "gold.jsonl"
TEST = ROOT / "eval" / "data" / "test" / "gold.jsonl"
SYM = ROOT / "eval" / "data" / "symmetry" / "symmetry.jsonl"
RESULTS = ROOT / "eval" / "results"
EVAL_DB = ROOT / "eval" / "data" / "cache" / "eval.db"
TECHNIQUES_YAML = ROOT / "backend" / "config" / "techniques.yaml"
LANG = "en"
EST_IN, EST_OUT = 1700, 450   # tokens per tagger call (real smoke test: 1632 in / 429 out)
MIN_PRED_FOR_PRECISION = 5
PRECISION_THRESHOLD = 0.70
FLAGGED = ("red", "amber")
BAKEOFF_SUBSET = ROOT / "eval" / "data" / "bakeoff_subset.json"
CREDIT_RATES = RESULTS / "credit_rates.json"
DEFAULT_BAKEOFF_MODELS = "claude-haiku-4.5-eu,gemini-3.5-flash-lite-eu,gpt-5.4-mini,mistral-small-4-eu"
# (source, clean_set, n): technique paragraphs from both sources + both clean sets; plus mirrored pairs
BAKEOFF_QUOTA = (("semeval", "", 10), ("synthetic", "", 8), ("semeval", "semeval_none", 6),
                 ("synthetic", "synthetic_hard_negative", 2))
BAKEOFF_PAIRS = 2
COST_GUARD_FACTOR = 2.0


class CostGuardStop(Exception):
    pass


# ------------------------------------------------------------------- data

def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load(final: bool, lang: str = LANG, limit: int | None = None) -> tuple[list[dict], list[dict]]:
    items = [i for i in read_jsonl(TEST if final else DEV) if i.get("lang") == lang]
    pairs = [p for p in read_jsonl(SYM) if p.get("lang") == lang]
    if limit:  # round-robin across sources so a small sample still covers synthetic AND semeval
        groups = defaultdict(list)
        for it in items:
            groups[it["source"]].append(it)
        mixed = [it for batch in zip(*groups.values()) for it in batch]
        rest = [it for it in items if it not in mixed]
        items = (mixed + rest)[:limit]
        pairs = pairs[: max(1, limit // 5)] if pairs else []
    return items, pairs


def _bakeoff_hash(key: str) -> str:
    return hashlib.sha256(f"stoprop-bakeoff:{key}".encode("utf-8")).hexdigest()


def make_bakeoff_subset(items: list[dict], pairs: list[dict]) -> dict:
    """Seeded, quota-based subset; written once to BAKEOFF_SUBSET and reused so every model sees the same texts."""
    ids = []
    for source, clean, n in BAKEOFF_QUOTA:
        pool = sorted((i for i in items if i["source"] == source and (i.get("clean_set") or "") == clean),
                      key=lambda i: _bakeoff_hash(i["id"]))
        ids += [i["id"] for i in pool[:n]]
    pair_ids = [p["pair_id"] for p in sorted(pairs, key=lambda p: _bakeoff_hash(p["pair_id"]))[:BAKEOFF_PAIRS]]
    return {"items": ids, "pairs": pair_ids,
            "quota": [list(q) for q in BAKEOFF_QUOTA] + [["symmetry_pairs", "", BAKEOFF_PAIRS]]}


def load_bakeoff(items: list[dict], pairs: list[dict], path: Path | None = None) -> tuple[list[dict], list[dict]]:
    path = path or BAKEOFF_SUBSET
    if path.exists():
        sub = json.loads(path.read_text(encoding="utf-8"))
    else:
        sub = make_bakeoff_subset(items, pairs)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(sub, indent=1) + "\n", encoding="utf-8")
    keep, keep_pairs = set(sub["items"]), set(sub["pairs"])
    missing = keep - {i["id"] for i in items}
    if missing:
        print(f"WARNING: {len(missing)} bake-off ids no longer in the dev split: {sorted(missing)[:5]}")
    return [i for i in items if i["id"] in keep], [p for p in pairs if p["pair_id"] in keep_pairs]


# ---------------------------------------------------------------- scoring

def overlap_ok(a: dict, b: dict) -> bool:
    inter = min(a["end"], b["end"]) - max(a["start"], b["start"])
    shorter = min(a["end"] - a["start"], b["end"] - b["start"])
    return inter > 0 and inter >= 0.5 * shorter


def counted_preds(spans: list[dict], mode: str) -> list[dict]:
    return [s for s in spans if mode == "lenient" or not s.get("reported_speech")]


def counted_gold(spans: list[dict], mode: str) -> list[dict]:
    return [s for s in spans if mode == "lenient" or s.get("reported_speech") is not True]


def prf(tp_p: int, n_p: int, tp_g: int, n_g: int) -> dict:
    p = tp_p / n_p if n_p else None
    r = tp_g / n_g if n_g else None
    f = 2 * p * r / (p + r) if p and r else (0.0 if (p is not None and r is not None) else None)
    rnd = lambda x: None if x is None else round(x, 3)  # noqa: E731
    return {"precision": rnd(p), "recall": rnd(r), "f1": rnd(f), "predicted": n_p, "gold": n_g,
            "tp_pred": tp_p, "tp_gold": tp_g}


def span_scores(items: list[dict], preds: dict[str, list[dict]], mode: str) -> dict:
    per = defaultdict(lambda: [0, 0, 0, 0])  # tp_pred, n_pred, tp_gold, n_gold
    for it in items:
        gold = counted_gold(it["spans"], mode)
        pred = counted_preds(preds.get(it["id"], []), mode)
        for p in pred:
            c = per[p["label"]]
            c[1] += 1
            if any(g["label"] == p["label"] and overlap_ok(g, p) for g in gold):
                c[0] += 1
        for g in gold:
            c = per[g["label"]]
            c[3] += 1
            if any(p["label"] == g["label"] and overlap_ok(g, p) for p in pred):
                c[2] += 1
    totals = [sum(c[i] for c in per.values()) for i in range(4)]
    return {"micro": prf(*totals), "per_label": {lbl: prf(*c) for lbl, c in sorted(per.items())}}


def clean_fpr(items: list[dict], preds: dict[str, list[dict]]) -> dict:
    techniques = {t["label"] for t in taxonomy.load()["techniques"] if t["kind"] == "technique"}
    out = {}
    sets = defaultdict(list)
    for it in items:
        if it.get("clean_set"):
            sets[it["clean_set"]].append(it)
    for name, its in sorted(sets.items()):
        any_flag = tech_flag = n_flags = words = 0
        for it in its:
            # spans the gold itself marks (supported claims, balance, quoted words) are not false positives
            allowed = [g for g in it["spans"] if g["label"] not in techniques or g.get("reported_speech")]
            flagged = [p for p in counted_preds(preds.get(it["id"], []), "strict") if p.get("color") in FLAGGED
                       and not any(g["label"] == p["label"] and overlap_ok(g, p) for g in allowed)]
            words += len(it["text"].split())
            n_flags += len(flagged)
            any_flag += bool(flagged)
            tech_flag += any(p["label"] in techniques for p in flagged)
        n = len(its)
        out[name] = {"paragraphs": n, "flagged_any": any_flag, "fpr_any": round(any_flag / n, 3) if n else None,
                     "flagged_techniques": tech_flag, "fpr_techniques": round(tech_flag / n, 3) if n else None,
                     "flags_per_1000_words": round(n_flags / words * 1000, 2) if words else None}
    return out


def symmetry(pairs: list[dict], preds: dict[str, list[dict]]) -> dict:
    flipped, examples = 0, []
    for p in pairs:
        la = sorted({s["label"] for s in counted_preds(preds.get(p["pair_id"] + ":a", []), "strict") if s.get("color") in FLAGGED})
        lb = sorted({s["label"] for s in counted_preds(preds.get(p["pair_id"] + ":b", []), "strict") if s.get("color") in FLAGGED})
        if la != lb:
            flipped += 1
            examples.append({"pair_id": p["pair_id"], "a": la, "b": lb})
    n = len(pairs)
    return {"pairs": n, "flipped": flipped, "flip_rate": round(flipped / n, 3) if n else None, "examples": examples[:10]}


def by_source(items: list[dict]) -> dict[str, list[dict]]:
    groups = defaultdict(list)
    for it in items:
        groups[it["source"]].append(it)
    return dict(groups)


def compute_metrics(items, pairs, preds, runs) -> dict:
    scores = {}
    for mode in ("strict", "lenient"):
        scores[mode] = {"all": span_scores(items, preds, mode),
                        "by_source": {src: span_scores(its, preds, mode) for src, its in sorted(by_source(items).items())}}
    all_runs = list(runs.values())
    n = len(all_runs) or 1
    proposed = sum(r["n_spans"] + r["rejections"] for r in all_runs)
    return {
        "scores": scores,
        "clean_fpr": clean_fpr(items, preds),
        "symmetry": symmetry(pairs, preds),
        "validator_rejection_rate": round(sum(r["rejections"] for r in all_runs) / proposed, 3) if proposed else None,
        "abstention_rate": round(sum(r["n_spans"] == 0 for r in all_runs) / n, 3),
        "cost": {"analyses": len(all_runs), "llm_calls": sum(r["llm_calls"] for r in all_runs),
                 "credits_per_call": round(sum(r.get("credits", 0.0) for r in all_runs)
                                           / max(1, sum(r["llm_calls"] for r in all_runs)), 4),
                 "input_tokens": sum(r["input_tokens"] for r in all_runs),
                 "output_tokens": sum(r["output_tokens"] for r in all_runs),
                 "mean_tokens_per_analysis": round(sum(r["input_tokens"] + r["output_tokens"] for r in all_runs) / n, 1),
                 "mean_latency_s": round(sum(r["latency"] for r in all_runs) / n, 2),
                 "credits": round(sum(r.get("credits", 0.0) for r in all_runs), 3),
                 "mean_credits_per_analysis": round(sum(r.get("credits", 0.0) for r in all_runs) / n, 4)},
        "llm_failures": sum(r["mode"] != "llm" for r in all_runs),
    }


# ----------------------------------------------------------------- engine

def texts_to_run(items, pairs) -> dict[str, str]:
    texts = {it["id"]: it["text"] for it in items}
    for p in pairs:
        texts[p["pair_id"] + ":a"] = p["a"]["text"]
        texts[p["pair_id"] + ":b"] = p["b"]["text"]
    return texts


def plan_calls(texts: dict[str, str], model: str) -> int:
    from app.pipeline import tagger
    from app.pipeline.segment import segment
    from app.store import get_store
    store = get_store()
    calls, seen = 0, set()
    for text in texts.values():
        seg = segment(text)
        todo = []
        for p in seg.paragraphs:
            key = tagger.cache_key(tagger.paragraph_hash(p.text), model)
            if key not in seen and store.cache_get(key) is None:
                seen.add(key)
                todo.append(p)
        calls += len(tagger.chunk_paragraphs(todo)) if todo else 0
    return calls


def read_credit_rates(path: Path | None = None) -> dict:
    path = path or CREDIT_RATES
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_credit_rate(model: str, runs: dict, path: Path | None = None) -> None:
    path = path or CREDIT_RATES
    calls = sum(r["llm_calls"] for r in runs.values())
    if not calls:
        return
    credits = sum(r.get("credits", 0.0) for r in runs.values())
    rates = read_credit_rates(path)
    old = rates.get(model, {})
    total_calls = old.get("calls_measured", 0) + calls
    total_credits = old.get("credits_measured", 0.0) + credits
    rates[model] = {"credits_per_call": round(total_credits / total_calls, 4), "calls_measured": total_calls,
                    "credits_measured": round(total_credits, 4),
                    "updated": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rates, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def projected_rate(model: str, rates: dict) -> tuple[float | None, str]:
    """(credits per call, how we know). Unmeasured models get the highest measured rate as a cautious guess."""
    if model in rates:
        return rates[model]["credits_per_call"], f"measured over {rates[model]['calls_measured']} calls"
    known = {m: r["credits_per_call"] for m, r in rates.items()}
    if known:
        m = max(known, key=known.get)
        return known[m], f"NOT measured yet; guessed from {m}"
    return None, "unknown (no measurement yet)"


async def predict(texts: dict[str, str], sensitivity: str, guard_rate: float | None = None) -> tuple[dict, dict]:
    """guard_rate: projected credits per call; the first analysis runs alone and the run stops (CostGuardStop) if
    it failed or cost more than COST_GUARD_FACTOR x the projection."""
    from app.analyze import analyze_text
    preds, runs = {}, {}
    sem = asyncio.Semaphore(3)

    async def one(key, text):
        async with sem:
            t0 = time.monotonic()
            r = await analyze_text(text, sensitivity=sensitivity, count_seen=False)
            runs[key] = {"latency": time.monotonic() - t0, "mode": r["mode"], "n_spans": len(r["spans"]),
                         "rejections": r["stats"]["validator_rejections"], "llm_calls": r["cost"]["llm_calls"],
                         "input_tokens": r["cost"]["input_tokens"], "output_tokens": r["cost"]["output_tokens"],
                         "credits": r["cost"].get("credits", 0.0), "mode_reason": r.get("mode_reason")}
            preds[key] = r["spans"]

    keys = list(texts)
    if guard_rate is not None and keys:
        first = keys.pop(0)
        await one(first, texts[first])
        r = runs[first]
        if r["mode"] != "llm":
            raise CostGuardStop(f"first analysis did not use the model (mode_reason={r['mode_reason']})", runs)
        if r["llm_calls"] and r["credits"] / r["llm_calls"] > COST_GUARD_FACTOR * guard_rate:
            raise CostGuardStop(f"first call cost {r['credits'] / r['llm_calls']:.4f} credits, projection "
                                f"{guard_rate:.4f} (> {COST_GUARD_FACTOR}x)", runs)
    await asyncio.gather(*(one(k, texts[k]) for k in keys))
    return preds, runs


# -------------------------------------------------------- taxonomy update

def update_dev_precision(per_label: dict, path: Path = TECHNIQUES_YAML) -> dict:
    """Write dev_precision / experimental for labels with enough predictions (text edit keeps comments)."""
    updates = {lbl: s["precision"] for lbl, s in per_label.items()
               if s["predicted"] >= MIN_PRED_FOR_PRECISION and s["precision"] is not None}
    if not updates:
        return {}
    lines = path.read_text(encoding="utf-8").splitlines()
    current = None
    for i, line in enumerate(lines):
        m = re.match(r"\s*- label: (\w+)", line)
        if m:
            current = m.group(1)
        elif current in updates and re.match(r"\s+dev_precision:", line):
            lines[i] = re.sub(r"dev_precision:.*", f"dev_precision: {updates[current]}", line)
        elif current in updates and re.match(r"\s+experimental:", line):
            lines[i] = re.sub(r"experimental:.*", f"experimental: {str(updates[current] < PRECISION_THRESHOLD).lower()}", line)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    taxonomy.load.cache_clear()
    return updates


# ------------------------------------------------------------------- main

def _load_env_local() -> None:
    path = ROOT / ".env.local"
    if not path.exists() or os.environ.get("ROUTER_API_KEY"):
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.split(" #", 1)[0].strip().strip("'\""))


def git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short=8", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    except OSError:
        return "unknown"


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--final", action="store_true", help="LOCKED test split")
    ap.add_argument("--models", default="", help="comma-separated bake-off models (default: MODEL_TAGGER)")
    ap.add_argument("--sensitivity", default="broad", choices=["strict", "balanced", "broad"])
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--bakeoff", action="store_true",
                    help=f"fixed mixed subset ({BAKEOFF_SUBSET.name}); default models: {DEFAULT_BAKEOFF_MODELS}")
    args = ap.parse_args(argv)
    if args.bakeoff and (args.final or args.limit):
        ap.error("--bakeoff runs on its fixed dev subset; do not combine with --final or --limit")

    from app.store import Store, set_store
    set_store(Store(EVAL_DB))  # separate cache from production
    items, pairs = load(args.final, LANG, args.limit)
    if not items:
        print("No gold items. Run: python eval/sheet_to_jsonl.py")
        return 2
    if args.bakeoff:
        items, pairs = load_bakeoff(items, pairs)
    default_models = DEFAULT_BAKEOFF_MODELS if args.bakeoff else os.environ.get("MODEL_TAGGER", "claude-haiku-4.5-eu")
    models = [m.strip() for m in (args.models or default_models).split(",") if m.strip()]
    bakeoff_mode = args.bakeoff or len(models) > 1
    texts = texts_to_run(items, pairs)
    print(f"Split: {'TEST (final)' if args.final else 'dev'}{' (bake-off subset)' if args.bakeoff else ''}  "
          f"language: {LANG}  items: {len(items)} "
          f"{dict(Counter(i['source'] for i in items))}  clean: {dict(Counter(i['clean_set'] for i in items if i['clean_set']))}  "
          f"symmetry pairs: {len(pairs)}")
    total_calls, total_credits, unknown = 0, 0.0, False
    rates = read_credit_rates()
    guard = {}
    for m in models:
        calls = plan_calls(texts, m)
        total_calls += calls
        rate, how = projected_rate(m, rates)
        guard[m] = rate
        if rate is None:
            unknown = True
            credit_txt = "credits unknown"
        else:
            total_credits += calls * rate
            credit_txt = f"~{calls * rate:.1f} credits ({rate:.3f}/call, {how})"
        print(f"  {m}: {len(texts)} analyses -> {calls} uncached LLM calls, ~{calls * EST_IN:,} in / "
              f"~{calls * EST_OUT:,} out tokens, {credit_txt}")
    print(f"Total: {total_calls} LLM calls, ~{total_credits:.1f} credits{' + unknown' if unknown else ''}")
    if not args.yes:
        print("\nPlan only. Re-run with --yes after the OK.")
        return 0
    _load_env_local()  # only now: the plan never needs the key
    if not os.environ.get("ROUTER_API_KEY"):
        print("ROUTER_API_KEY not set (.env.local missing?)")
        return 2

    from app.pipeline import tagger
    results, bakeoff = {}, []
    for m in models:
        os.environ["MODEL_TAGGER"] = m
        tagger.reset_inflight()
        try:
            preds, runs = asyncio.run(predict(texts, args.sensitivity, guard_rate=guard.get(m)))
        except CostGuardStop as stop:
            msg, partial = stop.args
            save_credit_rate(m, partial)
            print(f"STOPPED at {m}: {msg}. Nothing written except the measured credit rate. Ask before continuing.")
            return 3
        save_credit_rate(m, runs)
        metrics = compute_metrics(items, pairs, preds, runs)
        results[m] = metrics
        micro = metrics["scores"]["strict"]["all"]["micro"]
        bakeoff.append({"model": m, **{k: micro[k] for k in ("precision", "recall", "f1")},
                        "fpr_semeval_none": metrics["clean_fpr"].get("semeval_none", {}).get("fpr_any"),
                        "mean_tokens_per_analysis": metrics["cost"]["mean_tokens_per_analysis"],
                        "mean_credits_per_analysis": metrics["cost"]["mean_credits_per_analysis"],
                        "mean_latency_s": metrics["cost"]["mean_latency_s"],
                        "symmetry_flip_rate": metrics["symmetry"].get("flip_rate"),
                        "validator_rejection_rate": metrics["validator_rejection_rate"],
                        "llm_failures": metrics["llm_failures"]})
        print(f"  {m}: strict micro P={micro['precision']} R={micro['recall']} F1={micro['f1']}  "
              f"credits={metrics['cost']['credits']} ({metrics['cost']['llm_calls']} calls)")

    primary = models[0]
    out = {
        "git_commit": git_commit(), "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "split": "test" if args.final else "dev", "language": LANG, "model": primary, "sensitivity": args.sensitivity,
        "prompt_version": tagger.prompt_version(), "taxonomy_version": taxonomy.version(),
        "counts": {"items": len(items), "by_source": dict(Counter(i["source"] for i in items)),
                   "clean_sets": dict(Counter(i["clean_set"] for i in items if i["clean_set"])), "symmetry_pairs": len(pairs)},
        **results[primary],
        "gold_report": _read(RESULTS / "gold_report.json"),
        "ai_review": _read(RESULTS / "ai_review.json"),
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    dump = lambda path, data: path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    if bakeoff_mode:  # never gates techniques and never replaces latest.json
        out["subset"] = "bakeoff_subset" if args.bakeoff else ("sample" if args.limit else "full")
        out["models"] = bakeoff
        for m in models:
            dump(RESULTS / f"bakeoff_{re.sub(r'[^\w.-]', '_', m)}.json", {**out, "model": m, **results[m]})
        target = RESULTS / "bakeoff.json"
        dump(target, {k: v for k, v in out.items() if k not in ("gold_report", "ai_review")})
    elif args.limit:  # a small sample: kept for inspection, never shown on /metrics, never gates
        target = RESULTS / "sample.json"
        dump(target, out)
    else:
        if not args.final:
            out["dev_precision_updates"] = update_dev_precision(results[primary]["scores"]["strict"]["all"]["per_label"])
        target = RESULTS / "latest.json"
        dump(target, out)
    print(f"Wrote {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
