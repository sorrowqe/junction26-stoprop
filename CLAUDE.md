# Stoprop — project brief for Claude Code

> **Stoprop: see the spin before it sees you. We flag the tactics, not the opinions.**

Stoprop is a browser extension + web app that highlights manipulation techniques in text
(news, posts, comments), explains each one in plain language, and shows provenance
"receipts" for the source. Built for a hackathon (Hostinger challenge: detect propaganda /
bad-actor behaviour). **Final submission: Sunday 15 Nov 2026, 10:00 Europe/Tallinn.**

Judges care most about **the strength and honesty of the detection logic, including false
positives**, and want **measured precision / false-positive rate**, not a happy-path demo.
The product must run live on Hostinger (our KVM 2 VPS) with a public URL.

---

## 0. Working agreement (read first)

- The project owner (Aleksandr) is a sysadmin/security person who **does not write or review
  code**. He can run commands. So:
  - Work in **small, verifiable steps** (one milestone at a time, see §9).
  - After each step, tell him **exactly which commands to run** and **what output to expect**.
  - Prefer boring, simple tech. No frameworks unless clearly needed.
  - Write tests for the detection logic and run them yourself.
- **Never commit secrets.** All keys live in `.env` (gitignored). Provide `.env.example`.
- **Never run destructive commands on the VPS** (rm -rf, docker system prune, compose down of
  other projects, touching the existing `traefik` or `hermes-agent` containers) without asking.
- **LLM credits are limited** (one shared Hostinger AI Router balance for the whole team).
  Do not run bulk LLM calls (evals, bake-offs) without saying how many calls it will make.
  Use small samples while developing.
- When something is ambiguous, pick the simplest reasonable option and write it down in
  `docs/DECISIONS.md` (one line per decision).
- **We develop on Aleksandr's PC (likely Windows + Git Bash)** and deploy through GitHub (§8).
  The venue network is unreliable: never design anything that needs a long-lived SSH session.
  For local tests use a Python venv in `.venv` (gitignored). Commands you give him must work
  in Git Bash.
- **Git: commit after every working step** with a clear message (`M1: verbatim validator + tests`).
  This is our undo button. Push `main` to GitHub often (backup). Branch `live` = production.
- **Line endings:** `.gitattributes` must force LF for `*.sh`, `Dockerfile`, `*.py`, `*.yml`,
  `*.yaml` (Windows CRLF breaks bash on the server). Shell scripts are invoked as `bash script.sh`
  so the executable bit doesn't matter.
- **Keep `docs/PROGRESS.md` current**: done / in progress / next / known issues / how to verify.
  Update it at the end of every milestone and before any long pause. A fresh Claude Code
  session must be able to continue from CLAUDE.md + PROGRESS.md alone.
- **Deploying = pushing to the `live` branch** (`bash deploy.sh`). That changes what judges see:
  only when tests pass, and only after telling Aleksandr. Never `git push --force` to `live`.
- **Never SSH to the server or run commands there yourself.** Print the exact commands for
  Aleksandr instead (he runs them inside `tmux` because SSH drops).
- **Never read or print `.env` / `.env.local` contents** (they hold router keys). Use
  `.env.example` for documentation. To check config, check whether a variable is *set*, never its value.

---

## 1. Principles of the detection logic (non-negotiable)

1. **Detect how something is said, not what position it takes.** We label techniques
   ("this sentence uses a fear appeal"), never authors or outlets ("propagandist").
   Prompts must say this explicitly.
2. **The LLM proposes, deterministic code verifies.** Every span returned by a model must be
   found **verbatim** in the source text, or it is dropped. Character offsets are computed by
   our code, never trusted from the model.
3. **Reported speech is not the author's manipulation.** A loaded phrase inside a quotation
   attributed to someone else is labelled `reported_speech` and does not count against the
   source.
4. **Abstain instead of guessing.** "Nothing found" and "not checked" are valid outputs and
   must be shown honestly in the UI.
5. **Only show what we can measure.** Each technique has a measured dev-set precision. A
   technique is shown by default only if `dev_precision >= 0.70`; otherwise it is marked
   `experimental` and hidden by default (user can enable).
6. **No single "propaganda score".** Show a profile: genre, techniques found, density per
   1000 words, receipts summary, share of text flagged.
7. **Colour labels are descriptive, never truth claims.**
   - `red` = manipulation technique (high-precision techniques)
   - `amber` = unsupported claim (check-worthy factual claim with **no source given in the text**;
     NOT "false") or lower-confidence technique
   - `green` = claim with verifiable support in the text (link, named source, dated data);
     NOT "true"/"verified"
   - `blue` = balance marker (fair counter-argument, acknowledged uncertainty)
   - most text stays uncoloured. Never rely on colour alone (underline style + icon too).
8. **Privacy by default.** `/api/analyze` is stateless: no user identity stored with text.
   Reading history lives only in the extension (IndexedDB). Political reading history is
   GDPR special-category data — never send it to the server.

---

## 2. Architecture

```
Chrome extension (MV3)                        Web app (served by backend)
- right-click "Analyze with Stoprop"          - /            landing + paste text/URL analyzer
- toolbar: "Analyze this page" (Readability)  - /r/{id}      shareable result page
- highlights via CSS Custom Highlight API     - /methodology how detection works + limits
- side panel: explanations + receipts         - /metrics     live eval results
- local themes, history, dashboard page       - /extension   download zip + install steps
            │                                              │
            └──────────────► FastAPI backend ◄─────────────┘
                             - /api/analyze   text -> spans + profile
                             - /api/receipts  domain/url -> provenance receipts
                             - /api/metrics   latest eval results JSON
                             - /api/health
                             - SQLite cache (content hash), rate limit, budget guard
                             - Hostinger AI Router key ONLY here (never in extension)
```

Deployment: one Docker container (`stoprop`) behind the **existing Traefik** on the VPS.

---

## 3. Repository layout

```
stoprop/
  CLAUDE.md                 this file
  README.md                 what it is, how to run, how to deploy
  .env.example
  docker-compose.yml        stoprop service + Traefik labels
  Dockerfile
  deploy.sh                 git pull && docker compose up -d --build && health check
  backend/
    app/
      main.py               FastAPI app, routes, static files
      config.py             env vars, settings
      pipeline/
        extract.py          URL fetch (SSRF-safe) + main-text extraction
        segment.py          paragraphs, sentences (with ids), language detect
        heuristics.py       free lexical features (hints, not verdicts)
        tagger.py           LLM technique/claim tagging via AI Router
        validate.py         verbatim span validation, offsets, reported-speech check
        profile.py          genre, density, coverage, summary
      receipts/
        rdap.py  wayback.py  crtsh.py  lookalike.py  factcheck.py  syndication.py
      llm/
        client.py           OpenAI-compatible client, retries, usage + cost tracking
        budget.py           daily credit/token cap -> heuristics-only fallback
      store.py              SQLite: cache, results, usage log
      ratelimit.py
    prompts/
      tagger_system.md      static taxonomy + rules (put FIRST for prompt caching)
    config/
      techniques.yaml       taxonomy, colours, thresholds, dev_precision, enabled
      outlets.yaml          known outlets for lookalike detection
    tests/
  web/                      static HTML/CSS/vanilla JS (no build step)
  extension/                MV3 extension (no build step), vendored Readability.js
  eval/
    data/dev/*.jsonl        gold set for tuning
    data/test/*.jsonl       LOCKED gold set, only used with --final
    data/clean/*.jsonl      neutral texts (wire news, encyclopedic) for FPR
    data/symmetry/*.jsonl   mirrored pairs for neutrality test
    sheet_to_jsonl.py       convert annotation sheet (CSV: id,lang,text,quote,technique) to JSONL
    run_eval.py             metrics -> eval/results/latest.json
    results/
  docs/
    DECISIONS.md
    METHODOLOGY.md
```

---

## 4. Backend spec

### Env vars (`.env.example`)
```
DOMAIN=stoprop.tech                     # primary; www.stoprop.tech and stoprop.srv2159153.hstgr.cloud also routed
ROUTER_BASE_URL=https://router.hostinger.com/v1   # Hostinger AI Router, OpenAI-compatible
ROUTER_API_KEY=         # sk-hstgr-... use a separate key for prod (with a daily limit) and for dev/eval
MODEL_TAGGER=claude-haiku-4.5-eu        # default until the bake-off decides
MODEL_SUMMARY=claude-haiku-4.5-eu
BAKEOFF_MODELS=claude-haiku-4.5-eu,gemini-3.5-flash-lite-eu,gpt-5.4-mini,mistral-small-4-eu
# Rules: prefer -eu variants; never use reasoning models (gpt-5*, *-reasoning) in the tagger;
# expensive models (claude-fable-5.2, claude-opus-5.5) only for rare offline adjudication.
DAILY_TOKEN_CAP=2000000
PUBLIC_BASE_URL=https://<domain>
EXTENSION_ORIGINS=chrome-extension://<id>   # comma separated, for CORS
FACTCHECK_API_KEY=      # optional, Google Fact Check Tools
SEARCH_PROVIDER=none    # none | oxylabs | exa  (for syndication receipts)
SEARCH_API_KEY=
```

### `POST /api/analyze`
Request: `{ "text": str | null, "url": str | null, "lang": "auto", "sensitivity": "strict|balanced|broad" }`
- If `url`: fetch server-side **SSRF-safe**: only http/https, resolve DNS and block private,
  loopback, link-local, CGNAT and metadata ranges (incl. IPv6), max 2 MB, 8 s timeout,
  max 3 redirects (re-check each hop). Extract main text with readability-lxml (or trafilatura).
- Max input ~8000 words (truncate with a visible note) to protect the budget.
- Cache key: sha256(normalized text) + taxonomy version + model. Cache hit = no LLM call.

Response:
```json
{
  "id": "r_abc123",
  "lang": "en",
  "genre": "news|opinion|social|unknown",
  "spans": [
    {"start": 120, "end": 168, "quote": "...", "label": "fear_appeal", "color": "red",
     "confidence": 0.82, "explanation": "plain-language why", "reported_speech": false,
     "experimental": false}
  ],
  "profile": {"words": 812, "flagged_share": 0.06, "density_per_1000": 4.9,
              "counts": {"fear_appeal": 2}, "summary": "one or two neutral sentences"},
  "mode": "llm|heuristics_only",
  "notes": ["text truncated to 8000 words"],
  "cost": {"input_tokens": 0, "output_tokens": 0, "cached": true}
}
```

### Pipeline
1. **segment**: paragraphs -> sentences, each with an id `p3s2`. Detect language.
2. **heuristics** (free): intensifiers, absolutist words, us-vs-them pronouns, ALL CAPS,
   exclamation density, numbers without attribution, presence of links/named sources,
   quotation detection. Output = hints passed into the prompt. They are **not** verdicts.
3. **tagger** (LLM): system prompt = static taxonomy + rules (cache-friendly, first);
   user content = numbered sentences + hints. Model returns JSON:
   `[{ "sentence_id": "p3s2", "quote": "exact words", "label": "...", "confidence": 0-1,
       "explanation": "..." }]` or `[]`. Must allow "nothing found".
   Chunk long texts by paragraphs (~1200 words per call). Robust JSON parsing, one retry,
   then fail soft (heuristics-only for that chunk).
4. **validate**: find `quote` verbatim (whitespace-normalized) inside that sentence ->
   compute offsets ourselves. Drop if not found and count it (`validator_rejections`).
   Mark `reported_speech` if the span is inside quotation marks attributed to a third party.
5. **gate**: apply `techniques.yaml` (enabled, min_confidence per sensitivity, experimental).
6. **profile**: genre, coverage, density, counts, neutral summary (template, no extra LLM call).

### Taxonomy (`config/techniques.yaml`), v1 — keep it to these:
Techniques (red unless experimental/low confidence):
- `loaded_language` — emotionally charged words used to sway rather than inform
- `name_calling` — labelling a person/group to discredit instead of arguing
- `fear_appeal` — invoking threat/danger to push a conclusion or action
- `exaggeration_minimisation` — overstating or downplaying beyond what evidence supports
- `whataboutism` — deflecting criticism by pointing to someone else's wrongdoing
- `false_dilemma` — presenting only two options when more exist
- `questionable_authority` — appeal to vague/unnamed/irrelevant/fabricated authority
- `causal_oversimplification` — single cause for a complex outcome
- `bandwagon` — "everyone knows / everyone agrees" as the argument
- `doubt_casting` — undermining credibility without evidence
Claim labels:
- `unsupported_claim` (amber) — check-worthy factual claim with no source in the text
- `supported_claim` (green) — factual claim with a link/named source/dated data in the text
- `balance` (blue) — fair counter-argument or acknowledged uncertainty
Each entry: `{label, color, definition, example, enabled, min_confidence: {strict, balanced, broad}, dev_precision: null, experimental: true}`.
`run_eval.py` writes `dev_precision`; `experimental` becomes false when `dev_precision >= 0.70`.

### Caching & freshness ("our memory is a database, not a chat history")
- **Technique analysis never goes stale.** Cache per *paragraph*, key =
  `sha256(normalize(paragraph)) + model + prompt_version + taxonomy_version`, no TTL.
  `normalize` = Unicode NFKC, strip zero-width chars (U+200B–U+200D, U+2060, U+FEFF),
  collapse whitespace. Keep original text for offsets. Also store `seen_count` and
  `distinct_domains` per paragraph hash (free swarm signal: "seen on N pages").
- **Receipts have per-type TTLs** (soft TTL = serve + refresh in background, hard TTL = refresh before serving):
  lookalike: until outlets.yaml changes · rdap: 7 d · first archive / first cert: 7 d ·
  fact_checks: 12 h · syndication/corroboration: 2 h if content < 48 h old, else 24 h.
- **Single-flight**: concurrent requests for the same uncached key wait for one in-flight
  computation (asyncio lock/future per key) — never duplicate LLM or search calls.
- **Negative caching**: errors/not-found cached 10 min.
- **Budget-aware TTLs**: when daily usage > 70% of cap, multiply all soft TTLs by 2; at 100% -> heuristics-only.
- **User-visible freshness**: every receipt shows `checked_at`; "Refresh" allowed at most once
  per 15 min per item globally.
- **Themes are computed per theme, not per user** (stretch): one scheduled job per theme
  every few hours, shared by all users watching it.
- **Private mode**: requests flagged `private=true` (or without a public URL) are analyzed but
  NOT written to the shared cache.
- **Semantic claim memory** (SHOULD, after M4): embed extracted claims with
  `text-embedding-3-small-eu`; near-duplicates may reuse *search results* and be grouped into a
  narrative, but never reuse a verdict (negations embed closely).

### Budget guard & rate limit
- Log usage (tokens from API `usage` field) per call in SQLite. If daily cap is hit ->
  `mode: heuristics_only` + UI banner. Expose `/api/health` with budget status.
- Rate limit `/api/analyze`: 30/hour per IP (+ per install token from extension).

### `POST /api/receipts`
Request `{ "url": str }` or `{ "domain": str }`. Each receipt:
`{ "type": "...", "status": "found|not_found|not_checked|error", "text": "plain English",
   "evidence_url": "...", "checked_at": "ISO" }`. "not checked" must be shown, not hidden.
Receipts (all cached 24 h, 8 s timeouts, run concurrently):
- `domain_age` — RDAP via `https://rdap.org/domain/{domain}` (registration date)
- `first_seen_archive` — Wayback CDX first capture
- `first_certificate` — crt.sh JSON (slow; tolerate timeouts)
- `lookalike` — compare registrable domain to `outlets.yaml` (edit distance, homoglyphs,
  TLD swaps, hyphen/word insertion like `err-news.ee` vs `err.ee`)
- `fact_checks` — Google Fact Check Tools API (only if key set)
- `syndication` — exact-phrase search of 1–2 distinctive sentences (only if SEARCH_PROVIDER set).
  Distinguish wire syndication (AP/Reuters/BNS/ERR attribution) from copy networks.

---

## 5. Web app spec (`web/`, served by FastAPI)

- `/` — hero with the punchline, textarea + URL field, "Analyze" button, 3 sample texts
  (one manipulative, one neutral wire-style, one opinion piece) so judges can try instantly.
- Result view: text with highlights (colour + underline style + icon), legend, hover/click
  tooltip (technique name, definition, why this span, confidence, "reported speech" badge),
  profile card, receipts panel (if URL), "Report a wrong label" button (stores feedback).
- `/methodology` — renders `docs/METHODOLOGY.md`: pipeline, principles, known limits.
- `/metrics` — renders `eval/results/latest.json`: per-technique P/R/F1, clean-text FPR,
  flags per 1000 words on clean text, symmetry flip rate, validator rejection rate,
  cost & latency per analysis, model bake-off table, git commit + date of the run.
- `/extension` — download zip + 4-step "load unpacked" instructions.
- Plain HTML/CSS/vanilla JS, accessible, works in dark mode. No build step.

---

## 6. Extension spec (`extension/`, Manifest V3, Chrome)

- Permissions: `activeTab`, `contextMenus`, `sidePanel`, `storage`, `scripting`.
  Host permission only for our API domain. No `<all_urls>` unless auto-analyze is on.
- Context menu on selection: "Analyze with Stoprop".
- Toolbar action: "Analyze this page" -> content script runs vendored Readability, sends text
  (+ domain only if the user allows it, for receipts).
- Highlights with **CSS Custom Highlight API** (no DOM mutation); map offsets back to text nodes.
- Side panel: list of findings, explanations, profile, receipts.
- Options page: API base URL, sensitivity, enabled labels, **themes** (name + keywords, ET/RU/EN),
  auto-analyze on theme match (off by default; max N per hour), "send domain for receipts".
- Storage: settings in `chrome.storage.local` (NOT sync). History in IndexedDB:
  `{ts, domain, content_hash, themes_matched, counts}` — no full text, no full URL by default.
- Dashboard page (`dashboard.html`, extension page, local-only): techniques over time,
  themes encountered, domains with repeated bad receipts, recurring identical paragraphs.
- Sends a random install token header for rate limiting (not tied to identity).

---

## 7. Evaluation spec (`eval/`)

- Gold format (JSONL): `{id, lang, genre, text, spans:[{start,end,label}]}`.
  Team annotates in a shared sheet (`id, lang, text, quote, label`); `sheet_to_jsonl.py`
  converts quotes to offsets and reports quotes it cannot find.
- Splits: `dev` for tuning thresholds; `test` is **locked** — only `run_eval.py --final`.
- Metrics (`run_eval.py`):
  - span-level P/R/F1 per label; match = same label and overlap >= 50% of the shorter span
  - **clean-text FPR**: share of clean paragraphs with >= 1 red/amber span; flags per 1000 words
  - **symmetry flip rate**: pairs with sides/entities swapped -> share where label sets differ
  - validator rejection rate, abstention rate, mean cost (tokens) & latency per analysis
  - inter-annotator agreement (Cohen's kappa) if two annotators labelled the same items
  - `--models a,b,c` bake-off: same dev sample through several models -> F1 vs cost table
  - **print the number of LLM calls before running and ask for confirmation**
- Output `eval/results/latest.json` with git commit hash + timestamp; update
  `dev_precision` in `techniques.yaml`.

---

## 8. Deployment spec

- VPS: Hostinger KVM 2 (2 vCPU, 8 GB). Existing containers: `traefik-traefik-1` (reverse
  proxy, handles HTTPS) and `hermes-agent-...` — **do not modify them**.
- Project lives in `/docker/stoprop` on the VPS (Hostinger Docker Manager lists `/docker/*`).
- **Existing Traefik (verified)**: runs with `network_mode: host`, docker provider with
  `exposedbydefault=false`, entrypoints `web` (:80, redirects to https) and `websecure` (:443),
  certresolver `letsencrypt` (HTTP challenge). Because Traefik is in the host network namespace
  it reaches containers on their own bridge network directly — **no shared network needed and
  no published ports**. Use exactly this compose file:
  ```yaml
  services:
    stoprop:
      build:
        context: .
        args:
          - GIT_COMMIT          # taken from the environment of server-deploy.sh
      restart: unless-stopped
      env_file: .env
      volumes:
        - ./data:/app/data
      expose:
        - "8000"
      labels:
        - traefik.enable=true
        - traefik.http.routers.stoprop.rule=Host(`stoprop.tech`) || Host(`www.stoprop.tech`) || Host(`stoprop.srv2159153.hstgr.cloud`)
        - traefik.http.routers.stoprop.entrypoints=websecure
        - traefik.http.routers.stoprop.tls.certresolver=letsencrypt
        - traefik.http.services.stoprop.loadbalancer.server.port=8000
      healthcheck:
        test: ["CMD", "python", "-c", "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/health')"]
        interval: 30s
        timeout: 5s
        retries: 3
  ```
  (`${DOMAIN}` is interpolated by docker compose from the `.env` file in the project dir.)
- Run uvicorn with `--host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'`
  so rate limiting sees the real client IP from Traefik's `X-Forwarded-For` (safe because the
  container port is not published; only Traefik/host can reach it).
- **Never add a `ports:` section** (that would expose the app on a raw public port).
- **Deploy flow (pull-based, survives a bad network):**
  PC: develop → test → commit → `bash deploy.sh` (pushes `main` and `main:live`, then polls
  `https://stoprop.tech/api/health` until `commit` equals the local short hash).
  VPS: cron runs `scripts/server-deploy.sh` every minute; if `origin/live` changed it pulls,
  rebuilds, health-checks, and **rolls back automatically** if the new build is unhealthy.
- `/api/health` must return `"commit"` = `GIT_COMMIT` env baked in at build time
  (compose `build.args: [GIT_COMMIT]`, Dockerfile `ARG GIT_COMMIT` + `ENV GIT_COMMIT=$GIT_COMMIT`).
  Always use 8-char short hashes: `git rev-parse --short=8 HEAD`.
- `scripts/server-deploy.sh` (runs ON the VPS, from cron, as root):
  ```bash
  #!/usr/bin/env bash
  set -uo pipefail
  exec 9>/tmp/stoprop-deploy.lock; flock -n 9 || exit 0          # one deploy at a time
  cd /docker/stoprop
  git fetch -q origin live || exit 0                              # network hiccup: try next minute
  NEW=$(git rev-parse origin/live); CUR=$(git rev-parse HEAD)
  if [ "$NEW" = "$CUR" ] && [ "${1:-}" != "--force" ]; then exit 0; fi
  echo "$(date -Is) deploying ${NEW:0:8} (was ${CUR:0:8})"
  build() { export GIT_COMMIT=$(git rev-parse --short=8 HEAD); docker compose up -d --build; }
  healthy() { for i in $(seq 1 12); do
      docker compose exec -T stoprop python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=3)" >/dev/null 2>&1 && return 0
      sleep 5; done; return 1; }
  git reset -q --hard "$NEW"        # tracked files only; .env and data/ are untracked + gitignored
  if build && healthy; then echo "$(date -Is) OK ${NEW:0:8}"
  else echo "$(date -Is) FAILED ${NEW:0:8} -> rolling back to ${CUR:0:8}"
       git reset -q --hard "$CUR"; build; fi
  ```
  **Never use `git clean`** on the server (it would delete `.env` and `data/`).
- `deploy.sh` (runs on the PC, Git Bash compatible, no python dependency):
  ```bash
  #!/usr/bin/env bash
  set -euo pipefail
  git diff --quiet && git diff --cached --quiet || { echo "Uncommitted changes - commit first"; exit 1; }
  for i in 1 2 3 4 5; do
    git push origin main && git push origin main:live && break
    echo "Push failed (network?), retry $i/5 in 10s"; sleep 10
  done
  WANT=$(git rev-parse --short=8 HEAD); echo "Waiting for the server to go live with $WANT ..."
  for i in $(seq 1 36); do
    GOT=$(curl -fsS --max-time 10 https://stoprop.tech/api/health 2>/dev/null \
          | grep -o '"commit": *"[^"]*"' | grep -o '[0-9a-f]\{8\}' || true)
    [ "$GOT" = "$WANT" ] && { echo "LIVE: $WANT"; exit 0; }
    sleep 10
  done
  echo "Not live after 6 min. Check: ssh stoprop 'tail -40 /var/log/stoprop-deploy.log'"; exit 1
  ```
- Cron on the VPS: `* * * * * bash /docker/stoprop/scripts/server-deploy.sh >> /var/log/stoprop-deploy.log 2>&1`
- `.env` exists **only on the server** (`/docker/stoprop/.env`, `chmod 600`, created by Aleksandr).
  For local eval runs on the PC, `.env.local` (gitignored) holds the **dev** key.
- `.dockerignore` must exclude `.git`, `.env`, `.env.local`, `.venv`, `data/`, `eval/data/`,
  `__pycache__` so secrets and data never end up inside the image.

---

## 9. Milestones (do them in order, stop and report after each)

- **M0 walking skeleton**: repo, Dockerfile, compose, `/api/health` + placeholder `/` live over
  HTTPS on our domain behind Traefik. One test call to AI Router succeeds from the container.
- **M1**: segment + heuristics + validate + profile; `/api/analyze` in heuristics-only mode;
  web analyzer renders highlights and tooltips. Unit tests for validator & offsets.
- **M2**: LLM tagger via AI Router, cache, budget guard, rate limit, sensitivity gating.
- **M3**: receipts v1 (RDAP, Wayback, crt.sh, lookalike) + receipts panel; SSRF-safe URL fetch.
- **M4**: eval harness, `sheet_to_jsonl.py`, `/metrics` page, small bake-off (ask first).
- **M5**: extension MVP (context menu, analyze page, highlights, side panel).
- **M6**: themes, local history, extension dashboard page.
- **M7**: `/methodology`, README, demo samples, final eval with `--final`.
- Stretch (only if all above done): fact-check API, syndication search, Telegram digest.

Feature freeze: Sunday 08:30. Final eval + submission text by 09:30.

## 10. Non-goals
- No user accounts, no server-side reading history.
- No "is this person/outlet a propagandist" verdicts, no single overall score.
- No AI-generated-face/image analysis.
- No autonomous agent loops in the request path (cost, latency, prompt injection).