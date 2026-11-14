# Progress

_Last updated: 2026-11-14 (M1 code done, ready to deploy)_

## Done
- **M0 walking skeleton**: repo layout, Docker/compose/Traefik, `deploy.sh` + `scripts/server-deploy.sh`,
  `/api/health`, router smoke script. Server set up by Aleksandr per KICKOFF.md.
- **M1 analyzer without AI** (mode `heuristics_only`):
  - `pipeline/segment.py`: paragraphs -> sentences (ids `p{n}s{m}`, code-point offsets into the
    original text), abbreviation-aware splitting (EN/ET/RU), language detect en/et/ru/other.
  - `pipeline/lexicons.py` + `config/lexicons/{en,et,ru}.txt`: small word lists (sections documented in
    `config/lexicons/README.md`).
  - `pipeline/heuristics.py`: hints (emotive, intensifier, absolutist, us_them, all_caps, exclamation,
    link, attribution, number_attributed/unattributed, quotation, bandwagon_phrase, vague_authority,
    balance_marker, opinion_marker) + 4 conservative span rules (amber, experimental, low confidence).
  - `pipeline/validate.py`: verbatim span validation within the sentence, offsets by our code,
    reported-speech detection, `validator_rejections` count.
  - `pipeline/profile.py`: genre guess, words, flagged_share, density_per_1000, counts, neutral summary.
  - `taxonomy.py` (techniques.yaml v1 with all 13 labels + gating), `analyze.py` (orchestration).
  - API: `POST /api/analyze` (schema per CLAUDE.md §4 + `source`, `sentence_id`, `stats`),
    `GET /api/techniques`.
  - Web: analyzer on `/` with 3 samples, URL field disabled, sensitivity, "Show experimental labels",
    highlights (colour + underline style + icon), tooltip (hover / click / keyboard Enter, Esc closes),
    findings list, profile card, legend, banner for rules-only mode. Dark mode, phone layout.
  - Tests: 103 passed (`segment`, `validate`, `heuristics`, `profile`, `analyze`, `health`).
    The neutral sample is a test: it must produce zero red/amber spans.

## In progress
- M1 deploy + checklist (KICKOFF.md).

## Next
- M2: LLM tagger via AI Router, per-paragraph cache, single-flight, budget guard, rate limit.
  Hints from `extract_hints` go into the user message; LLM spans go through `validate_candidates`
  and `taxonomy.gate` exactly like the heuristic ones.
- "Report a wrong label" button (§5) needs storage -> do it with the SQLite store in M2.

## Known issues / limits
- Everything is experimental (hidden by default) until M4 measures precision. To see highlights,
  tick "Show experimental labels" (or click the button under "Findings").
- Genre guess and language detection are simple rules; Finnish may be detected as Estonian.
- Validator is strict: a quote spanning two sentences is rejected (by design).

## How to verify (PC, Git Bash, in the repo folder)
```bash
.venv/Scripts/python -m pip install -r backend/requirements-dev.txt   # if requirements changed
.venv/Scripts/python -m pytest -q                                     # expect: 103 passed
GIT_COMMIT=test1234 PYTHONPATH=backend .venv/Scripts/python -m uvicorn app.main:app --port 8000
# browser: http://127.0.0.1:8000  -> click a sample
# curl -s -X POST http://127.0.0.1:8000/api/analyze -H 'Content-Type: application/json' \
#      -d '{"text":"Everyone knows this plan failed."}'
```
(On Mac/Linux use `.venv/bin/python` instead of `.venv/Scripts/python`.)
