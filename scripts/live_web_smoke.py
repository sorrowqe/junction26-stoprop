"""Live verification of Stoprop's web search, anti-bot scraping, social URL reading, and WHOIS receipts.

IMPORTANT: Makes ZERO calls to the Hostinger AI Router (0.00 LLM credits).
Uses at most:
- 2 Brave Search queries (if BRAVE_API_KEY is set in .env.local),
- 1 Oxylabs AI Studio `/scrape` call (1 credit out of 1,000, if SEARCH_API_KEY is set),
- 1 public Bluesky post fetch + 1 port-43 WHOIS check for `err.ee`.
Never prints any API keys.
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "eval"), str(ROOT / "backend")]

import run_eval  # noqa: E402

run_eval._load_env_local()

from app import config  # noqa: E402
from app.pipeline import extract, oxylabs, websearch  # noqa: E402
from app.receipts import service as receipts_service  # noqa: E402
from app.store import Store, set_store  # noqa: E402
from app.swarm.collectors.http import Session  # noqa: E402


async def main() -> int:
    set_store(Store(":memory:"))
    print("Configured providers (no secrets shown):")
    print("  active_search_provider =", config.active_search_provider())
    print("  brave_configured       =", bool(config.brave_api_key()))
    print("  oxylabs_configured     =", oxylabs.configured())

    # 1. Search check (Brave News + Web, or Oxylabs Instant)
    if config.active_search_provider() != "none":
        t0 = time.monotonic()
        async with Session(max_requests=4, deadline_s=15.0) as s:
            hits = await websearch.search_web(s, "Estonia site:err.ee", kind="news", days=30, limit=5)
        dt = round((time.monotonic() - t0) * 1000)
        print(f"\n[1] Search ('Estonia site:err.ee', 30d): {len(hits)} hits in {dt} ms")
        for h in hits[:2]:
            print(f"    - {h['domain']} | {h['title'][:70]} | ts={h['ts'] is not None} | snippets={len(h['snippets'])}")

    # 2. Direct Tier-1 fetch check
    t0 = time.monotonic()
    art_direct = await extract.fetch_article("https://news.err.ee/")
    print(f"\n[2] Fetch URL (news.err.ee): via={art_direct.via}, words={len(art_direct.text.split())}, "
          f"time={round((time.monotonic() - t0) * 1000)} ms")

    # 3. Tier-2 Oxylabs AI Studio scraper check (on an article page that blocks plain bots / needs JS)
    if oxylabs.configured():
        t0 = time.monotonic()
        try:
            text, title = await oxylabs.scrape_markdown("https://www.bbc.com/news", timeout_s=30.0)
            print(f"\n[3] Oxylabs AI Studio scrape (bbc.com/news): words={len(text.split())}, "
                  f"title={str(title)[:60]!r}, time={round((time.monotonic() - t0) * 1000)} ms, "
                  f"calls_today={oxylabs.calls_today()}")
        except oxylabs.OxylabsError as exc:
            print(f"\n[3] Oxylabs AI Studio scrape note: {exc.code} ({exc.message}) after {round((time.monotonic() - t0) * 1000)} ms")

    # 4. Tier-0 Social URL shortcut check (Bluesky public post)
    t0 = time.monotonic()
    bsky_url = "https://bsky.app/profile/bsky.app/post/3l6oveex3ii2l"
    try:
        art_social = await extract.fetch_article(bsky_url)
        print(f"\n[4] Social URL shortcut ({bsky_url}): via={art_social.via}, "
              f"words={len(art_social.text.split())}, time={round((time.monotonic() - t0) * 1000)} ms")
    except extract.FetchError as exc:
        print(f"\n[4] Social URL shortcut note: {exc.code} ({exc.message})")

    # 5. `/source` receipts check for `err.ee` (verifies port-43 WHOIS fallback for .ee)
    t0 = time.monotonic()
    rec = await receipts_service.get_receipts("err.ee")
    print(f"\n[5] Source receipts (err.ee) in {round((time.monotonic() - t0) * 1000)} ms:")
    for r in rec["receipts"]:
        print(f"    - {r['type']}: [{r['status']}/{r['flag']}] {r['text'][:90]}")

    print("\nAI Router LLM calls made: 0 (0.00 credits)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
