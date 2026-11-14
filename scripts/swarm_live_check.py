"""ONE live collection through a swarm collector (no AI calls), for manual checks.

  python scripts/swarm_live_check.py <post-url> [--record DIR] [--snapshot web/static/examples/x.json]
  python scripts/swarm_live_check.py <post-url> --replay DIR --snapshot ... --pseudonymize   # no network

Prints what was collected and the swarm summary. --record saves every raw API response (query keys
removed) for building test fixtures; --snapshot saves the collected posts as a demo example file
({"source": ..., "posts": [...]}) so demos never fetch live.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.swarm import clusters, collectors, ingest  # noqa: E402
from app.swarm.collectors import http as chttp  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--record")
    ap.add_argument("--snapshot")
    ap.add_argument("--replay", help="answer from responses recorded earlier with --record (no network)")
    ap.add_argument("--pseudonymize", action="store_true",
                    help="snapshot: replace every account except the linked post's author with a digit-free word pair")
    args = ap.parse_args()
    recorded = []
    original = chttp.Session.get_json
    if args.replay:
        replies = [json.loads(f.read_text(encoding="utf-8"))["response"] for f in sorted(Path(args.replay).glob("*.json"))]

        async def original(self, url, params=None, headers=None):  # noqa: F811 - replay instead of network
            self.requests += 1
            return replies.pop(0)

    async def recording(self, url, params=None, headers=None):
        data = await original(self, url, params=params, headers=headers)
        safe = [(k, v) for k, v in (params.items() if isinstance(params, dict) else params or []) if k != "key"]
        recorded.append({"url": url, "params": safe, "response": data})
        return data
    chttp.Session.get_json = recording

    coll, _ = asyncio.run(collectors.collect_url(args.url, progress=lambda m: print("  progress:", m)))
    print(f"platform={coll.platform} posts={len(coll.posts)} accounts={len({p.author for p in coll.posts})} "
          f"requests={coll.requests} complete={coll.complete}")
    print("root:", json.dumps(coll.root, ensure_ascii=False)[:300])
    print("notes:", coll.notes)
    dated = sum(p.author_created_at is not None for p in coll.posts)
    print(f"posts with account creation date: {dated}/{len(coll.posts)}")
    r = asyncio.run(clusters.analyze_batch(ingest.Batch(posts=list(coll.posts), rows_total=len(coll.posts))))
    print("swarm summary:", r["summary"]["by_verdict"], "clusters:", r["summary"]["clusters"], f"{r['elapsed_ms']} ms")
    for c in r["clusters"][:8]:
        print(f"  {c['id']} {c['verdict']} {c['families']} {c['innocent_patterns']} | {c['headline']} | "
              f"{c['representative_text'][:80]!r}")
    if args.record:
        out = Path(args.record)
        out.mkdir(parents=True, exist_ok=True)
        for i, rec in enumerate(recorded):
            (out / f"{coll.platform}_{i:02d}.json").write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
        print(f"recorded {len(recorded)} responses -> {out}")
    if args.snapshot:
        names: dict[str, str] = {}
        adjectives = ("quiet amber brisk calm deep early fair gentle hidden ivory jolly keen lucky mellow noble olive "
                      "plain quick rapid silver tidy upbeat vivid warm young zesty").split()
        nouns = ("otter falcon maple harbor comet meadow pebble river lantern willow heron cedar ember fjord grove "
                 "island juniper kestrel lagoon marble nectar orchid prairie quartz").split()

        def alias(author: str) -> str:
            if not args.pseudonymize or author == (coll.root or {}).get("author"):
                return author
            if author not in names:
                n = len(names)
                names[author] = f"{adjectives[n % 25]}{nouns[(n // 25) % 25]}{'' if n < 625 else chr(97 + n // 625)}.example"
            return names[author]
        snap = {"source": {"platform": coll.platform, "url": args.url, "root": coll.root,
                           "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                           "note": "Snapshot of public posts, stored so demos never fetch live."},
                "posts": [{"id": f"{coll.platform}-{i:04d}" if args.pseudonymize else p.id, "author": alias(p.author), "timestamp": datetime.fromtimestamp(p.ts, timezone.utc).isoformat(),
                           "text": p.text, "url": p.url or "",
                           "author_created_at": datetime.fromtimestamp(p.author_created_at, timezone.utc).isoformat()
                           if p.author_created_at else "", "platform": p.platform} for i, p in enumerate(coll.posts)]}
        if args.pseudonymize:
            snap["source"]["note"] += (" Account names other than the original poster are replaced by pseudonyms; "
                                       "texts, times and account creation dates are unchanged.")
        path = Path(args.snapshot)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(snap, ensure_ascii=False, indent=0), encoding="utf-8")
        print(f"snapshot -> {path} ({path.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
