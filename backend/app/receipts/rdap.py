"""domain_age receipt: registration date via RDAP (https://rdap.org bootstrap)."""
from __future__ import annotations

from datetime import datetime, timezone

from app.receipts import http
from app.receipts.timefmt import age_phrase, parse_date

YOUNG_DAYS = 180


async def check(domain: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    evidence = f"https://lookup.icann.org/en/lookup?name={domain}"
    try:
        data = await http.get_json(f"https://rdap.org/domain/{domain}", accept_404=True, max_bytes=500_000)
    except http.SourceError:
        return {"status": "error", "flag": "neutral", "evidence_url": evidence,
                "text": "The domain registration lookup (RDAP) did not answer in time. Try Refresh later."}
    if data is None:
        return {"status": "not_found", "flag": "neutral", "evidence_url": evidence,
                "text": "No public registration record found (RDAP). Some country domains do not publish one."}
    registered = None
    for ev in (data.get("events") or []) if isinstance(data, dict) else []:
        if isinstance(ev, dict) and ev.get("eventAction") == "registration":
            registered = parse_date(ev.get("eventDate"))
            break
    if registered is None:
        return {"status": "not_found", "flag": "neutral", "evidence_url": evidence,
                "text": "The registry answered but does not publish the registration date."}
    days = (now - registered).days
    when = f"{registered:%Y-%m-%d} ({age_phrase(days)})"
    if days < YOUNG_DAYS:
        return {"status": "found", "flag": "warning", "evidence_url": evidence, "details": {"registered": f"{registered:%Y-%m-%d}", "days": days},
                "text": f"Domain registered only recently: {when}. New domains are common for clone and "
                        "throwaway sites, but also for genuinely new projects."}
    return {"status": "found", "flag": "neutral", "evidence_url": evidence, "details": {"registered": f"{registered:%Y-%m-%d}", "days": days},
            "text": f"Domain registered on {when}."}
