# Progress

_Last updated: 2026-11-14 (M0 code done, not yet deployed)_

## Done
- **M0 walking skeleton (code)**: repo layout per CLAUDE.md §3 (stub modules for later milestones),
  `.gitignore`, `.gitattributes` (LF), `.dockerignore`, `.env.example`, FastAPI app with
  `GET /api/health` and `GET /` (placeholder landing), Dockerfile (python:3.12-slim, non-root UID 10001,
  GIT_COMMIT baked in), `docker-compose.yml` + `deploy.sh` + `scripts/server-deploy.sh` verbatim
  from §8, `scripts/router_smoke.py`, pytest for health + landing (4 passed).

## In progress
- M0 deployment: Aleksandr sets up the server per KICKOFF.md (deploy key, clone `live` into
  `/docker/stoprop`, `.env`, `data/` ownership, first `server-deploy.sh --force`, cron).

## Next
- Finish the M0 checklist (KICKOFF.md): health over HTTPS shows the commit, router smoke test
  from the container, `ss -ltnp | grep 8000` empty, trivial change -> `bash deploy.sh` -> LIVE.
- Then M1: segment, heuristics, validate, profile, `/api/analyze` heuristics-only, web analyzer.

## Known issues
- Docker image has not been built yet (no Docker on the PC); first build happens on the server.
- Router smoke test not run yet (needs a key; run it in the container on the server).

## How to verify (PC, Git Bash, in the repo folder)
```bash
python -m venv .venv                                   # once
.venv/Scripts/python -m pip install -r backend/requirements-dev.txt
.venv/Scripts/python -m pytest -q                      # expect: 4 passed
GIT_COMMIT=test1234 PYTHONPATH=backend .venv/Scripts/python -m uvicorn app.main:app --port 8000
# other terminal: curl -s http://127.0.0.1:8000/api/health
# -> {"status":"ok","version":"0.1.0","commit":"test1234","budget":{"mode":"ok"}}
```
(On Mac/Linux use `.venv/bin/python` instead of `.venv/Scripts/python`.)
