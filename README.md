# Stoprop

**See the spin before it sees you. We flag the tactics, not the opinions.**

Stoprop is a web app and public API that looks for signs of propaganda and coordinated manipulation online.
It labels *how* something is said and *how* a crowd behaves. It never labels people, outlets or viewpoints, and it
never produces a single "propaganda score".

- Live: <https://stoprop.tech>
- API reference: <https://stoprop.tech/docs> (examples on <https://stoprop.tech/api>)
- Measured results: <https://stoprop.tech/metrics>
- How it works and where it fails: <https://stoprop.tech/methodology>

Built for the Hostinger challenge at Junction 2026. Version 1.0.0.

## What it does

| Tool | Page / endpoint | What you get | Uses AI |
| :--- | :--- | :--- | :--- |
| **Analyze** | `/` · `POST /api/analyze` · `POST /api/verify` | Manipulation techniques highlighted span by span, each explained in plain language. Optional claim check against recent news ("corroborated", "contradicted", "cited source not found", "no reports found", never "true/false"). | Yes |
| **Check a source** | `/source` · `POST /api/receipts` | Provenance "receipts" for a domain or article link: registration age, first archive capture, first TLS certificate, look-alike / clone check against known outlets, live-page transparency markers, outbound links, fact-checker mentions, verbatim copies elsewhere. | No |
| **Swarm mode** | `/swarm` · `POST /api/swarm` · `POST /api/trace` | Coordinated behaviour in a batch of posts (a public Reddit, Telegram, Bluesky, Hacker News or YouTube link, or a CSV/JSON upload), or a trace of where one text spread across the open web and public platforms. | Optional |
| **Clean traffic** | `/traffic` · `POST /api/traffic` | Web-server access logs split into credential stuffing, vulnerability scanners, spoofed crawlers, bulk scrapers, verified crawlers and real visitors, with ready-to-paste `.htaccess`, Nginx and `ufw` rules. | No |

Supporting pages: `/metrics` (benchmark results), `/methodology`, `/api`, `/docs` (OpenAPI).

## Rules the detection follows

1. **How, not what.** Techniques ("this sentence uses a fear appeal") and behaviours ("these 12 accounts posted the
   same text within 6 minutes"), never "propagandist" or "bot".
2. **The model proposes, plain code verifies.** Every quote the model returns must be found character for character
   in your text, or it is dropped. Offsets are computed by our code, never taken from the model.
3. **Reported speech is not the author's manipulation.** Loaded words attributed to a specific named source are
   marked as reported speech and not counted. Vague sources ("experts say") do not get that pass.
4. **Abstain instead of guessing.** "Nothing found", "insufficient data" and "not checked" are normal outputs.
5. **Two independent signals before a strong verdict.** A group of posts is "likely coordinated" only with copied
   text *plus* a timing burst, an account pattern or a shared link push. Copied text alone is "worth watching".
6. **Innocent explanations are always shown** (a shared news headline, a quoted official statement, an event hashtag).
7. **Colours describe structure, not truth.** Amber means "no source given in the text", not "false".
8. **Privacy by default.** No accounts and no reading history. "Don't store this text" skips the shared cache.
   Swarm uploads and server logs are processed in memory.
9. **No AI-generated-text detection.** Such detectors are unreliable and biased against non-native writers.

## Measured results

Every figure below comes from a file in [`eval/results/`](eval/results/) and is shown live on
[`/metrics`](https://stoprop.tech/metrics). The text benchmarks were run on 14 November 2026 with
`gemini-3.5-flash-lite-eu` and the same prompt that is deployed today (prompt version `c7db1a7998`).

**These are small benchmarks. Read them as "the mechanics work and false alarms are rare on this sample", not as
proof of real-world accuracy.**

### Text analyzer (English only)

Reference data: human-labelled news paragraphs from SemEval-2020 Task 11 plus controlled multi-technique passages
that we generated and filtered. A flagged span counts as correct when it has the same label as a reference span and
overlaps at least half of the shorter one. Reported speech is excluded.

Development split, 89 passages, 116 reference spans:

| Sensitivity | Precision | Recall | F1 | Clean news paragraphs flagged | Hard negatives flagged |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Strict** (default) | 65.4% (95% CI 52–77%) | 30.2% (23–39%) | 41.3% | 1 of 21 | 0 of 4 |
| Balanced | 60.5% | 40.5% | 48.5% | 3 of 21 | 0 of 4 |
| Broad (no confidence filter) | 58.8% | 41.4% | 48.6% | 3 of 21 | 0 of 4 |

Locked test split, 49 passages never used for tuning, run once at Broad:

| Subset | Precision | Recall | F1 | Clean passages flagged |
| :--- | :--- | :--- | :--- | :--- |
| All 49 passages | 60.0% | 38.9% | 47.2% | 0 of 11 |
| SemEval news (33) | 45.0% | 29.0% | 35.3% | 0 of 9 |
| Controlled passages (16) | 80.0% | 52.2% | 63.2% | 0 of 2 |

What these numbers say, plainly:

- **It rarely cries wolf on this sample**, and it is right about 6 times out of 10 when it does flag something.
- **It misses most techniques in real news** (about 30% recall on SemEval). It does much better on our own
  controlled passages than on real news, so the controlled figures flatter it.
- **No technique is "validated" yet** by our own bar (precision of at least 0.70 on at least 15 flagged spans).
  Only `loaded_language` has enough flagged spans (32, precision 59%). Seven of the thirteen labels have four or
  fewer reference spans in the development split.
- **Viewpoint symmetry is not there yet.** On 11 mirrored pairs (same argument, sides swapped), 5 pairs received
  label sets that differed by one label.
- Other languages are accepted and use the same Strict default, but are not benchmarked.

### Swarm mode

40 hand-built scenarios (20 coordinated, 20 innocent), seeded, no AI calls
([`eval/swarm/`](eval/swarm/)): **17 of 20 campaigns flagged "likely coordinated", 0 of 20 innocent crowds
flagged.** The three misses are a 5-account burst buried in 180 organic posts, a heavily paraphrased talking point,
and three long-standing accounts posting hours apart with no second signal. The last two are missed on purpose
rather than risk a false accusation.

The scenarios are synthetic and scored per scenario. They measure the mechanics, not how often coordination happens
in the wild or how well real campaigns are caught.

### Clean traffic

24 hand-built log scenarios (12 attacks or bots, 12 real visitors and verified crawlers), no AI calls
([`eval/traffic/`](eval/traffic/)): **12 of 12 flagged, 0 of 12 innocent visitors blocked.** These scenarios were
written alongside the rules, so treat this as a regression test more than an accuracy estimate.

### Reproduce

```bash
.venv/Scripts/python eval/swarm/run_swarm_eval.py       # 0 AI calls
.venv/Scripts/python eval/traffic/run_traffic_eval.py   # 0 AI calls
.venv/Scripts/python eval/run_eval.py                   # text, dev split: prints the planned AI calls and credits, runs nothing
.venv/Scripts/python eval/run_eval.py --yes             # ...then actually runs it (needs ROUTER_API_KEY in .env.local)
.venv/Scripts/python eval/calibrate.py --dry-run        # sensitivity thresholds from the cached run, 0 AI calls, writes nothing
```

The locked test split (`eval/data/test/`) is not in the repository.

## Known gaps

- English is the only benchmarked language.
- Claim verification, the look-alike domain check and the spread trace have unit tests but no accuracy benchmark.
- Login-walled platforms (X, Facebook, Instagram, TikTok) are not read. Paste the post text or upload an export.
- Irony, dog-whistles and what a text leaves out cannot be caught from wording alone.
- Planned in the original brief and **not built**: shareable result links, a "report a wrong label" button, and a
  visible "validated / not yet validated" marker on each technique.

## How it is put together

```text
Browser or API client
        │
FastAPI (one container behind Traefik)            web/  static HTML, CSS, vanilla JS (no build step)
        │
        ├─ Analyze    segment → word-rule hints → LLM tagger → verbatim validator → sensitivity gate → profile
        ├─ Verify     pick up to 3 claims → news / web search → read pages safely → verbatim evidence
        ├─ Receipts   RDAP / WHOIS · Wayback · certificate logs · look-alike check · live page audit · fact-checks
        ├─ Swarm      collectors → text reuse (MinHash) · timing (Poisson) · accounts · amplification → verdicts
        ├─ Trace      fingerprint sentences → search → verify copies → swarm detector on dated copies
        └─ Traffic    log parser → per-IP classifier → Shodan InternetDB / RIPE / VirusTotal → block rules
        │
SQLite (paragraph cache, usage log, receipt cache) · per-IP rate limits · daily AI token cap
```

- **AI use is bounded.** The tagger is one small model through the Hostinger AI Router (OpenAI-compatible). Results
  are cached per paragraph. A daily token cap switches the analyzer to rule-based hints when it is reached. Without
  a key the app still runs in that rule-based mode.
- **Fetching is SSRF-safe.** Only public http/https addresses, every redirect re-checked, 8 s and 2 MB limits.
- **Rate limits per client IP per hour:** analyze 30, receipts 60, traffic 30, verify 10, swarm 10, trace 5.

```text
backend/app/        main.py (routes) · analyze.py · pipeline/ · verify/ · receipts/ · swarm/ · trace/ · traffic/ · llm/
backend/config/     techniques.yaml (taxonomy + thresholds) · outlets.yaml · lexicons/ · languages.yaml
backend/prompts/    tagger_system.md
backend/tests/      607 tests, no network and no AI calls
eval/               gold-set tooling, benchmark runners, results/
web/                pages + static/ (style.css, ui.js, page scripts, self-hosted fonts, logo)
scripts/            server-deploy.sh and one-off live checks
docs/               PROGRESS.md · DECISIONS.md · METHODOLOGY.md
```

## API in five calls

```bash
# 1. Techniques in a text
curl -s https://stoprop.tech/api/analyze -H 'Content-Type: application/json' \
  -d '{"text": "Everyone knows corrupt bureaucrats signed away port revenue behind closed doors!", "sensitivity": "strict"}'

# 2. Provenance receipts for a site (no AI)
curl -s https://stoprop.tech/api/receipts -H 'Content-Type: application/json' \
  -d '{"url": "https://www.bbc.com/"}'

# 3. Coordinated behaviour in a public thread or channel
curl -s https://stoprop.tech/api/swarm -H 'Content-Type: application/json' \
  -d '{"url": "https://www.reddit.com/r/worldnews", "tag_clusters": true}'

# 4. Where a claim or article spread
curl -s https://stoprop.tech/api/trace -H 'Content-Type: application/json' \
  -d '{"text": "Federal Reserve interest rate decision"}'

# 5. Bots and scrapers in an access log (no AI)
curl -s -X POST https://stoprop.tech/api/traffic -H 'Content-Type: text/plain' \
  --data-binary @/var/log/nginx/access.log
```

Also: `POST /api/verify`, `GET /api/techniques`, `GET /api/metrics`, `GET /api/health`.

## Run it locally

Commands are for Git Bash on Windows. On Linux or macOS use `.venv/bin/` instead of `.venv/Scripts/`.

```bash
python -m venv .venv
.venv/Scripts/pip install -r backend/requirements-dev.txt
PYTHONPATH=backend .venv/Scripts/uvicorn app.main:app --reload --port 8000
```

Open <http://127.0.0.1:8000>. With no key set, the analyzer shows a "AI analysis is not switched on" banner and
uses word rules only. Check a source, Swarm mode and Clean traffic work without any key.

```bash
.venv/Scripts/python -m pytest -q        # expect: 607 passed
```

### Configuration

Copy `.env.example` to `.env` on the server. Nothing is required to start the app.

| Variable | Needed for |
| :--- | :--- |
| `ROUTER_API_KEY`, `ROUTER_BASE_URL` | AI analysis (Hostinger AI Router) |
| `MODEL_TAGGER`, `MODEL_SUMMARY`, `MODEL_EMBED` | Which models are used (defaults in `.env.example`) |
| `DAILY_TOKEN_CAP` | Daily AI budget; rule-based mode above it |
| `BRAVE_API_KEY`, `SEARCH_API_KEY` / `OXYLABS_API_KEY`, `SEARCH_PROVIDER`, `OXYLABS_DAILY_CAP` | Claim verification, spread trace, "copies elsewhere", reading bot-blocked pages |
| `GOOGLE_API_KEY` | YouTube comments in Swarm mode |
| `FACTCHECK_API_KEY` | Google Fact Check Tools receipts |
| `VIRUSTOTAL_API_KEY` | Extra IP reputation in Clean traffic (Shodan and RIPE need no key) |
| `RATE_LIMIT_MULTIPLIER` | Loosen rate limits on a shared network |

Secrets live only in `.env` (server) and `.env.local` (development). Both are git-ignored.

## Deploy

Production runs as one Docker container behind an existing Traefik on a Hostinger VPS
([`docker-compose.yml`](docker-compose.yml)). Deployment is pull-based:

```bash
bash deploy.sh    # pushes main and main:live, then waits until /api/health reports the new commit
```

A cron job on the server runs [`scripts/server-deploy.sh`](scripts/server-deploy.sh) every minute: it fetches the
`live` branch, rebuilds, health-checks the new container and rolls back automatically if it is unhealthy.

## Credits and licence

- Code: MIT, see [`LICENSE`](LICENSE).
- Fonts: Instrument Sans and Instrument Serif, SIL Open Font License, self-hosted in
  [`web/static/fonts/`](web/static/fonts/) with their licence files. The site loads nothing from third parties.
- Evaluation data: SemEval-2020 Task 11 (Da San Martino et al., 2020), see
  [`eval/data/raw/ATTRIBUTION.md`](eval/data/raw/ATTRIBUTION.md).
