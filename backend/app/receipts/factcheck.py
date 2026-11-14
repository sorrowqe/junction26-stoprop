"""fact_checks receipt:
1. Google Fact Check Tools API when `FACTCHECK_API_KEY` is configured.
2. Otherwise, when Brave Search (`BRAVE_API_KEY`) is configured, searches trusted fact-checkers and
   disinformation watchdogs (`euvsdisinfo.eu`, `propastop.org`, `snopes.com`, `politifact.com`,
   `fullfact.org`, `factcheck.org`, `leadstories.com`, `correctiv.org`).
"""
from __future__ import annotations

from app import config
from app.pipeline import websearch
from app.receipts import http
from app.swarm.collectors.http import Session

ENDPOINT = "https://factchecktools.googleapis.com/v1alpha1/claims:search"
WATCHDOG_SITES = {
    "euvsdisinfo.eu": "EUvsDisinfo",
    "propastop.org": "Propastop (Estonia)",
    "snopes.com": "Snopes",
    "politifact.com": "PolitiFact",
    "fullfact.org": "Full Fact",
    "factcheck.org": "FactCheck.org",
    "leadstories.com": "Lead Stories",
    "correctiv.org": "Correctiv",
}
_SITE_FILTER = "(" + " OR ".join(f"site:{d}" for d in WATCHDOG_SITES) + ")"


def _google_configured() -> bool:
    return bool(config.env("FACTCHECK_API_KEY"))


def _brave_configured() -> bool:
    return config.active_search_provider() == "brave" and bool(config.brave_api_key())


def configured() -> bool:
    return _google_configured() or _brave_configured()


def not_checked() -> dict:
    return {"status": "not_checked", "flag": "neutral", "evidence_url": None,
            "text": "Fact-check search is not configured on this server, so it was not checked."}


async def _check_brave_watchdogs(query: str) -> dict:
    clean_q = query.strip().replace('"', "")[:140]
    if not clean_q:
        return not_checked()
    # Exact-quote domain queries (e.g. "rt.com"), keyword match for longer article titles/claims
    term = f'"{clean_q}"' if ("." in clean_q and " " not in clean_q) else clean_q
    try:
        async with Session(max_requests=2, deadline_s=6.0) as s:
            hits = await websearch.brave_search(s, f"{term} {_SITE_FILTER}", kind="web", limit=6)
    except websearch.SearchProviderError:
        return {"status": "error", "flag": "neutral", "evidence_url": None,
                "text": "The fact-check & watchdog search did not answer in time. Try Refresh later."}

    items = []
    for h in hits:
        pub = WATCHDOG_SITES.get(h["domain"])
        if not pub:
            continue
        items.append({
            "claim": (h["title"] or clean_q)[:200],
            "publisher": pub,
            "rating": "",
            "url": h["url"],
            "snippet": (h["snippet"] or "")[:200],
        })
        if len(items) == 5:
            break

    if not items:
        return {"status": "not_found", "flag": "neutral", "evidence_url": None,
                "text": f"No reports mentioning “{clean_q[:80]}” were found across 8 fact-checkers & watchdogs (EUvsDisinfo, Propastop, Snopes, PolitiFact, Full Fact…)."}
    top = items[0]
    pubs = ", ".join(dict.fromkeys(it["publisher"] for it in items))
    return {
        "status": "found",
        "flag": "neutral",
        "evidence_url": top["url"],
        "details": {"items": items, "source": "watchdogs"},
        "text": f"{len(items)} mention{'s' if len(items) != 1 else ''} on fact-check / watchdog sites ({pubs} — may cite this domain as a source or review a claim). Top: “{top['claim']}” ({top['publisher']}).",
    }


async def check(query: str) -> dict:
    if not configured():
        return not_checked()
    if not _google_configured():
        return await _check_brave_watchdogs(query)
    params = {"query": query[:200], "pageSize": "5", "key": config.env("FACTCHECK_API_KEY")}
    try:
        data = await http.get_json(ENDPOINT, params=params, max_bytes=500_000)
    except http.SourceError:
        return {"status": "error", "flag": "neutral", "evidence_url": None,
                "text": "The fact-check search did not answer. Try Refresh later."}
    claims = (data or {}).get("claims") or [] if isinstance(data, dict) else []
    items = []
    for c in claims[:5]:
        review = (c.get("claimReview") or [{}])[0] if isinstance(c, dict) else {}
        items.append({
            "claim": (c.get("text") or "")[:200],
            "publisher": ((review.get("publisher") or {}).get("name") or (review.get("publisher") or {}).get("site") or "a fact-checker"),
            "rating": review.get("textualRating") or "",
            "url": review.get("url"),
        })
    if not items:
        return {"status": "not_found", "flag": "neutral", "evidence_url": None,
                "text": f"No related fact-checks found for “{query[:80]}”."}
    top = items[0]
    rating = f" rated “{top['rating']}”" if top["rating"] else ""
    return {"status": "found", "flag": "neutral", "evidence_url": top["url"], "details": {"items": items},
            "text": (f"{len(items)} related fact-check{'s' if len(items) != 1 else ''} found by keyword search "
                     f"(may not be about this exact page). Top: “{top['claim']}”{rating} by {top['publisher']}.")}
