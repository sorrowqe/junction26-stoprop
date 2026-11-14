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
