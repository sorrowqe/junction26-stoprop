"""Live claim-verification checks (AI + GDELT, dev key from .env.local). Announce the calls before running.

  python scripts/verify_live_check.py "text 1" ["text 2" ...]

Prints each check's verdicts, evidence, time and credits; stops after the first check if it cost > 2 credits.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "eval"), str(ROOT / "backend")]

import run_eval  # noqa: E402

run_eval._load_env_local()
from app.store import Store, set_store  # noqa: E402
from app.verify.run import verify  # noqa: E402

COST_GUARD = 2.0


async def main(texts: list[str]) -> int:
    set_store(Store(":memory:"))
    total = 0.0
    for n, text in enumerate(texts, start=1):
        print(f"\n=== Check {n}: {text}")
        r = await verify(text, progress=lambda m: print("   progress:", m))
        for c in r["claims"]:
            print(f" - [{c['verdict']}] {c['claim']}  (cited: {c['cited_source']} {c['cited_domains']})")
            print(f"   {c['text']}")
            for e in c["evidence"]:
                print(f"     {e['stance']}: {e['domain']} | {e['title'][:80]} | read={e['read']} | “{e['quote'][:100]}”")
            print(f"   unrelated articles judged: {len(c['also_found'])}; searched: {c['searched']}")
        for s in r["spans"]:
            print(f" + span {s['label']} [{s['start']}:{s['end']}] {s['quote']!r}")
        print(" notes:", *r["notes"], sep="\n   ")
        print(f" cost: {r['cost']}  time: {r['elapsed_ms'] / 1000:.1f} s")
        total += r["cost"]["credits"]
        if n == 1 and r["cost"]["credits"] > COST_GUARD:
            print(f"STOP: first check cost {r['cost']['credits']} credits (> {COST_GUARD}).")
            return 3
    print(f"\nTotal credits: {total:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
