"""Tests for Clean the Traffic (/traffic & POST /api/traffic): log parsers, Shodan + RIPE + VirusTotal
enrichment, spoofed-crawler & credential-stuffing detection, innocent NAT protection, and mitigations."""
from __future__ import annotations

import httpx2
from fastapi.testclient import TestClient

from app.main import app
from app.swarm.collectors import http as chttp

client = TestClient(app)


def _mock_intel_transport(monkeypatch):
    def handler(req: httpx2.Request):
        host, path = req.url.host, req.url.path
        if host == "internetdb.shodan.io":
            ip = path.rsplit("/", 1)[-1]
            if ip == "185.220.101.1":
                return httpx2.Response(200, json={
                    "ip": ip, "ports": [80, 443, 9001], "tags": ["tor", "scanner"],
                    "hostnames": ["tor-exit.example.org"], "vulns": ["CVE-2024-1234", "CVE-2023-9999"],
                })
            if ip == "45.33.32.156":
                return httpx2.Response(200, json={"ip": ip, "ports": [22, 80], "tags": ["cloud"], "hostnames": ["vps.example"], "vulns": []})
            return httpx2.Response(404, json={"detail": "No information available"})
        if host == "stat.ripe.net":
            ip = req.url.params.get("resource", "")
            holders = {
                "66.249.66.1": (15169, "GOOGLE - Google LLC", "66.249.64.0/19"),
                "185.220.101.1": (202425, "IP Volume inc", "185.220.101.0/24"),
                "45.33.32.156": (24940, "HETZNER-AS", "45.33.32.0/24"),
            }
            if ip in holders:
                asn, holder, pref = holders[ip]
                return httpx2.Response(200, json={"data": {"resource": pref, "asns": [{"asn": asn, "holder": holder}]}})
            return httpx2.Response(200, json={"data": {"resource": ip, "asns": []}})
        if host == "www.virustotal.com":
            return httpx2.Response(200, json={"data": {"attributes": {"last_analysis_stats": {"malicious": 5, "suspicious": 1}}}})
        return httpx2.Response(404)

    monkeypatch.setattr(chttp, "client_factory", lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))
def test_traffic_combined_logs_shodan_ripe_vt_and_mitigations(monkeypatch):
    _mock_intel_transport(monkeypatch)
    monkeypatch.setenv("VIRUSTOTAL_API_KEY", "vt-test-key")
    logs = "\n".join([
        '93.184.216.34 - - [14/Nov/2026:18:20:01 +0000] "GET / HTTP/1.1" 200 8400 "-" "Mozilla/5.0 Chrome/131.0"',
        '93.184.216.34 - - [14/Nov/2026:18:20:02 +0000] "GET /static/style.css HTTP/1.1" 200 4100 "-" "Mozilla/5.0 Chrome/131.0"',
        '93.184.216.34 - - [14/Nov/2026:18:20:03 +0000] "GET /favicon.ico HTTP/1.1" 404 120 "-" "Mozilla/5.0 Safari/605.1"',
        '66.249.66.1 - - [14/Nov/2026:18:20:10 +0000] "GET /robots.txt HTTP/1.1" 200 180 "-" "Mozilla/5.0 (compatible; Googlebot/2.1)"',
        '66.249.66.1 - - [14/Nov/2026:18:20:18 +0000] "GET /metrics HTTP/1.1" 200 7200 "-" "Mozilla/5.0 (compatible; Googlebot/2.1)"',
        '45.33.32.156 - - [14/Nov/2026:18:20:20 +0000] "GET /page-1 HTTP/1.1" 200 5000 "-" "Mozilla/5.0 (compatible; Googlebot/2.1)"',
        '45.33.32.156 - - [14/Nov/2026:18:20:22 +0000] "GET /page-2 HTTP/1.1" 200 5000 "-" "Mozilla/5.0 (compatible; Googlebot/2.1)"',
        *[f'185.220.101.1 - - [14/Nov/2026:18:20:{30 + k:02d} +0000] "POST /wp-login.php HTTP/1.1" 401 512 "-" "Mozilla/5.0"' for k in range(6)],
    ])
    r = client.post("/api/traffic", json={"data": logs, "enrich": True})
    assert r.status_code == 200
    body = r.json()
    by_ip = {a["ip"]: a for a in body["actors"]}

    assert by_ip["93.184.216.34"]["category"] == "human_visitor" and by_ip["93.184.216.34"]["action"] == "allow"
    assert by_ip["66.249.66.1"]["category"] == "verified_crawler"
    assert by_ip["45.33.32.156"]["category"] == "spoofed_crawler" and by_ip["45.33.32.156"]["action"] == "block"
    assert by_ip["185.220.101.1"]["category"] == "credential_stuffing"
    assert "tor" in by_ip["185.220.101.1"]["intel"]["tags"]
    assert by_ip["185.220.101.1"]["intel"]["vulns_count"] == 2
    assert by_ip["185.220.101.1"]["intel"]["vt_malicious"] == 5

    ht = body["mitigations"]["htaccess"]
    assert "185.220.101.1" in ht and "45.33.32.156" in ht
    assert "93.184.216.34" not in ht and "66.249.66.1" not in ht


def test_traffic_caddy_json_csv_raw_upload_and_metrics():
    caddy = "\n".join([
        '{"ts":1794680401,"request":{"client_ip":"203.0.113.77","method":"GET","uri":"/.env","headers":{"User-Agent":["curl/8.7"]}},"status":404,"size":120}',
        '{"ts":1794680402,"request":{"client_ip":"203.0.113.77","method":"GET","uri":"/.git/config","headers":{"User-Agent":["curl/8.7"]}},"status":404,"size":120}',
        '{"ts":1794680403,"request":{"client_ip":"203.0.113.77","method":"GET","uri":"/phpmyadmin/","headers":{"User-Agent":["curl/8.7"]}},"status":404,"size":120}',
    ])
    r = client.post("/api/traffic", content=caddy.encode("utf-8"), headers={"Content-Type": "text/plain"})
    assert r.status_code == 200 and r.json()["actors"][0]["category"] == "vuln_scanner"

    csv_logs = "ip,timestamp,method,path,status,user_agent\n203.0.113.88,2026-11-14T12:00:01Z,GET,/p1,200,python-requests/2.31\n"
    r2 = client.post("/api/traffic", json={"data": csv_logs, "enrich": False})
    assert r2.status_code == 200 and r2.json()["actors"][0]["category"] == "ai_or_bulk_scraper"

    assert client.get("/traffic").status_code == 200
    m = client.get("/api/metrics").json()
    assert m["traffic"] is not None and m["traffic"]["scenarios"] == 24 and m["traffic"]["f1"] == 1.0