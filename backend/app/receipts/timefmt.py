"""Small date helpers for receipt texts."""
from __future__ import annotations

from datetime import datetime, timezone


def parse_date(value) -> datetime | None:
    """ISO 8601 / 'YYYY-MM-DDTHH:MM:SS' / Wayback 'YYYYMMDDhhmmss' -> aware UTC datetime."""
    if not isinstance(value, str) or not value:
        return None
    v = value.strip()
    if v.isdigit() and len(v) >= 8:
        try:
            return datetime.strptime(v[:14].ljust(14, "0"), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def age_phrase(days: int) -> str:
    if days < 0:
        return "in the future"
    if days < 1:
        return "today"
    if days < 60:
        return f"{days} day{'s' if days != 1 else ''} ago"
    if days < 730:
        months = round(days / 30.4)
        return f"about {months} months ago"
    years = days // 365
    return f"about {years} years ago"
