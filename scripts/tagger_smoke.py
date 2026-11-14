"""ONE real tagger call on a demo sample (default: the manipulative one). Costs ~3k tokens.

Uses an in-memory store, so nothing is cached or counted in the app's database.
  PC:      set -a; source .env.local; set +a; PYTHONPATH=backend .venv/Scripts/python scripts/tagger_smoke.py
  Server:  cd /docker/stoprop && docker compose exec stoprop python scripts/tagger_smoke.py
Optional argument: sample id (manipulative | neutral | opinion).
"""
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import config  # noqa: E402
from app.analyze import analyze_text  # noqa: E402
from app.store import Store, get_store, set_store  # noqa: E402


def main() -> int:
    if not config.llm_configured():
        print("ROUTER_API_KEY / ROUTER_BASE_URL / MODEL_TAGGER not set - nothing called.")
        return 2
    sample_id = sys.argv[1] if len(sys.argv) > 1 else "manipulative"
    samples = {s["id"]: s["text"] for s in json.loads((ROOT / "web/static/samples.json").read_text(encoding="utf-8"))}
    set_store(Store(":memory:"))
    print(f"One call to {config.model_tagger()} on sample '{sample_id}' ...")
    t0 = time.monotonic()
    r = asyncio.run(analyze_text(samples[sample_id], sensitivity="broad", private=True, count_seen=False))
    print(f"mode={r['mode']} ({r['mode_reason']})  {time.monotonic() - t0:.1f}s  cost={r['cost']}")
    print(f"validator_rejections={r['stats']['validator_rejections']}  prompt_version={r['stats']['prompt_version']}")
    for s in r["spans"]:
        print(f"  [{s['label']} {s['confidence']:.2f} {s['color']}{' RS' if s['reported_speech'] else ''}] "
              f"\"{s['quote']}\" - {s['explanation']}")
    if not r["spans"]:
        print("  (no spans)")
    for n in r["notes"]:
        print("  note:", n)
    print("usage log (one row per HTTP attempt):")
    for row in get_store().usage_log():
        print("  ", json.dumps(row))
    return 0 if r["mode"] == "llm" else 1


if __name__ == "__main__":
    sys.exit(main())
