# Progress

_Last updated: 2026-11-14 (M2 done; real call OK; ready to deploy)_

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

## In progress
- Deploy M2 + checklist.

## Real-call check (2026-11-14, dev key, claude-haiku-4.5-eu, prompt_version 426d229cfc)
- Manipulative sample: 1 call, 1632 in / 429 out tokens, 3.6 s, 5 spans, 0 validator rejections
  (bandwagon, questionable_authority, unsupported_claim, loaded_language, name_calling).
- Observation: quotes are often whole sentences instead of the shortest passage -> tune in M4 with eval data.

## Next
- M3: SSRF-safe URL fetch + extraction, receipts v1, `/api/receipts`, `/source`, TTLs, Refresh.
- Later: "Report a wrong label" (`POST /api/feedback`, 30/h) and "Share result" (`/r/{id}`) - not in M2 scope.

## Known issues / limits
- Every technique is experimental (hidden by default) until M4 measures precision -> the UI says so and
  offers "Show experimental labels".
- Rate limits are in memory: they reset on every deploy.
- Genre/language detection are simple rules; Finnish may be detected as Estonian.
- The model tends to quote whole sentences (inflates flagged_share); prompt tuning planned for M4.

## How to verify (PC, Git Bash, in the repo folder)
```bash
.venv/Scripts/python -m pip install -r backend/requirements-dev.txt   # if requirements changed
.venv/Scripts/python -m pytest -q                                     # expect: 164 passed
GIT_COMMIT=test1234 PYTHONPATH=backend .venv/Scripts/python -m uvicorn app.main:app --port 8000
# browser: http://127.0.0.1:8000 (without a key: rules-only mode + banner)
```
Server (after deploy):
```bash
curl -s https://stoprop.tech/api/health        # budget.tokens_today, llm.configured, storage
cd /docker/stoprop && docker compose exec stoprop python scripts/tagger_smoke.py   # ONE real call
```
(On Mac/Linux use `.venv/bin/python` instead of `.venv/Scripts/python`.)
