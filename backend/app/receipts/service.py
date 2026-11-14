"""Receipts orchestration: concurrency, per-type TTLs, soft/hard expiry, negative caching,
single-flight and the global 15-minute Refresh limit (CLAUDE.md §4 "Caching & freshness").

- soft TTL: serve the cached value and refresh it in the background.
- hard TTL: refresh before serving.
- errors / not_found are cached for 10 minutes (negative caching).
- budget mode "high" (>= 70% of the daily AI cap): soft TTLs x2 (fewer outbound calls).
- Refresh: allowed only if the item was checked more than 15 minutes ago (global, per item).
- lookalike is a pure function of outlets.yaml (recomputed every time, so it changes exactly when
  the file changes); fact_checks without a key and syndication are "not_checked" (never cached).
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from datetime import datetime, timezone

from app.llm import budget
from app.receipts import crtsh, factcheck, lookalike, rdap, syndication, wayback
from app.receipts.domain import normalize_host, registrable
from app.store import get_store

log = logging.getLogger("stoprop.receipts")

HOUR = 3600
DAY = 24 * HOUR
TTLS = {  # type: (soft, hard) seconds
    "domain_age": (7 * DAY, 28 * DAY),
    "first_seen_archive": (7 * DAY, 28 * DAY),
    "first_certificate": (7 * DAY, 28 * DAY),
    "fact_checks": (12 * HOUR, 48 * HOUR),
}
NEGATIVE_TTL = 10 * 60
REFRESH_MIN_AGE = 15 * 60
TITLES = {
    "lookalike": "Look-alike check",
    "domain_age": "Domain age",
    "first_seen_archive": "First archived",
    "first_certificate": "First certificate",
    "fact_checks": "Fact-checks",
    "syndication": "Copies elsewhere",
}
ORDER = ("lookalike", "domain_age", "first_seen_archive", "first_certificate", "fact_checks", "syndication")


def clock() -> float:
    """Wall clock (seconds). Tests monkeypatch this."""
    return time.time()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


_inflight: dict[str, asyncio.Future] = {}
_background: set[asyncio.Task] = set()


async def _compute_and_store(key: str, rtype: str, fetch) -> dict:
    """Single-flight: concurrent callers for the same key share one computation."""
    if key in _inflight:
        return await asyncio.shield(_inflight[key])
    fut = asyncio.get_running_loop().create_future()
    _inflight[key] = fut
    try:
        try:
            data = await fetch()
        except Exception:  # noqa: BLE001 - a receipt source must never break the request
            log.exception("receipt %s failed", rtype)
            data = {"status": "error", "flag": "neutral", "evidence_url": None,
                    "text": "This check failed unexpectedly. Try Refresh later."}
        now = clock()
        if data.get("status") in ("error", "not_found"):
            soft = hard = now + NEGATIVE_TTL
        else:
            soft_ttl, hard_ttl = TTLS[rtype]
            if budget.status()["mode"] in ("high", "over"):
                soft_ttl *= 2
            soft, hard = now + soft_ttl, now + max(hard_ttl, soft_ttl)
        get_store().receipt_put(key, data, now, soft, hard)
        result = {"data": data, "checked_at": now}
        fut.set_result(result)
        return result
    except BaseException as exc:
        if not fut.done():
            fut.set_exception(exc)
            fut.exception()  # mark retrieved
        raise
    finally:
        if _inflight.get(key) is fut:
            del _inflight[key]


async def _cached(rtype: str, subject: str, fetch, refresh: bool) -> dict:
    key = f"{rtype}:{subject}"
    row = get_store().receipt_get(key)
    now = clock()
    note = None
    if row is not None:
        age = now - row["checked_at"]
        if refresh and age < REFRESH_MIN_AGE:
            wait = max(1, round((REFRESH_MIN_AGE - age) / 60))
            note = f"Checked less than 15 minutes ago; refresh available in about {wait} min."
            return _shape(rtype, row["data"], row["checked_at"], cached=True, note=note)
        if not refresh and now < row["soft_until"]:
            return _shape(rtype, row["data"], row["checked_at"], cached=True)
        if not refresh and now < row["hard_until"]:
            if key not in _inflight:
                task = asyncio.create_task(_compute_and_store(key, rtype, fetch))
                _background.add(task)
                task.add_done_callback(_background.discard)
            return _shape(rtype, row["data"], row["checked_at"], cached=True, refreshing=True)
    result = await _compute_and_store(key, rtype, fetch)
    return _shape(rtype, result["data"], result["checked_at"], cached=False)


def _shape(rtype: str, data: dict, checked_at: float | None, *, cached: bool, refreshing: bool = False,
           note: str | None = None) -> dict:
    out = {
        "type": rtype,
        "title": TITLES[rtype],
        "status": data.get("status", "error"),
        "flag": data.get("flag", "neutral"),
        "text": data.get("text", ""),
        "evidence_url": data.get("evidence_url"),
        "checked_at": _iso(checked_at) if checked_at else None,
        "cached": cached,
    }
    if data.get("details"):
        out["details"] = data["details"]
    if refreshing:
        out["refreshing"] = True
    if note:
        out["note"] = note
    return out


async def get_receipts(target: str, *, title: str | None = None, refresh: bool = False) -> dict:
    """Receipts for a URL or domain. Raises DomainError for unusable input."""
    host = normalize_host(target)
    reg = registrable(host)
    now = clock()

    async def fc():
        query = title or reg
        sub = hashlib.sha256(query.lower().encode("utf-8")).hexdigest()[:16]
        return await _cached("fact_checks", sub, lambda: factcheck.check(query), refresh)

    jobs = {
        "domain_age": _cached("domain_age", reg, lambda: rdap.check(reg), refresh),
        "first_seen_archive": _cached("first_seen_archive", reg, lambda: wayback.check(reg), refresh),
        "first_certificate": _cached("first_certificate", reg, lambda: crtsh.check(reg), refresh),
    }
    if factcheck.configured():
        jobs["fact_checks"] = fc()
    results = dict(zip(jobs, await asyncio.gather(*jobs.values())))
    results["lookalike"] = _shape("lookalike", lookalike.check(host), now, cached=False)
    if "fact_checks" not in results:
        results["fact_checks"] = _shape("fact_checks", factcheck.not_checked(), None, cached=False)
    results["syndication"] = _shape("syndication", syndication.not_checked(), None, cached=False)
    return {"domain": host, "registrable_domain": reg, "receipts": [results[t] for t in ORDER]}


async def domain_receipts(target: str) -> dict:
    """Light receipts for trace mode: lookalike (offline) + domain age (RDAP, cached like /source)."""
    host = normalize_host(target)
    reg = registrable(host)
    age = await _cached("domain_age", reg, lambda: rdap.check(reg), False)
    return {"domain": host, "registrable_domain": reg,
            "receipts": [_shape("lookalike", lookalike.check(host), clock(), cached=False), age]}


def reset() -> None:
    """For tests."""
    _inflight.clear()
