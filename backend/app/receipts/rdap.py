"""domain_age receipt: registration date via RDAP (https://rdap.org bootstrap), with port-43 WHOIS
fallback for country TLDs (such as .ee, .lv, .lt, .de, .fi, .se) that do not run public RDAP."""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone

from app.receipts import http
from app.receipts.timefmt import age_phrase, parse_date

YOUNG_DAYS = 180
WHOIS_TIMEOUT_S = 3.5
_WHOIS_SERVERS = {
    "ee": "whois.tld.ee",
    "lv": "whois.nic.lv",
    "lt": "whois.domreg.lt",
    "fi": "whois.fi",
    "se": "whois.iis.se",
    "dk": "whois.punktum.dk",
    "de": "whois.denic.de",
    "it": "whois.nic.it",
    "es": "whois.nic.es",
    "at": "whois.nic.at",
    "pl": "whois.dns.pl",
    "ru": "whois.tcinet.ru",
    "ua": "whois.ua",
}
_WHOIS_CREATED = re.compile(
    r"^\s*(?:registered|created|creation date|domain registration date|registered on|registration time)"
    r"[.\s]*:\s*(.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_DMY_DOT = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})")


def parse_whois_date(raw_text: str) -> datetime | None:
    """Extract a registration datetime from raw port-43 WHOIS text."""
    for m in _WHOIS_CREATED.finditer(raw_text or ""):
        val = m.group(1).strip()
        # e.g. '2010-07-04 04:34:46 +03:00' -> ISO '2010-07-04T04:34:46+03:00'
        iso_cand = re.sub(r"^(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})\s*([+-]\d{2}:?\d{2}|Z)?.*$",
                          lambda x: f"{x.group(1)}T{x.group(2)}{x.group(3) or ''}", val)
        if len(iso_cand) >= 10 and iso_cand[4:5] == "-" and iso_cand[7:8] == "-":
            dt = parse_date(iso_cand[:25] if "T" in iso_cand else iso_cand[:10])
            if dt:
                return dt
        dmy = _DMY_DOT.match(val)
        if dmy:
            try:
                return datetime(int(dmy.group(3)), int(dmy.group(2)), int(dmy.group(1)), tzinfo=timezone.utc)
            except ValueError:
                pass
        dt = parse_date(val)
        if dt:
            return dt
    return None


async def _query_port43(server: str, query: str) -> str:
    reader, writer = await asyncio.open_connection(server, 43)
    try:
        writer.write(f"{query}\r\n".encode("ascii", errors="ignore"))
        await writer.drain()
        chunks = bytearray()
        while len(chunks) < 16_384:
            piece = await reader.read(4096)
            if not piece:
                break
            chunks += piece
        return bytes(chunks).decode("utf-8", errors="replace")
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass


async def _default_whois_lookup(domain: str) -> datetime | None:
    tld = domain.rsplit(".", 1)[-1].lower() if "." in domain else ""
    server = _WHOIS_SERVERS.get(tld)
    try:
        async with asyncio.timeout(WHOIS_TIMEOUT_S):
            if not server and tld:
                iana = await _query_port43("whois.iana.org", tld)
                ref = re.search(r"^\s*whois:\s*(\S+)", iana, re.IGNORECASE | re.MULTILINE)
                server = ref.group(1).strip() if ref else None
            if not server:
                return None
            body = await _query_port43(server, domain)
            return parse_whois_date(body)
    except Exception:  # noqa: BLE001 - port 43 blocked or registry down: keep not_found
        return None


# Replaced in tests (`conftest.py`) so tests never open live sockets.
whois_lookup = _default_whois_lookup


def _format_age(registered: datetime, now: datetime, evidence: str, via_whois: bool = False) -> dict:
    days = (now - registered).days
    when = f"{registered:%Y-%m-%d} ({age_phrase(days)})"
    details = {"registered": f"{registered:%Y-%m-%d}", "days": days}
    if via_whois:
        details["source"] = "whois"
    if days < YOUNG_DAYS:
        return {"status": "found", "flag": "warning", "evidence_url": evidence, "details": details,
                "text": f"Domain registered only recently: {when}. New domains are common for clone and "
                        "throwaway sites, but also for genuinely new projects."}
    return {"status": "found", "flag": "neutral", "evidence_url": evidence, "details": details,
            "text": f"Domain registered on {when}."}


async def check(domain: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    evidence = f"https://lookup.icann.org/en/lookup?name={domain}"
    try:
        data = await http.get_json(f"https://rdap.org/domain/{domain}", accept_404=True, max_bytes=500_000, timeout_s=5.0)
    except http.SourceError:
        return {"status": "error", "flag": "neutral", "evidence_url": evidence,
                "text": "The domain registration lookup (RDAP) did not answer in time. Try Refresh later."}
    if data is None:
        reg_whois = await whois_lookup(domain)
        if reg_whois is not None:
            return _format_age(reg_whois, now, evidence, via_whois=True)
        return {"status": "not_found", "flag": "neutral", "evidence_url": evidence,
                "text": "No public registration record found (RDAP). Some country domains do not publish one."}
    registered = None
    for ev in (data.get("events") or []) if isinstance(data, dict) else []:
        if isinstance(ev, dict) and ev.get("eventAction") == "registration":
            registered = parse_date(ev.get("eventDate"))
            break
    if registered is None:
        reg_whois = await whois_lookup(domain)
        if reg_whois is not None:
            return _format_age(reg_whois, now, evidence, via_whois=True)
        return {"status": "not_found", "flag": "neutral", "evidence_url": evidence,
                "text": "The registry answered but does not publish the registration date."}
    return _format_age(registered, now, evidence)
