"""first_seen_archive receipt: first capture in the Internet Archive's Wayback Machine (CDX API)."""
from __future__ import annotations

from datetime import datetime, timezone

from app.receipts import http
from app.receipts.timefmt import age_phrase, parse_date

YOUNG_DAYS = 90


async def check(domain: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    params = {"url": domain, "output": "json", "limit": "1", "fl": "timestamp,original", "filter": "statuscode:200",
              "from": "1996"}
    try:
        rows = await http.get_json("https://web.archive.org/cdx/search/cdx", params=params, max_bytes=200_000,
                                   timeout_s=5.0)
    except http.SourceError:
        return {"status": "error", "flag": "neutral", "evidence_url": f"https://web.archive.org/web/*/{domain}",
                "text": "The Internet Archive did not answer in time. Try Refresh later."}
    if not isinstance(rows, list) or len(rows) < 2 or not isinstance(rows[1], list) or len(rows[1]) < 2:
        return {"status": "not_found", "flag": "neutral", "evidence_url": f"https://web.archive.org/web/*/{domain}",
                "text": "The Internet Archive has no saved copy of this site. Brand-new sites often have none."}
    ts, original = rows[1][0], rows[1][1]
    first = parse_date(ts)
    if first is None:
        return {"status": "error", "flag": "neutral", "evidence_url": None,
                "text": "The Internet Archive returned an unreadable date."}
    days = (now - first).days
    evidence = f"https://web.archive.org/web/{ts}/{original}"
    when = f"{first:%Y-%m-%d} ({age_phrase(days)})"
    details = {"first_capture": f"{first:%Y-%m-%d}", "days": days}
    if days < YOUNG_DAYS:
        return {"status": "found", "flag": "warning", "evidence_url": evidence, "details": details,
                "text": f"First saved by the Internet Archive only recently: {when}."}
    return {"status": "found", "flag": "neutral", "evidence_url": evidence, "details": details,
            "text": f"First saved by the Internet Archive on {when}."}
