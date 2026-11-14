"""Deterministic bad-bot, credential-stuffing, vuln-scanner, and scraper detector for server logs.

Separates malicious automation and bulk scrapers from legitimate search crawlers and real human visitors
(including shared office/campus NAT IPs), enriched with Shodan InternetDB, RIPE ASN, and VirusTotal.
"""
from __future__ import annotations

import re
import time
import uuid
from collections import Counter, defaultdict

from app.traffic import intel as intel_mod
from app.traffic.parser import LogEntry, parse_logs

_AUTH_PATH_RE = re.compile(
    r"(?:/wp-login\.php|/xmlrpc\.php|/wp-admin/admin-ajax\.php|/(?:admin|user|account|auth|api/v?\d*/?auth|api)/login"
    r"|/oauth/token|/signin|/session)(?:[/?#]|$)",
    re.I,
)
_EXPLOIT_PATH_RE = re.compile(
    r"(?:/\.env|/\.git/|/\.aws/|/phpmyadmin|/pma/|/adminer|/wp-config|/vendor/phpunit|/actuator/"
    r"|/cgi-bin/|/boaform/|/HNAP1|/evox/|/sdk|/shell|/eval-stdin\.php|\.\./|union\s+select|etc/passwd)",
    re.I,
)
_STATIC_EXT_RE = re.compile(r"\.(?:css|js|mjs|png|jpe?g|gif|webp|svg|ico|woff2?|ttf|map)(?:[?#]|$)", re.I)

_AI_SCRAPER_UA_RE = re.compile(
    r"(?:GPTBot|ChatGPT-User|OAI-SearchBot|ClaudeBot|anthropic-ai|Bytespider|CCBot|PerplexityBot|"
    r"Diffbot|ImagesiftBot|FacebookBot|Amazonbot|Omgilibot|Scrapy|python-requests|python-httpx|"
    r"aiohttp|Go-http-client|curl/|Wget/|Java/|libwww-perl|HeadlessChrome|PhantomJS|Playwright|Puppeteer)",
    re.I,
)
_GOOD_BOT_UA_RE = re.compile(
    r"(?:Googlebot|bingbot|Applebot|DuckDuckBot|Slurp|YandexBot|Baiduspider|facebookexternalhit|"
    r"Twitterbot|LinkedInBot|Slackbot|TelegramBot|Discordbot|UptimeRobot|Better\s*Uptime|Pingdom|Feedbin|Feedly|NetNewsWire)",
    re.I,
)
_BROWSER_UA_RE = re.compile(r"(?:Mozilla/5\.0.*(?:Chrome/|Firefox/|Safari/|Edg/|Mobile/))", re.I)
_DATACENTER_HINT_RE = re.compile(
    r"(?:DigitalOcean|Hetzner|OVH|Linode|Akamai\s+Connected|Choopa|Vultr|Contabo|M247|LeaseWeb|"
    r"Amazon|AWS|Google\s+Cloud|Microsoft\s+Corporation|Oracle|Alibaba|Tencent|Stark\s+Industries|IP\s+Volume|HostRoyale)",
    re.I,
)
def _classify_actor(ip: str, rows: list[LogEntry], intel: dict) -> dict:
    rows = sorted(rows, key=lambda r: r.ts)
    n = len(rows)
    span_s = max(1.0, rows[-1].ts - rows[0].ts)
    uas = [r.ua for r in rows if r.ua]
    top_ua = Counter(uas).most_common(1)[0][0] if uas else ""
    distinct_uas = len({u for u in uas if u})

    auth_hits = [r for r in rows if _AUTH_PATH_RE.search(r.path)]
    auth_posts = [r for r in auth_hits if r.method in ("POST", "PUT")]
    auth_fails = [r for r in auth_hits if r.status in (401, 403, 429)]
    exploit_hits = [r for r in rows if _EXPLOIT_PATH_RE.search(r.path)]
    exploit_paths = sorted({r.path.split("?", 1)[0][:60] for r in exploit_hits})
    static_hits = sum(1 for r in rows if _STATIC_EXT_RE.search(r.path))
    err_4xx = sum(1 for r in rows if 400 <= r.status < 500)
    robots_hits = sum(1 for r in rows if r.path.startswith("/robots.txt"))

    claim = intel_mod.claimed_crawler(top_ua)
    crawler_check = intel_mod.verify_crawler_claim(claim, intel) if claim else None
    tags = set(intel.get("tags") or [])
    holder = intel.get("holder") or ""
    vt_mal = intel.get("vt_malicious") or 0

    reasons: list[str] = []
    innocent_notes: list[str] = []
    category, severity, action = "human_visitor", "clean", "allow"

    if claim and (crawler_check == "spoofed" or auth_posts or len(exploit_paths) >= 2):
        category, severity, action = "spoofed_crawler", "malicious", "block"
        cname = intel_mod.CRAWLER_RULES[claim]["name"]
        if crawler_check == "spoofed":
            reasons.append(f"Claims {cname} User-Agent, but IP belongs to AS{intel.get('asn')} ({holder or 'non-official network'}) instead of {cname}'s official ASN.")
        if auth_posts or exploit_paths:
            reasons.append(f"Claims {cname} User-Agent while probing login/exploit paths ({len(auth_posts)} auth POSTs, {len(exploit_paths)} exploit paths).")
    elif len(auth_posts) >= 5 and (len(auth_fails) >= 3 or len(auth_posts) / max(1.0, span_s / 60.0) >= 4.0):
        category, severity, action = "credential_stuffing", "malicious", "block"
        reasons.append(f"{len(auth_posts)} login POST attempts ({len(auth_fails)} failed 401/403/429) within {round(span_s)}s.")
        if distinct_uas >= 3:
            reasons.append(f"Rotated {distinct_uas} distinct User-Agents from the same IP.")
    elif len(exploit_paths) >= 3 or (len(exploit_hits) >= 4 and err_4xx >= 3):
        category, severity, action = "vuln_scanner", "malicious", "block"
        reasons.append(f"Probed {len(exploit_hits)} exploit/config paths ({', '.join(exploit_paths[:4])}) with {err_4xx} HTTP 4xx errors.")
    elif _AI_SCRAPER_UA_RE.search(top_ua) or (
        n >= 14 and static_hits == 0 and not _GOOD_BOT_UA_RE.search(top_ua)
        and (not _BROWSER_UA_RE.search(top_ua) or _DATACENTER_HINT_RE.search(holder) or (tags & {"cloud", "vpn", "proxy", "tor", "scanner"}))
    ):
        category, severity, action = "ai_or_bulk_scraper", "scraper", "rate_limit"
        if _AI_SCRAPER_UA_RE.search(top_ua):
            reasons.append(f"Automated scraper / HTTP client (“{top_ua[:68]}”), {n} requests ({static_hits} static assets).")
        else:
            reasons.append(f"Swept {n} HTML/API pages in {round(span_s)}s with 0 CSS/JS/image asset loads.")
    elif _GOOD_BOT_UA_RE.search(top_ua):
        category, severity, action = "verified_crawler", "benign_bot", "allow"
        if crawler_check == "verified":
            reasons.append(f"Verified search crawler via ASN/rDNS (AS{intel.get('asn')} {holder}).")
        else:
            reasons.append(f"Polite crawler / feed reader (“{top_ua[:60]}”{', checked /robots.txt' if robots_hits else ''}).")
    else:
        if static_hits > 0:
            innocent_notes.append(f"Loaded {static_hits} static assets (CSS/JS/images) alongside pages — normal browser traffic.")
        if distinct_uas >= 2 and static_hits > 0:
            innocent_notes.append(f"{distinct_uas} normal browsers sharing one IP (typical office/campus NAT).")
        if err_4xx and len(exploit_paths) == 0:
            innocent_notes.append(f"{err_4xx} benign 404/4xx on ordinary URLs, no exploit probes.")
    if tags & {"tor", "scanner", "compromised", "honeypot", "vpn", "proxy"}:
        tstr = ", ".join(sorted(tags & {"tor", "scanner", "compromised", "honeypot", "vpn", "proxy"}))
        reasons.append(f"Shodan InternetDB flags this IP as: {tstr}.")
    if intel.get("vulns_count", 0) > 0 and severity != "clean":
        reasons.append(f"Shodan reports {intel['vulns_count']} known CVEs on open ports ({', '.join(intel['vulns_sample'][:3])}).")
    if vt_mal >= 2:
        reasons.append(f"VirusTotal: {vt_mal} security engines flag this IP as malicious.")
        if category == "human_visitor" and len(exploit_hits) + len(auth_fails) >= 2:
            category, severity, action = "vuln_scanner", "malicious", "block"

    top_paths = [p for p, _ in Counter(r.path.split("?", 1)[0] for r in rows).most_common(5)]
    return {
        "ip": ip, "category": category, "severity": severity, "action": action,
        "requests": n, "span_s": round(span_s, 1), "rpm": round(n / max(span_s / 60.0, 1.0), 1),
        "static_assets": static_hits, "auth_attempts": len(auth_posts), "exploit_probes": len(exploit_hits),
        "error_4xx": err_4xx, "top_ua": top_ua[:140], "sample_paths": top_paths,
        "reasons": reasons, "innocent_notes": innocent_notes, "intel": intel,
    }


def _build_timeline(entries: list[LogEntry], sev_by_ip: dict[str, str], n_bins: int = 24) -> dict:
    ts_all = [e.ts for e in entries]
    t0, t1 = min(ts_all), max(ts_all)
    span = max(t1 - t0, 60.0)
    step = span / n_bins
    bins = [{"t0": round(t0 + k * step, 1), "t1": round(t0 + (k + 1) * step, 1),
             "total": 0, "malicious": 0, "scraper": 0, "clean": 0} for k in range(n_bins)]
    for e in entries:
        idx = min(n_bins - 1, max(0, int((e.ts - t0) / step)))
        b = bins[idx]
        b["total"] += 1
        sev = sev_by_ip.get(e.ip, "clean")
        if sev == "malicious":
            b["malicious"] += 1
        elif sev == "scraper":
            b["scraper"] += 1
        else:
            b["clean"] += 1
    return {"start": round(t0, 1), "end": round(t1, 1), "span_s": round(span, 1), "bins": bins}


def _mitigations(block_ips: list[str], scraper_ips: list[str]) -> dict:
    all_bad = block_ips[:25]
    htaccess = (
        "# Hostinger / Apache (.htaccess)\n<RequireAll>\n  Require all granted\n"
        + "".join(f"  Require not ip {ip}\n" for ip in all_bad)
        + "</RequireAll>"
    ) if all_bad else "# No malicious IPs detected requiring an immediate .htaccess block."
    nginx = (
        "# Nginx server / location block\n"
        + "".join(f"deny {ip};\n" for ip in all_bad)
        + ("# Rate-limit aggressive scrapers (1 req/sec):\n# limit_req_zone $binary_remote_addr zone=scrapers:10m rate=1r/s;" if scraper_ips else "")
    ).strip()
    ufw = (
        "\n".join(f"sudo ufw deny from {ip} to any port 80,443 proto tcp" for ip in all_bad)
        if all_bad else "# No firewall blocks needed."
    )
    return {"htaccess": htaccess, "nginx": nginx, "ufw": ufw}


async def analyze_logs(raw: str, *, enrich: bool = True, intel_override: dict[str, dict] | None = None) -> dict:
    t0 = time.monotonic()
    entries, parse_stats = parse_logs(raw)
    by_ip: dict[str, list[LogEntry]] = defaultdict(list)
    for e in entries:
        by_ip[e.ip].append(e)

    # Prioritise candidate IPs with suspicious paths, bot UAs, or high request volume for live Shodan/RIPE lookup
    ranked_ips = sorted(
        by_ip,
        key=lambda ip: (
            any(_AUTH_PATH_RE.search(r.path) or _EXPLOIT_PATH_RE.search(r.path) for r in by_ip[ip]),
            any(intel_mod.claimed_crawler(r.ua) or _AI_SCRAPER_UA_RE.search(r.ua) for r in by_ip[ip]),
            len(by_ip[ip]),
        ),
        reverse=True,
    )
    intel_map = intel_override if intel_override is not None else await intel_mod.enrich_ips(ranked_ips, enabled=enrich)

    actors = [_classify_actor(ip, rows, intel_map.get(ip) or {"ip": ip, "checked": False, "links": {}}) for ip, rows in by_ip.items()]
    sev_rank = {"malicious": 0, "scraper": 1, "benign_bot": 2, "clean": 3}
    actors.sort(key=lambda a: (sev_rank.get(a["severity"], 9), -a["requests"], a["ip"]))

    sev_by_ip = {a["ip"]: a["severity"] for a in actors}
    block_ips = [a["ip"] for a in actors if a["action"] == "block"]
    scraper_ips = [a["ip"] for a in actors if a["action"] == "rate_limit"]

    req_by_sev = Counter(sev_by_ip[e.ip] for e in entries)
    return {
        "id": "tr_" + uuid.uuid4().hex[:10],
        "summary": {
            "requests": len(entries),
            "unique_ips": len(actors),
            "format": parse_stats["format"],
            "skipped_lines": parse_stats["skipped"],
            "malicious_ips": len(block_ips),
            "scraper_ips": len(scraper_ips),
            "verified_crawlers": sum(1 for a in actors if a["severity"] == "benign_bot"),
            "human_ips": sum(1 for a in actors if a["severity"] == "clean"),
            "malicious_requests": req_by_sev["malicious"],
            "scraper_requests": req_by_sev["scraper"],
            "clean_requests": req_by_sev["clean"] + req_by_sev["benign_bot"],
            "intel_enriched_ips": sum(1 for v in intel_map.values() if v.get("checked")),
        },
        "timeline": _build_timeline(entries, sev_by_ip),
        "actors": actors[:60],
        "mitigations": _mitigations(block_ips, scraper_ips),
        "notes": [
            "Processed in memory; server logs are never written to disk.",
            "IP threat intelligence combines Shodan InternetDB (open ports, tags, CVEs, rDNS) and RIPE NCC ASN registry records.",
        ],
        "elapsed_ms": round((time.monotonic() - t0) * 1000),
    }