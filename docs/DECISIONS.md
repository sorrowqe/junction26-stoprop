# Decisions

One line per decision (date, milestone, what, why).

- 2026-11-14 M0: Runtime deps in `backend/requirements.txt`; test-only deps (pytest, httpx) in `backend/requirements-dev.txt` so they stay out of the image.
- 2026-11-14 M0: Pinned to current releases (fastapi 0.162.7, uvicorn 0.54.0, openai 3.51.0, PyYAML 6.0.3); verified locally on Python 3.14, container runs 3.12.
- 2026-11-14 M0: Container user `stoprop` has fixed UID 10001; host `/docker/stoprop/data` must be `chown 10001:10001` so the app can write SQLite there (M2).
- 2026-11-14 M0: App code in the image is root-owned (read-only for the app user); only `/app/data` is writable.
- 2026-11-14 M0: `PYTHONPATH=/app/backend`, uvicorn target `app.main:app`; locally `pytest.ini` sets `pythonpath = backend`.
- 2026-11-14 M0: `/api/health` `commit` falls back to `"dev"` when `GIT_COMMIT` is unset (local runs); Dockerfile ARG default is `unknown`.
- 2026-11-14 M0: FastAPI auto-docs (`/docs`, `/redoc`) disabled - nothing to expose publicly.
- 2026-11-14 M0: Landing page is a single self-contained `web/index.html` (inline CSS, dark mode via prefers-color-scheme); static mount comes with M1.
- 2026-11-14 M0: `router_smoke.py` reads env only (no dotenv dependency); on the PC load `.env.local` with `set -a; source .env.local; set +a`.
- 2026-11-14 M0: `.gitignore` uses `/data/` (repo root only) instead of `data/`, otherwise `eval/data/dev|clean|symmetry` gold sets would be ignored too. `eval/data/test/` stays ignored (locked split).
- 2026-11-14 M1: Rule-based spans come from 4 patterns only (bandwagon phrases, vague "experts say", figure without a source in the same sentence, shouting capitals). They are always amber + experimental + confidence 0.3-0.45 and dropped at `strict`. Other lexical features are hints for the M2 prompt, never spans.
- 2026-11-14 M1: Experimental spans are returned by the API but hidden by default in the UI (principle 5). Every technique is `experimental: true` until M4 measures dev precision, so for now nothing is red. The UI shows "N experimental hints hidden" + a button to show them.
- 2026-11-14 M1: Profile `counts` / `density_per_1000` / `flagged_share` only count spans shown by default (non-experimental) that are not reported speech; "flagged" = red or amber. Extra profile fields: `genre`, `experimental {count, counts}`, `reported_speech`.
- 2026-11-14 M1: Paragraphs = blank-line separated blocks; if a text has no blank lines, each line is a paragraph (pasted articles often use single newlines).
- 2026-11-14 M1: Validator matches inside the given sentence only (no fallback to neighbouring sentences); normalizes NFKC, whitespace, quote marks, dashes, zero-width chars, ё->е; case-sensitive first, then case-insensitive fallback; repeated phrase -> first occurrence (or `occurrence=n`).
- 2026-11-14 M1: Reported speech = span inside a quote region (“” „“ „” «» "") with a speech verb (any of EN/ET/RU lists) within 100 chars before/after or a colon before it; scare quotes after "so-called / nn / так называемый" are NOT reported speech. Unclosed quote runs to paragraph end.
- 2026-11-14 M1: API offsets are Unicode code points (Python str indices); web/app.js converts them to UTF-16 indices for JS. The extension (M5) must do the same.
- 2026-11-14 M1: Lexicon language is detected per paragraph (fallback: whole-text language), so mixed EN/RU/ET texts work.
- 2026-11-14 M1: Added `GET /api/techniques` (taxonomy for legend/tooltips), `app/taxonomy.py` (load + gate), `app/analyze.py` (pipeline orchestration), `app/pipeline/lexicons.py`. Request size cap 100,000 chars (413), then word truncation at 8,000 (prefix cut, so offsets stay valid).
- 2026-11-14 M1: Sample texts live in `web/static/samples.json` (fictional town "Harbourton") so tests can assert the neutral sample stays clean.
