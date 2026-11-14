"""first_certificate receipt: earliest TLS certificate in Certificate Transparency logs (crt.sh),
with automatic fallback to CertSpotter (`api.certspotter.com`) when crt.sh is overloaded."""
from __future__ import annotations

from datetime import datetime, timezone

from app.receipts import http
from app.receipts.timefmt import age_phrase, parse_date

YOUNG_DAYS = 90
MAX_BYTES = 3_000_000


async def _certspotter_fallback(domain: str) -> list[datetime]:
    try:
        rows = await http.get_json(
            "https://api.certspotter.com/v1/issuances",
            params={"domain": domain},
            max_bytes=500_000,
            accept_404=True,
            timeout_s=4.0,
        )
    except http.SourceError:
        return []
    dates: list[datetime] = []
    for r in rows if isinstance(rows, list) else []:
        d = parse_date(r.get("not_before")) if isinstance(r, dict) else None
        if d:
            dates.append(d)
    return dates


async def check(domain: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    evidence = f"https://crt.sh/?q={domain}"
    via_fallback = False
    try:
        rows = await http.get_json("https://crt.sh/", params={"q": domain, "output": "json"}, max_bytes=MAX_BYTES,
                                   timeout_s=4.5)
        dates = [d for r in (rows if isinstance(rows, list) else [])
                 if isinstance(r, dict) and (d := parse_date(r.get("not_before")))]
    except http.TooLarge:
        return {"status": "found", "flag": "neutral", "evidence_url": evidence,
                "text": "Thousands of TLS certificates on record: a long certificate history."}
    except http.SourceError:
        dates = await _certspotter_fallback(domain)
        via_fallback = True
        if not dates:
            return {"status": "error", "flag": "neutral", "evidence_url": evidence,
                    "text": "The certificate log search (crt.sh) did not answer; it is often overloaded. Try Refresh later."}

    if not dates:
        return {"status": "not_found", "flag": "neutral", "evidence_url": evidence,
                "text": "No TLS certificates found in public logs for this exact domain."}
    first = min(dates)
    days = (now - first).days
    when = f"{first:%Y-%m-%d} ({age_phrase(days)})"
    details = {"first_certificate": f"{first:%Y-%m-%d}", "days": days, "certificates": len(dates)}
    if via_fallback:
        details["source"] = "certspotter"
        return {"status": "found", "flag": "warning" if days < YOUNG_DAYS else "neutral",
                "evidence_url": evidence, "details": details,
                "text": f"Active TLS certificates in public logs since at least {when} ({len(dates)} recent issuance{'s' if len(dates) != 1 else ''} via CertSpotter; full crt.sh archive was busy)."}
    if days < YOUNG_DAYS:
        return {"status": "found", "flag": "warning", "evidence_url": evidence, "details": details,
                "text": f"First TLS certificate issued only recently: {when}."}
    return {"status": "found", "flag": "neutral", "evidence_url": evidence, "details": details,
            "text": f"First TLS certificate issued on {when}."}
