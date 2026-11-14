"""Daily token cap -> heuristics-only fallback (CLAUDE.md §4 "Budget guard").

mode: "ok" (< 70% of cap) · "high" (>= 70%: receipts double their soft TTLs, M3) ·
"over" (>= 100%: no LLM calls, heuristics only + UI banner).
"""
from __future__ import annotations

from app import config
from app.store import get_store

HIGH_SHARE = 0.7


def status() -> dict:
    usage = get_store().usage_today()
    cap = config.daily_token_cap()
    used = usage["tokens"]
    share = (used / cap) if cap else 1.0
    mode = "over" if share >= 1.0 else ("high" if share >= HIGH_SHARE else "ok")
    return {
        "mode": mode,
        "tokens_today": used,
        "cap": cap,
        "share": round(share, 4),
        "calls_today": usage["calls"],
        "errors_today": usage["errors"],
        "day": "UTC",
    }


def llm_available() -> tuple[bool, str]:
    """(available, reason) where reason is "ok" | "no_key" | "budget"."""
    if not config.llm_configured():
        return False, "no_key"
    if status()["mode"] == "over":
        return False, "budget"
    return True, "ok"
