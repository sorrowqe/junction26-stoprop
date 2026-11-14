"""Deterministic 24-scenario Server-Log Bot & Scraper Benchmark (12 bad-bot/attack + 12 innocent controls)."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.traffic.detector import analyze_logs  # noqa: E402


def _clf(ip: str, sec: int, method: str, path: str, status: int, ua: str, size: int = 1420) -> str:
    mm, ss = divmod(sec, 60)
    hh, mm = divmod(mm, 60)
    return f'{ip} - - [14/Nov/2026:{12 + hh:02d}:{mm:02d}:{ss:02d} +0000] "{method} {path} HTTP/1.1" {status} {size} "-" "{ua}"'


CHROME = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
SAFARI = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
FIREFOX = "Mozilla/5.0 (X11; Linux x86_64; rv:132.0) Gecko/20100101 Firefox/132.0"


def _bad_scenarios(bg: list[str]) -> list[tuple]:
    return [
        ("atk_01_wp_cred_stuffing", "WordPress /wp-login.php credential stuffing burst", "203.0.113.50",
         bg + [_clf("203.0.113.50", 10 + k * 2, "POST", "/wp-login.php", 401, CHROME, 512) for k in range(10)], {}),
        ("atk_02_api_login_ua_rotation", "API /api/login brute-force rotating User-Agents", "203.0.113.51",
         bg + [_clf("203.0.113.51", 20 + k * 3, "POST", "/api/login", 403, [CHROME, SAFARI, FIREFOX, "Mozilla/5.0"][k % 4], 320) for k in range(8)], {}),
        ("atk_03_xmlrpc_amplification", "XML-RPC multicall credential attack", "203.0.113.52",
         bg + [_clf("203.0.113.52", 30 + k * 2, "POST", "/xmlrpc.php", 403, "Mozilla/5.0", 290) for k in range(7)], {}),
        ("atk_04_dotenv_git_scanner", "Vulnerability scanner probing /.env, /.git, /phpmyadmin", "203.0.113.53",
         bg + [_clf("203.0.113.53", 40 + k, "GET", p, 404, CHROME, 180) for k, p in enumerate(["/.env", "/.git/config", "/phpmyadmin/", "/wp-config.php.bak", "/actuator/env"])], {}),
        ("atk_05_path_traversal_probe", "Path traversal & PHPUnit RCE scanner", "203.0.113.54",
         bg + [_clf("203.0.113.54", 50 + k, "GET", p, 403, "Mozilla/5.0", 210) for k, p in enumerate(["/vendor/phpunit/eval-stdin.php", "/cgi-bin/../../etc/passwd", "/.aws/credentials", "/adminer.php"])], {}),
        ("atk_06_spoofed_googlebot_asn", "Spoofed Googlebot User-Agent from non-Google VPS ASN", "203.0.113.55",
         bg + [_clf("203.0.113.55", 60 + k * 5, "GET", f"/article/{k}", 200, "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)") for k in range(6)],
         {"203.0.113.55": {"ip": "203.0.113.55", "checked": True, "asn": 24940, "holder": "HETZNER-AS", "hostnames": ["vps.example.net"], "tags": ["cloud"], "ports": [22], "vulns_count": 0, "vulns_sample": [], "links": {}}}),
        ("atk_07_spoofed_bingbot_login", "Fake Bingbot POSTing to /admin/login", "203.0.113.56",
         bg + [_clf("203.0.113.56", 70 + k * 2, "POST", "/admin/login", 401, "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)") for k in range(4)], {}),
        ("atk_08_python_requests_scraper", "Bulk python-requests article scraper", "203.0.113.57",
         bg + [_clf("203.0.113.57", 80 + k, "GET", f"/news/item-{k}", 200, "python-requests/2.32.0") for k in range(12)], {}),
        ("atk_09_bytespider_unthrottled", "Aggressive AI crawler (Bytespider)", "203.0.113.58",
         bg + [_clf("203.0.113.58", 90 + k, "GET", f"/archive/p-{k}", 200, "Mozilla/5.0 (compatible; Bytespider)") for k in range(15)], {}),
        ("atk_10_headless_chrome_sweep", "HeadlessChrome scraping 18 pages with 0 assets", "203.0.113.59",
         bg + [_clf("203.0.113.59", 100 + k, "GET", f"/post/{k}", 200, "Mozilla/5.0 HeadlessChrome/130.0.0.0") for k in range(18)], {}),
        ("atk_11_stealth_datacenter_sweep", "Stealth browser-UA scraper on datacenter ASN (0 static assets)", "203.0.113.60",
         bg + [_clf("203.0.113.60", 110 + k, "GET", f"/catalog/page-{k}", 200, CHROME) for k in range(18)],
         {"203.0.113.60": {"ip": "203.0.113.60", "checked": True, "asn": 14061, "holder": "DIGITALOCEAN-ASN", "hostnames": [], "tags": ["cloud"], "ports": [], "vulns_count": 0, "vulns_sample": [], "links": {}}}),
        ("atk_12_vt_flagged_tor_prober", "Tor exit node flagged by VirusTotal probing endpoints", "203.0.113.61",
         bg + [_clf("203.0.113.61", 120, "GET", "/.env", 404, CHROME), _clf("203.0.113.61", 122, "POST", "/wp-login.php", 401, CHROME)],
         {"203.0.113.61": {"ip": "203.0.113.61", "checked": True, "asn": 202425, "holder": "IP Volume", "hostnames": [], "tags": ["tor"], "ports": [9001], "vulns_count": 0, "vulns_sample": [], "vt_malicious": 7, "links": {}}}),
    ]
def _clean_scenarios(bg: list[str]) -> list[tuple]:
    return [
        ("clean_01_normal_reader", "Normal desktop reader loading article + CSS/JS/images", "198.51.100.21",
         [_clf("198.51.100.21", 10 + k * 4, "GET", p, 200, CHROME) for k, p in enumerate(["/", "/static/style.css", "/static/app.js", "/img/logo.png", "/news/story-1"])], {}),
        ("clean_02_office_nat_24_reqs", "Shared office NAT IP (3 browsers loading pages + assets)", "198.51.100.22",
         [_clf("198.51.100.22", 10 + k * 2, "GET", ("/news/" + str(k) if k % 2 == 0 else "/static/style.css"), 200, [CHROME, SAFARI, FIREFOX][k % 3]) for k in range(24)], {}),
        ("clean_03_verified_googlebot", "Real Googlebot verified via AS15169 GOOGLE", "66.249.66.1",
         bg + [_clf("66.249.66.1", 20 + k * 12, "GET", p, 200, "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)") for k, p in enumerate(["/robots.txt", "/", "/news/a", "/news/b"])],
         {"66.249.66.1": {"ip": "66.249.66.1", "checked": True, "asn": 15169, "holder": "GOOGLE - Google LLC", "hostnames": ["crawl-66-249-66-1.googlebot.com"], "tags": [], "ports": [], "vulns_count": 0, "vulns_sample": [], "links": {}}}),
        ("clean_04_verified_bingbot", "Real Bingbot verified via AS8075 MICROSOFT", "40.77.167.1",
         bg + [_clf("40.77.167.1", 30 + k * 15, "GET", p, 200, "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)") for k, p in enumerate(["/robots.txt", "/about", "/contact"])],
         {"40.77.167.1": {"ip": "40.77.167.1", "checked": True, "asn": 8075, "holder": "MICROSOFT-CORP-MSN-AS-BLOCK", "hostnames": ["msnbot-40-77-167-1.search.msn.com"], "tags": [], "ports": [], "vulns_count": 0, "vulns_sample": [], "links": {}}}),
        ("clean_05_mobile_favicon_404", "Mobile Safari user hitting missing /apple-touch-icon.png 404s", "198.51.100.25",
         [_clf("198.51.100.25", 10, "GET", "/", 200, SAFARI), _clf("198.51.100.25", 11, "GET", "/static/style.css", 200, SAFARI),
          _clf("198.51.100.25", 12, "GET", "/apple-touch-icon.png", 404, SAFARI), _clf("198.51.100.25", 13, "GET", "/favicon.ico", 404, SAFARI)], {}),
        ("clean_06_single_password_typo", "Human user mistyping password once then succeeding", "198.51.100.26",
         [_clf("198.51.100.26", 10, "GET", "/login", 200, CHROME), _clf("198.51.100.26", 11, "GET", "/static/style.css", 200, CHROME),
          _clf("198.51.100.26", 20, "POST", "/api/login", 401, CHROME), _clf("198.51.100.26", 35, "POST", "/api/login", 200, CHROME)], {}),
        ("clean_07_rss_feedly_reader", "Feedly RSS reader polling /feed.xml politely", "198.51.100.27",
         bg + [_clf("198.51.100.27", 40 + k * 60, "GET", "/feed.xml", 200, "Feedly/1.0 (+http://www.feedly.com/fetcher.html)") for k in range(3)], {}),
        ("clean_08_uptime_monitor", "UptimeRobot health check hitting /api/health", "198.51.100.28",
         bg + [_clf("198.51.100.28", 60 * k, "GET", "/api/health", 200, "Mozilla/5.0+(compatible; UptimeRobot/2.0)") for k in range(5)], {}),
        ("clean_09_social_link_previews", "Telegram & Slack link-preview unfurlers", "198.51.100.29",
         bg + [_clf("198.51.100.29", 50, "GET", "/news/post-1", 200, "TelegramBot (like TwitterBot)"),
               _clf("198.51.100.29", 55, "GET", "/news/post-2", 200, "Slackbot-LinkExpanding 1.0")], {}),
        ("clean_10_deep_reader_12_pages", "Engaged reader browsing 12 articles over 9 min with CSS", "198.51.100.30",
         [_clf("198.51.100.30", 1, "GET", "/static/style.css", 200, FIREFOX)] + [_clf("198.51.100.30", 45 * (k + 1), "GET", f"/blog/post-{k}", 200, FIREFOX) for k in range(12)], {}),
        ("clean_11_broken_old_bookmark", "Visitor hitting 2 old moved article URLs (404) + homepage", "198.51.100.31",
         [_clf("198.51.100.31", 10, "GET", "/2021/old-a", 404, CHROME), _clf("198.51.100.31", 18, "GET", "/2021/old-b", 404, CHROME),
          _clf("198.51.100.31", 25, "GET", "/", 200, CHROME), _clf("198.51.100.31", 26, "GET", "/static/style.css", 200, CHROME)], {}),
        ("clean_12_applebot_verified", "Real Applebot verified via AS714 APPLE", "17.241.75.1",
         bg + [_clf("17.241.75.1", 30 + k * 20, "GET", f"/page-{k}", 200, "Mozilla/5.0 (Applebot/0.1)") for k in range(4)],
         {"17.241.75.1": {"ip": "17.241.75.1", "checked": True, "asn": 714, "holder": "APPLE-ENGINEERING", "hostnames": ["17-241-75-1.applebot.apple.com"], "tags": [], "ports": [], "vulns_count": 0, "vulns_sample": [], "links": {}}}),
    ]
def build_scenarios() -> list[dict]:
    bg = [
        _clf("198.51.100.10", 5, "GET", "/", 200, CHROME, 8400),
        _clf("198.51.100.10", 6, "GET", "/static/style.css", 200, CHROME, 4100),
        _clf("198.51.100.10", 6, "GET", "/static/app.js", 200, CHROME, 6200),
    ]
    out = [{"id": i, "kind": "bad_actor", "label": lbl, "target_ip": ip, "raw": "\n".join(lines), "intel": intel}
           for i, lbl, ip, lines, intel in _bad_scenarios(bg)]
    out += [{"id": i, "kind": "innocent", "label": lbl, "target_ip": ip, "raw": "\n".join(lines), "intel": intel}
            for i, lbl, ip, lines, intel in _clean_scenarios(bg)]
    return out


def main() -> None:
    scenarios = build_scenarios()
    tp = fp = fn = tn = 0
    details = []
    for sc in scenarios:
        res = asyncio.run(analyze_logs(sc["raw"], enrich=False, intel_override=sc["intel"]))
        actor = next((a for a in res["actors"] if a["ip"] == sc["target_ip"]), None)
        flagged = bool(actor and actor["severity"] in ("malicious", "scraper"))
        if sc["kind"] == "bad_actor":
            ok = flagged
            tp += int(flagged)
            fn += int(not flagged)
        else:
            ok = not flagged
            fp += int(flagged)
            tn += int(not flagged)
        details.append({"id": sc["id"], "kind": sc["kind"], "label": sc["label"],
                        "category": actor["category"] if actor else "none", "passed": ok})

    p = round(tp / (tp + fp), 3) if (tp + fp) else 0.0
    r = round(tp / (tp + fn), 3) if (tp + fn) else 0.0
    f1 = round(2 * p * r / (p + r), 3) if (p + r) else 0.0
    out_data = {
        "scenarios": len(scenarios), "bad_scenarios": 12, "innocent_scenarios": 12,
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": p, "recall": r, "f1": f1, "innocent_fpr": round(fp / 12, 3),
        "details": details,
    }
    out_path = ROOT / "eval" / "results" / "traffic_latest.json"
    out_path.write_text(json.dumps(out_data, indent=2) + "\n", encoding="utf-8")
    print(f"Traffic benchmark ({len(scenarios)} scenarios): P={p} R={r} F1={f1} | Innocent FPR={fp}/12 -> {out_path}")


if __name__ == "__main__":
    main()