"""Live IP threat-intelligence enrichment: Shodan InternetDB + RIPE NCC ASN + optional VirusTotal v3.

All outbound calls run through `app.swarm.collectors.http.Session` (test-isolated, bounded timeouts)
and are cached for 24h in SQLite `receipt_cache`.
"""
from __future__ import annotations

import asyncio
import time

from app import config
from app.store import get_store
from app.swarm.collectors.http import CollectorError, HTTPStatus, Session
from app.traffic.parser import is_public_ip

SHODAN_INTERNETDB = "https://internetdb.shodan.io/{ip}"
RIPESTAT_PREFIX = "https://stat.ripe.net/data/prefix-overview/data.json"
VIRUSTOTAL_IP = "https://www.virustotal.com/api/v3/ip_addresses/{ip}"

INTEL_TTL_S = 24 * 3600
MAX_INTEL_IPS = 12

# Official search crawler ASNs & rDNS suffixes for spoofed-bot verification
CRAWLER_RULES = {
    "googlebot": {"asns": {15169, 396982}, "rdns": (".googlebot.com", ".google.com", ".googleusercontent.com"), "name": "Googlebot"},
    "bingbot": {"asns": {8075, 8068}, "rdns": (".search.msn.com",), "name": "Bingbot"},
    "applebot": {"asns": {714}, "rdns": (".applebot.apple.com",), "name": "Applebot"},
    "duckduckbot": {"asns": {8075, 16509, 14618}, "rdns": (".duckduckgo.com",), "name": "DuckDuckBot"},
}


def claimed_crawler(ua: str) -> str | None:
    low = (ua or "").lower()
    for token in CRAWLER_RULES:
        if token in low:
            return token
    return None


def verify_crawler_claim(claim_key: str, intel: dict | None) -> str:
    """Return 'verified' | 'spoofed' | 'unverified' given live ASN/rDNS intel."""
    if not intel or not intel.get("checked"):
        return "unverified"
    rule = CRAWLER_RULES.get(claim_key)
    if not rule:
        return "unverified"
    asn = intel.get("asn")
    hosts = [h.lower().rstrip(".") for h in (intel.get("hostnames") or [])]
    if asn in rule["asns"] or any(h.endswith(rule["rdns"]) for h in hosts):
        return "verified"
    if asn is not None:
        return "spoofed"
    return "unverified"
async def _lookup_one(s: Session, ip: str) -> dict:
    store = get_store()
    now = time.time()
    cache_key = f"ip_intel:{ip}"
    cached = store.receipt_get(cache_key)
    if cached and cached["soft_until"] > now:
        return cached["data"]

    out: dict = {
        "ip": ip,
        "checked": False,
        "asn": None,
        "holder": None,
        "prefix": None,
        "ports": [],
        "tags": [],
        "hostnames": [],
        "vulns_count": 0,
        "vulns_sample": [],
        "vt_malicious": None,
        "vt_suspicious": None,
        "links": {
            "shodan": f"https://www.shodan.io/host/{ip}",
            "virustotal": f"https://www.virustotal.com/gui/ip-address/{ip}",
            "abuseipdb": f"https://www.abuseipdb.com/check/{ip}",
        },
    }

    async def shodan_job():
        try:
            d = await s.request_json("GET", SHODAN_INTERNETDB.format(ip=ip), timeout_s=3.5)
            if isinstance(d, dict):
                out["checked"] = True
                out["ports"] = [int(p) for p in (d.get("ports") or [])[:16] if isinstance(p, int)]
                out["tags"] = [str(t) for t in (d.get("tags") or [])[:10]]
                out["hostnames"] = [str(h) for h in (d.get("hostnames") or [])[:5]]
                vulns = [str(v) for v in (d.get("vulns") or [])]
                out["vulns_count"] = len(vulns)
                out["vulns_sample"] = vulns[:5]
        except HTTPStatus as exc:
            if exc.status == 404:
                out["checked"] = True  # Shodan 404 = clean (no open ports indexed)
        except CollectorError:
            pass

    async def ripe_job():
        try:
            d = await s.request_json("GET", RIPESTAT_PREFIX, params={"resource": ip}, timeout_s=3.5)
            data = (d or {}).get("data") if isinstance(d, dict) else None
            if isinstance(data, dict):
                out["checked"] = True
                out["prefix"] = data.get("resource")
                asns = data.get("asns") or []
                if asns and isinstance(asns[0], dict):
                    out["asn"] = asns[0].get("asn")
                    out["holder"] = asns[0].get("holder")
        except CollectorError:
            pass

    async def vt_job():
        vt_key = config.virustotal_api_key()
        if not vt_key:
            return
        try:
            d = await s.request_json("GET", VIRUSTOTAL_IP.format(ip=ip), headers={"x-apikey": vt_key}, timeout_s=4.0)
            stats = (((d or {}).get("data") or {}).get("attributes") or {}).get("last_analysis_stats") or {}
            if isinstance(stats, dict):
                out["vt_malicious"] = int(stats.get("malicious") or 0)
                out["vt_suspicious"] = int(stats.get("suspicious") or 0)
        except CollectorError:
            pass

    await asyncio.gather(shodan_job(), ripe_job(), vt_job())
    if out["checked"]:
        store.receipt_put(cache_key, out, now, now + INTEL_TTL_S, now + INTEL_TTL_S * 2)
    return out


async def enrich_ips(ips: list[str], *, enabled: bool = True) -> dict[str, dict]:
    """Enrich up to MAX_INTEL_IPS public IPs via Shodan InternetDB, RIPE NCC, and optional VirusTotal."""
    public = [ip for ip in dict.fromkeys(ips) if is_public_ip(ip)][:MAX_INTEL_IPS]
    base = {
        ip: {
            "ip": ip, "checked": False, "asn": None, "holder": None, "prefix": None,
            "ports": [], "tags": [], "hostnames": [], "vulns_count": 0, "vulns_sample": [],
            "vt_malicious": None, "vt_suspicious": None,
            "links": {
                "shodan": f"https://www.shodan.io/host/{ip}",
                "virustotal": f"https://www.virustotal.com/gui/ip-address/{ip}",
                "abuseipdb": f"https://www.abuseipdb.com/check/{ip}",
            },
        }
        for ip in dict.fromkeys(ips)
    }
    if not enabled or not public:
        return base
    try:
        async with Session(max_requests=MAX_INTEL_IPS * 3 + 2, deadline_s=7.0) as s:
            got = await asyncio.gather(*(_lookup_one(s, ip) for ip in public))
        for item in got:
            base[item["ip"]] = item
    except Exception:  # noqa: BLE001 - threat-intel lookup must fail soft and never break log analysis
        pass
    return base