"""ONE live trace for manual checks: python scripts/trace_live_check.py (--url URL | --text TEXT) [--record DIR]

Without ROUTER_API_KEY / SEARCH_API_KEY in the environment this makes NO AI and NO Oxylabs calls: the
word-rarity rule picks the sentences and only Bluesky's free public search runs. Load .env.local yourself
(and announce the calls first) to include the AI fingerprint and open-web search.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.swarm.collectors import http as chttp  # noqa: E402
from app.trace.run import trace  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url")
    ap.add_argument("--text")
    ap.add_argument("--record")
    args = ap.parse_args()
    recorded = []
    original = chttp.Session.request_json

    async def recording(self, method, url, **kw):
        data = await original(self, method, url, **kw)
        params = kw.get("params")
        safe = [(k, v) for k, v in (params.items() if isinstance(params, dict) else params or []) if k != "key"]
        recorded.append({"method": method, "url": url, "params": safe, "response": data})
        return data
    chttp.Session.request_json = recording
    r = asyncio.run(trace(text=args.text, url=args.url, progress=lambda m: print("  progress:", m)))
    print("fingerprints:", r["fingerprint_method"])
    for f in r["fingerprints"]:
        print(f"  [{f['copies']} copies] {f['query']}")
    for c in r["coverage"]:
        print(f"  {c['source']}: {c['status']} searches={c['searches']} results={c['results']} confirmed={c['confirmed']}"
              f"{' | ' + c['note'] if c['note'] else ''}")
    sp = r["spread"]
    print(f"copies={sp['copies']} unconfirmed={sp['unconfirmed']} related={sp['related_not_copies']} "
          f"accounts={len(sp['accounts'])} domains={len(sp['domains'])} first_seen={sp['first_seen']}")
    print("verdict:", r["verdict"], (r["cluster"] or {}).get("families"), (r["cluster"] or {}).get("innocent_patterns"))
    print("headline:", (r["cluster"] or {}).get("headline"))
    print("notes:", *r["notes"], sep="\n  ")
    print("cost:", r["cost"], f"{r['elapsed_ms']} ms")
    if args.record:
        out = Path(args.record)
        out.mkdir(parents=True, exist_ok=True)
        for i, rec in enumerate(recorded):
            (out / f"trace_{i:02d}.json").write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
        print(f"recorded {len(recorded)} responses -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
