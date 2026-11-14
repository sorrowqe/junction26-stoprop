"""fact_checks receipt: Google Fact Check Tools claim search (only if FACTCHECK_API_KEY is set).

This is a keyword search (page title, or the domain name), so results are *related*
fact-checks, not necessarily about this exact page. The key travels as a query parameter
and is never logged or returned.
"""
from __future__ import annotations

from app import config
from app.receipts import http

ENDPOINT = "https://factchecktools.googleapis.com/v1alpha1/claims:search"


def configured() -> bool:
    return bool(config.env("FACTCHECK_API_KEY"))


def not_checked() -> dict:
    return {"status": "not_checked", "flag": "neutral", "evidence_url": None,
            "text": "Fact-check search is not configured on this server, so it was not checked."}


async def check(query: str) -> dict:
    if not configured():
        return not_checked()
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
