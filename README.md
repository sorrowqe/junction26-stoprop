# Stoprop — See the Spin Before It Sees You

> **Live on Hostinger:** [https://stoprop.tech](https://stoprop.tech) · **Interactive API Docs:** [https://stoprop.tech/docs](https://stoprop.tech/docs) · **Measured Benchmarks:** [https://stoprop.tech/metrics](https://stoprop.tech/metrics)  
> *We flag rhetorical tactics and coordinated behaviours — never people, outlets, or political viewpoints.*

Stoprop is an open-web anti-propaganda & coordinated-amplification intelligence suite built for the **Hostinger Challenge at Junction**. Instead of black-box "fake news scores" that cry wolf on fair opinions or breaking news, Stoprop pairs **character-verified AI rhetoric analysis** with **deterministic provenance receipts** and **multi-signal statistical swarm detection**.

---

## Four Integrated Tools (Covering All 4 Hostinger Challenge Tracks)

| Tool | Challenge Track | Capabilities | False-Positive Guardrail |
| :--- | :--- | :--- | :--- |
| **1. Analyze & Verify** (`/`) | *Name the technique* | Highlights **14 taxonomy labels** (11 persuasion/attribution tactics + 3 claim/balance markers) span-by-span in plain English, and cross-checks factual claims against live news reporting (`/api/verify`). | Every quote is verified **character-for-character** by deterministic code (`0%` hallucinated spans); attributed quotes (*reported speech*) are never blamed on the journalist; calibrated `Strict` mode keeps clean-news false alarms $\le 5\%$. |
| **2. Check a Source** (`/source`) | *Trust the source* | Generates verifiable provenance "receipts" (`0` AI cost): RDAP + ccTLD port-43 WHOIS domain age, Wayback first capture, Certificate Transparency (`crt.sh` + CertSpotter), homoglyph/TLD-swap clone check, live HTML newsroom transparency & **outbound link-network audit**, 8-watchdog fact-check search, and verbatim copy search. | Never calls a site "fake" solely for being young — separates reassuring records, neutral facts, satire/state-media disclosures, and concrete brand-impersonation warnings. |
| **3. Swarm Mode** (`/swarm`) | *Spot the swarm* | Single smart input for **Reddit**, **Telegram (`t.me`)**, **Bluesky**, **Hacker News**, **YouTube**, CSV/JSON exports, and **5-platform open-web narrative spread tracing** (`/api/trace`), plus AI manipulation tagging on flagged clusters & paraphrase embeddings. | Requires **text reuse ($T$) + at least one independent behavioural signal** (**$S$** Poisson timing burst $p < 0.001$, **$A$** look-alike/cohort accounts, or **$M$** non-mainstream link push) **plus** 5 innocent-crowd filters (headlines, official quotes, wire syndication, live events, short replies). |
| **4. Clean the Traffic** (`/traffic`) | *Clean the traffic* | Parses Nginx/Apache Combined, Caddy/Traefik JSON, or CSV access logs (`0` AI cost), enriches IPs live via **Shodan InternetDB**, **RIPE NCC ASN**, and optional **VirusTotal**, catches credential-stuffing waves, vulnerability scanners, ASN-spoofed search crawlers & bulk AI scrapers, and outputs ready-to-paste Hostinger `.htaccess`, Nginx, and `ufw` rules. | Protects shared office/campus NAT IPs, real browsers loading CSS/JS/image assets, benign 404s, and ASN/rDNS-verified Googlebot/Bingbot/Applebot crawlers (`12/12` clean controls protected). |

---

## Architecture

```text
  Browser / API Client ──► FastAPI (/, /source, /swarm, /traffic, /metrics, /docs)
                                     │
   ┌───────────────────────┬─────────┴──────────────┬────────────────────────┐
   ▼                       ▼                        ▼                        ▼
1. Text & Claim Engine  2. Provenance Engine     3. Swarm & Spread        4. Clean-Traffic Logs
• Segment + Hints       • Lookalike / TLD-Swap   • Reddit/TG/BSky/HN/YT   • Nginx/Apache/Caddy/CSV
• Hostinger AI Router   • RDAP + Port-43 WHOIS   • Brave + Oxylabs Search • Shodan InternetDB
• Verbatim Quote Rescue • Wayback + CT Logs      • 5-gram MinHash Jaccard • RIPE NCC ASN Check
• Overlap Deduplication • Live HTML & Link Graph • Poisson Burst Scan     • VirusTotal Reputation
• Live Claim Verify     • 8-Watchdog Fact-Checks • Innocent Crowd Filters • .htaccess / Nginx / UFW
   └───────────────────────┴─────────┬──────────────┴────────────────────────┘
                                     ▼
                           SQLite (WAL) Cache & Caps
```
- **SSRF-Safe Multi-Tier Fetching (`backend/app/pipeline/extract.py`):** Validates DNS answers on every hop (blocking RFC1918, loopback, link-local, and cloud metadata IPs), orders IPv4 ahead of IPv6 to prevent dual-stack stalls, extracts article text via `trafilatura`, and fails over to Oxylabs AI Studio on bot-blocked pages with a daily cap.
- **Resilient Search Failover (`backend/app/pipeline/websearch.py`):** Uses Brave Search API (`extra_snippets=true`, 1-QPS pacing) as primary with automatic failover to Oxylabs Instant Search and GDELT DOC 2.0.

---

## Measured Accuracy & False-Positive Benchmarks

All evaluation harnesses (`eval/`) and result snapshots (`eval/results/`) are committed and rendered live on [`/metrics`](https://stoprop.tech/metrics):

| Benchmark Suite | Dataset Size | Headline Accuracy | Clean / Innocent Pass Rate (False-Alarm Check) |
| :--- | :--- | :--- | :--- |
| **Text Detector — Dev Split** (`Strict` default) | 89 passages (SemEval-2020 + multi-technique) | **65.4% Precision** · **41.3% F1** *(Broad F1: **48.6%**)* | **24 / 25 clean (96.0%)** — `1/21` (`4.8%`) on SemEval clean news + `0/4` on opinion controls (`95% CI 80.5–99.3%`) |
| **Text Detector — Locked Held-Out Test Split** (`test_final.json`) | 49 unseen passages (never used in tuning) | **60.0% Precision** (**80.0%** on multi-technique) · **47.2% F1** | **11 / 11 clean (100%)** — `0/9` on unseen SemEval clean news + `0/2` on unseen hard negatives |
| **Swarm Mode Stress Benchmark** (`0` AI calls, seed `2026`) | 40 multi-post feeds (20 campaigns + 20 organic crowds) | **100% Precision** · **85.0% Recall (17/20)** · **91.9% F1** | **20 / 20 clean (100%)** — `0` false alarms across BBC/ERR/Reuters headlines, quotes, AP/Reuters wire syndication & live events (`95% CI 83.9–100%`) |
| **Clean-Traffic Log Benchmark** (`0` AI calls, `traffic_latest.json`) | 24 server-log feeds (12 attack/bot + 12 real visitor/crawler) | **100% Precision** · **100% Recall (12/12)** · **100% F1** | **12 / 12 clean (100%)** — `0` false blocks across shared office NAT, ASN-verified Googlebot/Bingbot/Applebot, RSS readers & benign 404s |

---

## Quick API Examples

```bash
# 1. Analyze text for manipulation techniques
curl -s https://stoprop.tech/api/analyze \
  -H 'Content-Type: application/json' \
  -d '{"text": "Everyone knows corrupt bureaucrats signed away port revenue behind closed doors!", "sensitivity": "strict"}'

# 2. Check domain & live-page provenance receipts (0 AI credits)
curl -s https://stoprop.tech/api/receipts \
  -H 'Content-Type: application/json' \
  -d '{"url": "https://www.bbc.com/"}'

# 3. Inspect a public Reddit / Telegram / Bluesky thread or channel for coordinated swarms
curl -s https://stoprop.tech/api/swarm \
  -H 'Content-Type: application/json' \
  -d '{"url": "https://www.reddit.com/r/worldnews", "tag_clusters": true}'

# 4. Trace where a claim or article spread across Open Web, Reddit, Telegram, Bluesky & HN
curl -s https://stoprop.tech/api/trace \
  -H 'Content-Type: application/json' \
  -d '{"text": "Federal Reserve interest rate decision"}'

# 5. Inspect web-server access logs for credential stuffers, spoofed crawlers & AI scrapers
curl -s -X POST https://stoprop.tech/api/traffic \
  -H 'Content-Type: text/plain' --data-binary @/var/log/nginx/access.log
```

---

## Local Setup & Tests

```bash
python -m venv .venv
.venv/Scripts/pip install -r backend/requirements.txt   # (.venv/bin/pip on Linux/macOS)
cp .env.example .env.local                               # set ROUTER_API_KEY (optional: BRAVE_API_KEY)
PYTHONPATH=backend .venv/Scripts/uvicorn app.main:app --reload --port 8000

# Offline test suite (606+ tests, 0 live network or AI calls)
.venv/Scripts/python -m pytest -q

# Production deployment (Hostinger VPS via Docker Compose + Traefik TLS)
docker compose up -d --build
```

## License

MIT — see [`LICENSE`](LICENSE).
