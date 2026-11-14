"""first_certificate receipt: earliest TLS certificate in Certificate Transparency logs (crt.sh).

crt.sh is slow and often overloaded: timeouts are expected and reported honestly. Big, old
domains have thousands of certificates; above the size cap we say so instead of a date.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.receipts import http
from app.receipts.timefmt import age_phrase, parse_date

YOUNG_DAYS = 90
MAX_BYTES = 3_000_000


async def check(domain: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    evidence = f"https://crt.sh/?q={domain}"
    try:
        rows = await http.get_json("https://crt.sh/", params={"q": domain, "output": "json"}, max_bytes=MAX_BYTES)
    except http.TooLarge:
        return {"status": "found", "flag": "neutral", "evidence_url": evidence,
                "text": "Thousands of TLS certificates on record: a long certificate history."}
    except http.SourceError:
        return {"status": "error", "flag": "neutral", "evidence_url": evidence,
                "text": "The certificate log search (crt.sh) did not answer; it is often overloaded. Try Refresh later."}
    dates = []
    for r in rows if isinstance(rows, list) else []:
        d = parse_date(r.get("not_before")) if isinstance(r, dict) else None
        if d:
            dates.append(d)
    if not dates:
        return {"status": "not_found", "flag": "neutral", "evidence_url": evidence,
                "text": "No TLS certificates found in public logs for this exact domain."}
    first = min(dates)
    days = (now - first).days
    when = f"{first:%Y-%m-%d} ({age_phrase(days)})"
    details = {"first_certificate": f"{first:%Y-%m-%d}", "days": days, "certificates": len(dates)}
    if days < YOUNG_DAYS:
        return {"status": "found", "flag": "warning", "evidence_url": evidence, "details": details,
                "text": f"First TLS certificate issued only recently: {when}."}
    return {"status": "found", "flag": "neutral", "evidence_url": evidence, "details": details,
            "text": f"First TLS certificate issued on {when}."}
