"""Self-consistency baseline: analyze the English symmetry "a" variants a SECOND time with the cache bypassed.

  python eval/self_consistency.py          # plan (no calls)
  python eval/self_consistency.py --yes    # run (DEV key from .env.local)

Why: the symmetry flip rate (a vs b = same text with the sides swapped) mixes two things, bias and plain
run-to-run noise. Re-running the identical "a" texts measures the noise alone ("self-flip rate"): if the
symmetry flip rate is not clearly above it, the flips are noise, not evidence of a political lean.

The second run uses its own cache (eval/data/cache/selfcons.db), so it never reads the first run's results;
the first run stays in eval/data/cache/eval.db. Rates per sensitivity level are computed by eval/calibrate.py
from both caches (no calls). Writes eval/results/self_consistency.json (run metadata + broad-level result).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_eval as r  # noqa: E402

SELF_DB = r.ROOT / "eval" / "data" / "cache" / "selfcons.db"
OUT = r.RESULTS / "self_consistency.json"


def a_texts(pairs: list[dict]) -> dict[str, str]:
    return {p["pair_id"] + ":a": p["a"]["text"] for p in pairs}


def label_sets(preds: dict[str, list[dict]]) -> dict[str, list[str]]:
    """Same label-set definition as run_eval.symmetry (flagged, reported speech excluded)."""
    return {k: sorted({s["label"] for s in r.counted_preds(v, "strict") if s.get("color") in r.FLAGGED})
            for k, v in preds.items()}


def self_flip(first: dict[str, list[dict]], second: dict[str, list[dict]]) -> dict:
    a, b = label_sets(first), label_sets(second)
    keys = sorted(set(a) & set(b))
    flipped = [k for k in keys if a[k] != b[k]]
    return {"texts": len(keys), "flipped": len(flipped),
            "self_flip_rate": round(len(flipped) / len(keys), 3) if keys else None,
            "examples": [{"id": k, "run1": a[k], "run2": b[k]} for k in flipped[:10]]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--model", default=os.environ.get("MODEL_TAGGER", "gemini-3.5-flash-lite-eu"))
    args = ap.parse_args(argv)

    from app.store import Store, set_store
    _, pairs = r.load(False, r.LANG)
    texts = a_texts(pairs)
    if not texts:
        print("No English symmetry pairs. Run: python eval/sheet_to_jsonl.py")
        return 2
    set_store(Store(SELF_DB))
    calls = r.plan_calls(texts, args.model)
    rate, how = r.projected_rate(args.model, r.read_credit_rates())
    credit_txt = f"~{calls * rate:.1f} credits ({how})" if rate is not None else "credits unknown"
    print(f"Self-consistency: {len(texts)} symmetry 'a' texts, model {args.model}, separate cache {SELF_DB.name}")
    print(f"  {calls} uncached LLM calls, {credit_txt}")
    if not args.yes:
        print("\nPlan only. Re-run with --yes after the OK.")
        return 0
    r._load_env_local()
    if not os.environ.get("ROUTER_API_KEY"):
        print("ROUTER_API_KEY not set (.env.local missing?)")
        return 2
    os.environ["MODEL_TAGGER"] = args.model
    try:
        second, runs = asyncio.run(r.predict(texts, "broad", guard_rate=rate))
    except r.CostGuardStop as stop:
        msg, partial = stop.args
        r.save_credit_rate(args.model, partial)
        print(f"STOPPED: {msg}")
        return 3
    r.save_credit_rate(args.model, runs)

    # first run: the normal eval cache (cache-only, never calls)
    set_store(Store(r.EVAL_DB))
    if r.plan_calls(texts, args.model):
        print("The first run is not in the eval cache; run run_eval.py first.")
        return 2
    r._cache_only_mode()
    first, _ = asyncio.run(r.predict(texts, "broad"))
    result = {"model": args.model, "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "git_commit": r.git_commit(), "llm_calls": sum(x["llm_calls"] for x in runs.values()),
              "credits": round(sum(x.get("credits", 0.0) for x in runs.values()), 3),
              "broad": self_flip(first, second)}
    OUT.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  self-flip (broad): {result['broad']['flipped']}/{result['broad']['texts']}  credits={result['credits']}")
    print(f"Wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
