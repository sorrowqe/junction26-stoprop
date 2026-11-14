"""Oxylabs AI Studio client (https://api-aistudio.oxylabs.io, `x-api-key`) on `httpx2`.

Provides Tier-2 anti-bot/JS Markdown scraping (`/scrape` -> `/scrape/run/data`) with:
- 6-hour SQLite caching (`receipt_cache`) + single-flight deduplication,
- daily call cap (`OXYLABS_DAILY_CAP`, default 150) logged to `usage_log` (0 LLM tokens/credits).
SSRF validation (`check_url` + `resolve_public`) always runs in `extract.py` before calling this module.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
import time

import httpx2

from app import config
from app.store import get_store, monotonic_ms, today

BASE_URL = "https://api-aistudio.oxylabs.io"
SCRAPE_TIMEOUT_S = 30.0
POLL_INTERVAL_S = 1.5
CACHE_TTL_S = 6 * 3600
_inflight: dict[str, asyncio.Future] = {}


class OxylabsError(Exception):
    def __init__(self, code: str, message: str, status: int | None = None):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _default_client() -> httpx2.AsyncClient:
    return httpx2.AsyncClient(trust_env=False, follow_redirects=False, timeout=httpx2.Timeout(15.0))


client_factory = _default_client


def reset() -> None:
    _inflight.clear()


def configured() -> bool:
    return bool(config.oxylabs_api_key())


def calls_today() -> int:
    rows = get_store()._exec(
        "SELECT COUNT(*) FROM usage_log WHERE day = ? AND kind IN ('oxylabs_scrape', 'oxylabs_search')",
        (today(),),
    )
    return int(rows[0][0]) if rows else 0


def status() -> dict:
    cap = config.oxylabs_daily_cap()
    used = calls_today() if configured() else 0
    return {"configured": configured(), "calls_today": used, "daily_cap": cap,
            "available": configured() and (cap == 0 or used < cap)}


def _headers() -> dict[str, str]:
    return {"x-api-key": config.oxylabs_api_key(), "Content-Type": "application/json",
            "Accept": "application/json", "User-Agent": f"StopropBot/{config.VERSION}"}


_MD_IMG = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MD_LNK = re.compile(r"\[([^\]]+)\]\((?:https?://|/|#)[^)]*\)")
_MD_REF = re.compile(r"\[([^\]]+)\]\[[^\]]*\]")
_MD_HDR = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*$", re.MULTILINE)
_MD_RUL = re.compile(r"^\s*(?:[-*_]\s*){3,}$", re.MULTILINE)
_MD_TAG = re.compile(r"</?[a-zA-Z][^>]{0,200}>")
_JUNK = re.compile(
    r"^(?:skip to (?:main )?content|accept (?:all )?cookies|manage cookies|cookie policy|"
    r"privacy policy|sign in|log in|subscribe|advertisement|share this article)\s*$", re.IGNORECASE,
)


def markdown_to_text(md: str) -> tuple[str, str | None]:
    """Strip Markdown syntax into clean article text + H1 title while preserving exact wording."""
    if not md or not isinstance(md, str):
        return "", None
    no_imgs = _MD_IMG.sub(" ", md)
    title = None
    for m in _MD_HDR.finditer(no_imgs):
        if len(m.group(1)) == 1:
            cand = re.sub(r"\[!*", "", _MD_LNK.sub(r"\1", m.group(2))).replace("]", " ").strip(" *_`")
            cand = re.sub(r"\s{2,}", " ", cand)
            if len(cand) >= 4:
                title = cand[:300]
                break
    t = no_imgs.replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"```[\s\S]*?```", "\n", t)
    t = _MD_LNK.sub(r"\1", t)
    t = _MD_REF.sub(r"\1", t)
    t = _MD_RUL.sub("\n", t)
    t = _MD_TAG.sub(" ", t)
    t = _MD_HDR.sub(r"\2", t)
    t = re.sub(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1", r"\2", t)
    t = re.sub(r"(?<!\w)(\*|_)(?=\S)([^*\n]+?)(?<=\S)\1(?!\w)", r"\2", t)
    lines: list[str] = []
    for raw in t.split("\n"):
        line = re.sub(r"[ \t]{2,}", " ", re.sub(r"^\s*(?:>\s*|[-*+]\s+)", "", raw).strip())
        if not line:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        if _JUNK.match(line) or (line.count("|") >= 3 and len(line.split()) <= 18):
            continue
        lines.append(line)
    return "\n".join(lines).strip(), title


async def _run_scrape(url: str, timeout_s: float) -> dict:
    t0 = monotonic_ms()
    cap = config.oxylabs_daily_cap()
    if cap > 0 and calls_today() >= cap:
        raise OxylabsError("cap", "Daily browser-scraping budget reached on this server.")
    payload = {"url": url, "output_format": "markdown", "render_javascript": "auto", "optimize_content": True}
    try:
        async with asyncio.timeout(timeout_s):
            async with client_factory() as client:
                r = await client.post(f"{BASE_URL}/scrape", json=payload, headers=_headers())
                if r.status_code in (401, 403):
                    raise OxylabsError("auth", "Oxylabs AI Studio rejected the API key.", r.status_code)
                if r.status_code == 429:
                    raise OxylabsError("rate_limit", "Oxylabs AI Studio rate limit reached.", 429)
                if r.status_code >= 400:
                    raise OxylabsError("http", f"Oxylabs scrape failed (HTTP {r.status_code}).", r.status_code)
                body = r.json()
                run_id = body.get("run_id") if isinstance(body, dict) else None
                if not run_id:
                    raise OxylabsError("bad_response", "Oxylabs did not return a job id.")
                while True:
                    await asyncio.sleep(POLL_INTERVAL_S)
                    p = await client.get(f"{BASE_URL}/scrape/run/data", params={"run_id": run_id}, headers=_headers())
                    if p.status_code == 202 or p.status_code >= 500:
                        continue
                    if p.status_code >= 400:
                        raise OxylabsError("http", f"Oxylabs poll failed (HTTP {p.status_code}).", p.status_code)
                    pb = p.json()
                    if not isinstance(pb, dict):
                        continue
                    if pb.get("status") == "completed":
                        raw = pb.get("data")
                        if isinstance(raw, dict):
                            raw = raw.get("markdown") or raw.get("content") or ""
                        text, title = markdown_to_text(raw if isinstance(raw, str) else "")
                        get_store().log_usage("oxylabs_scrape", "oxylabs-ai-studio", 0, 0, monotonic_ms() - t0, ok=True)
                        return {"text": text, "title": title}
                    if pb.get("status") == "failed":
                        raise OxylabsError("job_failed", f"Oxylabs scrape failed ({pb.get('error_code') or 'failed'}).")
    except OxylabsError as exc:
        get_store().log_usage("oxylabs_scrape", "oxylabs-ai-studio", 0, 0, monotonic_ms() - t0, ok=False, error=exc.code)
        raise
    except TimeoutError:
        get_store().log_usage("oxylabs_scrape", "oxylabs-ai-studio", 0, 0, monotonic_ms() - t0, ok=False, error="timeout")
        raise OxylabsError("timeout", "Browser scraping timed out.") from None
    except (httpx2.HTTPError, ValueError) as exc:
        get_store().log_usage("oxylabs_scrape", "oxylabs-ai-studio", 0, 0, monotonic_ms() - t0, ok=False, error=type(exc).__name__)
        raise OxylabsError("network", "Could not reach Oxylabs AI Studio.") from None


async def scrape_markdown(url: str, *, timeout_s: float = SCRAPE_TIMEOUT_S) -> tuple[str, str | None]:
    if not configured():
        raise OxylabsError("unconfigured", "Oxylabs AI Studio is not configured.")
    ck = "oxy_scrape:" + hashlib.sha256(url.strip().encode("utf-8")).hexdigest()[:32]
    now = time.time()
    cached = get_store().receipt_get(ck)
    if cached and now < cached["hard_until"]:
        return cached["data"].get("text", ""), cached["data"].get("title")
    if ck in _inflight:
        res = await asyncio.shield(_inflight[ck])
        return res["text"], res["title"]
    fut = asyncio.get_running_loop().create_future()
    _inflight[ck] = fut
    try:
        res = await _run_scrape(url, timeout_s)
        if res["text"]:
            now = time.time()
            get_store().receipt_put(ck, res, now, now + CACHE_TTL_S, now + CACHE_TTL_S)
        fut.set_result(res)
        return res["text"], res["title"]
    except BaseException as exc:
        if not fut.done():
            fut.set_exception(exc)
            fut.exception()
        raise
    finally:
        if _inflight.get(ck) is fut:
            del _inflight[ck]
