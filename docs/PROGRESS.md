# Progress

_Last updated: 2026-11-14 (M4 deployed + verified; M5 Part A (swarm detector + UI) done, Part B next)_

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

- **Gold-set tooling (pre-M4)**: `eval/goldcsv.py` (CSV format, `[[label|phrase|rs]]` markers, validation),
  `eval/make_synthetic.py` (seeded specs, gpt-5.5-eu, dry run by default), `eval/semeval_sample.py`
  (Zenodo download, md5 check, label mapping) -> `eval/data/raw/semeval_sample.csv` (100 paragraphs, 112 spans).

## M4 (deployed + verified by Aleksandr)
- Scope change: evaluation is **English only** (ET/RU kept in the CSVs, excluded). Built: `config/languages.yaml` +
  `app/languages.py` (language_evaluated, strict default, UI note), indirect reported speech (validate.py, prompt,
  CLAUDE.md §1.3), `eval/ai_review.py` (Gemini + Mistral consensus), `eval/sheet_to_jsonl.py`, `eval/run_eval.py`,
  `/api/metrics` + `/metrics` (disclosures, language coverage, results, test-set quality). Tests: 416 passed.
- Synthetic gold GENERATED (gpt-5.5-eu, 23 calls, ~49k tokens): 90 items (157 rows) + 20 symmetry pairs.
- AI review DONE (34.88 credits): 38/50 English synthetic paragraphs accepted, 12 excluded (mostly hard negatives:
  Mistral found unmarked techniques or rejected rows; Gemini accepted everything -> kappa 0.0, raw agreement 87.7%),
  11/12 symmetry pairs kept, 25 reported-speech corrections. sheet_to_jsonl: 89 dev / 49 test items (English),
  clean sets: 30 SemEval "none" + 6 synthetic hard negatives (all splits), 63 ET/RU rows not evaluated.
- Sample run DONE (`--limit 10`, 14 Haiku calls, dev key): 4.69 credits = **0.335 credits per call**, ~1,750 tokens and
  2.3 s per analysis, 0 failures, 0 validator rejections. Written to eval/results/sample.json (never shown, never gates).
  Measured rates per model are kept in eval/results/credit_rates.json; every plan projects credits from them.
- Reported-speech fix (validate.py, specific-source rule, contrast clauses, more reporting verbs): sample re-scored
  from cache (`--cache-only`, 0 calls): synthetic strict now equals lenient (P=R=1.0 on 7 / 10 spans). Tests: 449 passed.
- Bake-off = FIXED subset eval/data/bakeoff_subset.json (seeded quota: 10 SemEval technique + 8 synthetic technique
  + 6 SemEval "none" + 2 synthetic hard negatives + 2 symmetry pairs = 30 analyses per model, same for every model).
  `run_eval.py --bakeoff` plans it; results go to bakeoff.json (shown on /metrics), never to latest.json, never gate.
  Cost guard: each model's first analysis runs alone; stop if it failed or cost > 2x the projected credits per call.
- Full English dev run DONE (claude-haiku-4.5-eu, 97 calls, 29.7 credits, 0 failures): strict F1 synthetic 0.853,
  SemEval 0.348; clean FPR (any flag) SemEval "none" 38% (8/21), synthetic hard negatives 25% (1/4); symmetry flip 36%
  (4/11). "balanced" = "broad" on this data; "strict" cuts SemEval-none FPR to 4.8% but halves recall. Gating:
  exaggeration_minimisation (0.857, n=7) and questionable_authority (0.8, n=5) are no longer experimental.
- Bake-off DONE (fixed 30-analysis subset, 10.0 credits): gemini-3.5-flash-lite-eu F1 0.600 = Haiku 0.600 at 0.093
  credits/analysis (Haiku ~0.31/call); gpt-5.4-mini F1 0.507 (0.211); mistral-small-4-eu F1 0.061 (abstains on 29/30).
- Tagger SWITCHED to gemini-3.5-flash-lite-eu (temperature 0). Full dev run: 82 calls, 7.61 credits, 1 failure
  (unusable JSON twice -> rules), strict F1 synthetic 0.778, SemEval 0.286; SemEval-none technique FPR 1/21 at broad.
- Self-consistency (`eval/self_consistency.py`, 11 calls, 1.12 credits, separate cache selfcons.db): 0/11 self-flips
  at broad (1/11 at strict/balanced); symmetry flips 1/11 at broad.
- Calibration (`eval/calibrate.py`, 0 calls): strict >= 0.9, balanced >= 0.85, broad = no filter; SemEval-none
  technique FPR 0/21, 1/21, 1/21 (Wilson CIs on /metrics). Site default = strict for every language. Shown by default
  (precision >= 0.70 at broad, >= 5 predictions): exaggeration_minimisation only; all other labels experimental.
- /metrics: sensitivity trade-off table with CIs, self-flip baseline, SemEval-2020 context (verified: best SI F1 51.74,
  best TC micro-F1 63.74, corrected, arXiv:2009.02696), Gemini-overlap disclosure. Analyzer shows the measured
  trade-off under the sensitivity select.
- NEXT: deploy (server .env MODEL_TAGGER change first). Sunday morning: `run_eval.py --final` (announce calls).

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

## M5 Part A DONE (swarm detector + UI, not yet deployed)
- `backend/app/swarm/`: `ingest.py` (CSV/JSON, aliases, ISO/unix s/ms, row-level reasons, 2000 posts / 2 MB),
  `textreuse.py` (T: normalize + lowercase, URLs/@mentions stripped, char 5-gram MinHash LSH via datasketch,
  exact Jaccard >= 0.6 decides, >= 8 words, different authors; deep: embeddings >= 12 words, cosine >= 0.92,
  max 2000, "paraphrase"), `timing.py` (S: tightest window with 60% of posts vs batch rate, Poisson sf p < 0.001,
  >= 3 authors; cadence CV < 0.2 over >= 5 posts as supporting evidence), `accounts.py` (A: name stem + digits,
  Levenshtein <= 2/1/0 by stem length; creation within 7 days; single-purpose >= 3 posts with >= 70% in cluster),
  `amplification.py` (M: same non-mainstream URL / hashtag / mention by >= 5 authors; outlets.yaml never counts),
  `clusters.py` (components, verdicts, innocent patterns, shared-accounts note, optional tagging max 10),
  `explain.py` (behaviour wording, evidence for/against).
- `POST /api/swarm` (JSON {data|posts, deep, tag_clusters} or raw text/csv; 10/h, invalid uploads not counted),
  `llm/client.embed()` (MODEL_EMBED, usage + credits logged as kind "embed").
- `/swarm` page: paste/upload/sample, options (deep + tag off by default, cost noted), cluster cards sorted by verdict,
  inline SVG timeline, evidence for/against, innocent explanations, account table, JSON download.
- Sample: `scripts/make_swarm_sample.py` (seeded) -> `web/static/swarm_sample.csv` (264 posts, 127 accounts):
  copy-paste campaign (3 message clusters, T+S+A+M, likely), template campaign (T+M, likely), headline sharing
  (T+S, downgraded to worth watching), generic phrase (organic), event-hashtag cheers (organic). ~130 ms.
- Tests: 496 passed (40 swarm tests incl. 2000 posts < 20 s, every signal + innocent pattern, API, rate limit).

## Next
- M5 Part B: benchmark generator + run_swarm_eval.py + /metrics swarm section (announce calls/credits first).
- Later: "Report a wrong label" (`POST /api/feedback`, 30/h) and "Share result" (`/r/{id}`).
- Sunday morning: `run_eval.py --final` (announce calls).

## Known issues / limits
- Only exaggeration_minimisation is shown by default (measured precision >= 0.70 with >= 5 predictions); the rest
  are experimental (UI offers "Show experimental labels").
- Swarm: headline sharing within an hour also fires the timing signal, so it is downgraded to "worth watching",
  not "looks organic" (spec: organic_looking only without S/A). Clusters are per message: one campaign posting
  three different messages shows as three clusters, linked by "the same accounts also posted ...".
- Rate limits are in memory: they reset on every deploy.
- Genre/language detection are simple rules; Finnish may be detected as Estonian.
- The model tends to quote whole sentences (inflates flagged_share); prompt tuning planned for M4.
- Receipts: .ee has no RDAP; crt.sh is often overloaded; some outlets (AP, Reuters) block fetching -> paste text.
- Lookalike can flag dictionary words one letter away from an outlet name (e.g. lemonade vs lemonde).
- Rate limit is per IP: at the venue many people may share one IP (30 analyses/h). Open question to Aleksandr.

## How to verify (PC, Git Bash, in the repo folder)
```bash
.venv/Scripts/python -m pip install -r backend/requirements-dev.txt   # if requirements changed
.venv/Scripts/python -m pytest -q                                     # expect: 420 passed
GIT_COMMIT=test1234 PYTHONPATH=backend .venv/Scripts/python -m uvicorn app.main:app --port 8000
# browser: http://127.0.0.1:8000 (without a key: rules-only mode + banner)
```
Server (after deploy):
```bash
curl -s https://stoprop.tech/api/health        # budget.tokens_today, llm.configured, storage
cd /docker/stoprop && docker compose exec stoprop python scripts/tagger_smoke.py   # ONE real call
```
(On Mac/Linux use `.venv/bin/python` instead of `.venv/Scripts/python`.)
