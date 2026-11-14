# Progress

_Last updated: 2026-11-14 (M3 done; ready to deploy)_

Spec: CLAUDE.md **v2** (web service only, no extension, swarm mode in M5).

## Done
- **M0** walking skeleton + pull-based deploy (`deploy.sh` -> `live` -> cron `server-deploy.sh`).
- **M1** segment / heuristics / validate / profile, heuristics-only analyze, web analyzer UI.
- **v2 reconcile** (commit 1260de5): extension + EXTENSION_ORIGINS removed, MODEL_EMBED added, pages
  `/source` `/swarm` `/metrics` (placeholders), `/methodology` (principles), `/api` (curl examples), `/docs`,
  nav + tool cards, swarm/eval stubs, `innocent_explanations` + `receipts` fields.
- **M2** AI tagging:
  - `llm/client.py` (AsyncOpenAI on httpx2, 30 s timeout, 1 retry on 5xx/timeout, every attempt logged to SQLite,
    robust JSON array extraction), `llm/budget.py` (DAILY_TOKEN_CAP, modes ok/high/over),
    `ratelimit.py` (analyze 30/h, swarm 10/h, receipts 60/h, feedback 30/h per IP), `store.py` (SQLite:
    paragraph cache, sightings, usage log; memory fallback).
  - `prompts/tagger_system.md` (static rules, taxonomy rendered in), `pipeline/tagger.py` (per-paragraph cache,
    single-flight, ~1200-word chunks, private mode, fail soft per chunk).
  - `analyze.py`: mode `llm` / `heuristics_only` with `mode_reason` (ok / no_key / budget / llm_error),
    cost + `llm_calls`, `stats.seen_before`, innocent explanations, background pre-warm of demo samples.
  - `/api/health`: budget (tokens today, cap, share, mode), llm configured + model, storage kind.
  - Web: "Don't store this text", mode banners, innocent-explanations card, AI cost / cache in profile.
  - `scripts/tagger_smoke.py`: ONE real tagger call on a sample (in-memory store).
  - Tests: 164 passed, LLM always mocked (a test fixture makes any real call fail loudly).

- **M3** sources:
  - `pipeline/extract.py`: SSRF-safe fetch (static URL checks, public-only DNS, guarded connect to the vetted IP,
    manual redirects max 3 re-checked, 8 s, 2 MB, HTML only, no env proxies) + trafilatura main text/title/date.
  - `receipts/`: `lookalike` (outlets.yaml, 48 outlets: homoglyphs/punycode, TLD swap, outlet-name subdomain, typos,
    extra words/hyphens), `rdap` (domain age), `wayback` (first capture), `crtsh` (first certificate, tolerant),
    `factcheck` (only with FACTCHECK_API_KEY, else not_checked), `syndication` (not_checked),
    `service.py` (soft/hard TTLs, 10-min negative cache, single-flight, background refresh, 15-min Refresh limit,
    budget-aware TTLs), `domain.py` (IDNA + offline PSL), `http.py` (8 s, size caps, guarded client).
  - API: `POST /api/receipts` (60/h per IP), `/api/analyze` with `url` (returns `text`, `source`, `receipts`).
  - Web: URL field on `/` (fetched text shown in the editor), Source card with receipts + Refresh; real `/source` page.
  - Tests: 340 passed (101 SSRF cases incl. rebinding + redirect to private; receipts with HTTP mocked; real
    DNS/HTTP forbidden in tests).

## In progress
- Deploy M3 + checklist.

## M3 real-network check (local PC, no AI key)
- 11 SSRF URLs via the running app with real DNS (incl. 127.0.0.1.nip.io, localtest.me) -> all 400.
- Articles fetched OK: news.err.ee, rus.err.ee, err.ee, bbc.com/news/articles/..., theguardian.com.
  apnews.com (403) and reuters.com (401) refuse bots -> honest error message.
- RDAP: .ee domains have no RDAP via rdap.org (-> "not found", honest). web.archive.org unreachable from the
  venue network at test time; crt.sh frequently times out -> "check failed / try Refresh later".

## Real-call check (2026-11-14, dev key, claude-haiku-4.5-eu, prompt_version 426d229cfc)
- Manipulative sample: 1 call, 1632 in / 429 out tokens, 3.6 s, 5 spans, 0 validator rejections
  (bandwagon, questionable_authority, unsupported_claim, loaded_language, name_calling).
- Observation: quotes are often whole sentences instead of the shortest passage -> tune in M4 with eval data.

## Next
- M4: `sheet_to_jsonl.py`, `run_eval.py`, `/metrics`, small bake-off (announce calls), gating; prompt tuning for shorter quotes.
- Later: "Report a wrong label" (`POST /api/feedback`, 30/h) and "Share result" (`/r/{id}`) - not in M2 scope.

## Known issues / limits
- Every technique is experimental (hidden by default) until M4 measures precision -> the UI says so and
  offers "Show experimental labels".
- Rate limits are in memory: they reset on every deploy.
- Genre/language detection are simple rules; Finnish may be detected as Estonian.
- The model tends to quote whole sentences (inflates flagged_share); prompt tuning planned for M4.
- Receipts: .ee has no RDAP; crt.sh is often overloaded; some outlets (AP, Reuters) block fetching -> paste text.
- Lookalike can flag dictionary words one letter away from an outlet name (e.g. lemonade vs lemonde).
- Rate limit is per IP: at the venue many people may share one IP (30 analyses/h). Open question to Aleksandr.

## How to verify (PC, Git Bash, in the repo folder)
```bash
.venv/Scripts/python -m pip install -r backend/requirements-dev.txt   # if requirements changed
.venv/Scripts/python -m pytest -q                                     # expect: 340 passed
GIT_COMMIT=test1234 PYTHONPATH=backend .venv/Scripts/python -m uvicorn app.main:app --port 8000
# browser: http://127.0.0.1:8000 (without a key: rules-only mode + banner)
```
Server (after deploy):
```bash
curl -s https://stoprop.tech/api/health        # budget.tokens_today, llm.configured, storage
cd /docker/stoprop && docker compose exec stoprop python scripts/tagger_smoke.py   # ONE real call
```
(On Mac/Linux use `.venv/bin/python` instead of `.venv/Scripts/python`.)
