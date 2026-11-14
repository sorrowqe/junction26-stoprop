# Stoprop — project brief for Claude Code (v2: web service only)

> **Stoprop: see the spin before it sees you. We flag the tactics, not the opinions.**

Stoprop is a **web service** (web app + public API) that detects signs of propaganda and
coordinated manipulation online, three ways:

1. **Analyze** a text or URL → highlights manipulation techniques span by span and explains
   each one in plain language (challenge direction *"Name the technique"*).
2. **Check the source** → plain-English provenance "receipts" for a URL/domain: domain age,
   lookalike/clone domains, first seen, fact-checks (*"Trust the source"*).
3. **Swarm mode** → upload a batch of posts (CSV/JSON with author + timestamp + text) →
   detects coordinated inauthentic behaviour from text reuse, timing synchrony, account
   similarity and amplification patterns — never from a single post (*"Spot the swarm"*).

Built for Junction 2026, Hostinger challenge. **Final submission: Sunday 15 Nov 2026, 10:00
Europe/Tallinn.** Judges care most about **the strength and honesty of the detection logic,
including false positives**, and want **measured precision / false-positive rate**, not a
happy-path demo. Must run live on Hostinger (our KVM 2 VPS) with a public URL: https://stoprop.tech

> **Change log v2:** the browser extension is DROPPED (do not build it; remove any extension
> scaffolding, `EXTENSION_ORIGINS`, install-token logic if present). Swarm mode is NEW (M5).
> **M0 and M1 are DONE.** Before starting M2, read `docs/PROGRESS.md`, reconcile the existing
> code with this v2 spec, and list any mismatches you fix.

---

## 0. Working agreement (read first)

- The project owner (Aleksandr) is a sysadmin/security person who **does not write or review
  code**. He can run commands. So:
  - Work in **small, verifiable steps** (one milestone at a time, see §9).
  - After each step, tell him **exactly which commands to run** and **what output to expect**.
  - Prefer boring, simple tech. No frameworks unless clearly needed.
  - Write tests for the detection logic and run them yourself.
- **Never commit secrets.** All keys live in `.env` (gitignored). Provide `.env.example`.
- **LLM credits are limited** (one shared Hostinger AI Router balance for the whole team).
  Never run bulk LLM calls (evals, bake-offs, benchmark generation) without first printing how
  many calls it will make and waiting for OK. Use small samples while developing.
- When something is ambiguous, pick the simplest reasonable option and write it down in
  `docs/DECISIONS.md` (one line per decision).
- **We develop on Aleksandr's PC (Windows + Git Bash)** and deploy through GitHub (§8).
  The venue network is unreliable: never design anything that needs a long-lived SSH session.
  Local tests use a Python venv in `.venv` (gitignored). Commands you give him must work in Git Bash.
- **Git: commit after every working step** with a clear message. Push `main` often (backup).
  Branch `live` = production.
- **Line endings:** `.gitattributes` forces LF for `*.sh`, `Dockerfile`, `*.py`, `*.yml`, `*.yaml`.
  Shell scripts are invoked as `bash script.sh`.
- **Keep `docs/PROGRESS.md` current**: done / in progress / next / known issues / how to verify.
  A fresh session must be able to continue from CLAUDE.md + PROGRESS.md alone.
- **Deploying = `bash deploy.sh`** (pushes `main:live`). Only when tests pass and after telling
  Aleksandr. Never `git push --force` to `live`.
- **Never SSH to the server or run commands there yourself.** Print exact commands for Aleksandr
  (he runs them inside `tmux`).
- **Never read or print `.env` / `.env.local` contents.** Check only whether a variable is set.

---

## 1. Principles of the detection logic (non-negotiable)

1. **Detect how something is said, not what position it takes.** We label techniques
   ("this sentence uses a fear appeal") and behaviours ("these accounts posted near-identical
   text within 6 minutes"), never people, outlets or viewpoints ("propagandist", "bot").
   Prompts and UI copy must follow this.
2. **The LLM proposes, deterministic code verifies.** Every span returned by a model must be
   found **verbatim** in the source text or it is dropped. Offsets are computed by our code.
3. **Reported speech is not the author's manipulation.** Loaded language attributed to someone
   else is labelled `reported_speech` and does not count against the text. This covers direct
   quotes AND attributed indirect speech ("X said that…", "X called it…", "according to X…",
   "…, the minister said"), but ONLY when attributed to a specific, identifiable source (named
   person, organisation or outlet; "the minister"; he/she). Not reported speech: first-person
   attribution ("I said that…") and vague/unnamed sources ("experts say", "insiders claim",
   "many believe", "supporters argued") — that vagueness is the technique.
4. **Abstain instead of guessing.** "Nothing found", "insufficient data" and "not checked" are
   valid outputs and must be shown honestly.
5. **Two independent signals before a strong verdict.** A swarm cluster is "likely coordinated"
   only with text-reuse evidence **plus** at least one independent family (timing or account
   similarity). One family alone = "worth watching".
6. **Only show what we can measure.** A technique is shown by default only if its measured
   `dev_precision >= 0.70`; otherwise `experimental` and hidden by default.
7. **No single "propaganda score".** Show profiles and evidence, not one number.
8. **Colour labels are descriptive, never truth claims.**
   `red` = manipulation technique · `amber` = unsupported claim (no source given in the text;
   NOT "false") or lower-confidence technique · `green` = claim with verifiable support in the
   text (NOT "true") · `blue` = balance marker. Most text stays uncoloured. Never colour alone
   (underline style + icon too).
9. **Show the innocent explanations.** Every flagged result lists plausible non-malicious
   explanations (e.g. "many people share the same news headline", "quoted official statement").
10. **Privacy by default.** No accounts. Analyses are stateless; pasted text is cached only as
    described in §4; swarm uploads are processed in memory and never stored unless the user
    explicitly clicks "Share result".

---

## 2. Architecture

```
Browser (judges/users)
  /            Analyze text or URL (highlights + explanations + receipts if URL)
  /source      Check a source (receipts only, no LLM cost)
  /swarm       Swarm mode (upload CSV/JSON or "Load sample dataset")
  /r/{id}      Shared result (only created on explicit "Share")
  /metrics     Live evaluation results (precision, recall, FPR, cost)
  /methodology How it works + known limitations
  /api         API landing (curl examples) + /docs (FastAPI OpenAPI)
        │
        ▼
FastAPI backend (one container behind the existing Traefik)
  POST /api/analyze   POST /api/receipts   POST /api/swarm   POST /api/share
  POST /api/feedback  GET /api/metrics     GET /api/health
  SQLite (cache, usage log, feedback, shares) · rate limit · budget guard
  Hostinger AI Router key lives ONLY server-side
```

---

## 3. Repository layout (target)

```
stoprop/
  CLAUDE.md  README.md  .env.example  .gitignore  .gitattributes  .dockerignore
  Dockerfile  docker-compose.yml  deploy.sh
  scripts/      server-deploy.sh  router_smoke.py
  backend/
    app/
      main.py  config.py  store.py  ratelimit.py
      pipeline/   extract.py segment.py heuristics.py tagger.py validate.py profile.py
      receipts/   rdap.py wayback.py crtsh.py lookalike.py factcheck.py syndication.py
      swarm/      ingest.py textreuse.py timing.py accounts.py amplification.py clusters.py explain.py
      llm/        client.py budget.py
    prompts/      tagger_system.md
    config/       techniques.yaml outlets.yaml lexicons/*.txt
    tests/
  web/            static HTML/CSS/vanilla JS (no build step)
  eval/
    data/raw/  data/dev/  data/test/ (LOCKED)  data/clean/  data/symmetry/
    sheet_to_jsonl.py  run_eval.py
    swarm/  make_benchmark.py  run_swarm_eval.py  benchmark/ (generated, committed, small)
    results/
  docs/  PROGRESS.md  DECISIONS.md  METHODOLOGY.md
```

---

## 4. Backend spec — Analyze & Receipts

### Env vars (`.env.example`)
```
DOMAIN=stoprop.tech
ROUTER_BASE_URL=https://router.hostinger.com/v1
ROUTER_API_KEY=                          # server: stoprop-prod key; PC .env.local: stoprop-dev key
MODEL_TAGGER=gemini-3.5-flash-lite-eu    # bake-off 2026-11-14 (same F1 as Haiku, ~1/3 credits)
MODEL_SUMMARY=claude-haiku-4.5-eu
MODEL_EMBED=text-embedding-3-small-eu    # swarm deep mode only
BAKEOFF_MODELS=claude-haiku-4.5-eu,gemini-3.5-flash-lite-eu,gpt-5.4-mini,mistral-small-4-eu
# Prefer -eu variants; never reasoning models in the tagger; claude-fable-5.2 / claude-opus-5.5
# only for rare offline adjudication.
DAILY_TOKEN_CAP=2000000
PUBLIC_BASE_URL=https://stoprop.tech
FACTCHECK_API_KEY=                       # optional, Google Fact Check Tools (AIza...)
SEARCH_PROVIDER=none                     # none | oxylabs | exa
SEARCH_API_KEY=
```

### `POST /api/analyze`
Request: `{ "text": str|null, "url": str|null, "sensitivity": "strict|balanced|broad", "private": bool }`
- `url`: SSRF-safe fetch: http/https only; resolve DNS and block private, loopback, link-local,
  CGNAT 100.64/10, multicast, 169.254.169.254 and IPv6 equivalents (::1, fc00::/7, fe80::/10,
  IPv4-mapped); re-check every redirect (max 3); 2 MB; 8 s timeout; text/html only.
  Extract main text (trafilatura or readability-lxml). Include receipts for the domain.
- Max ~8000 words (truncate with visible note).
- Response:
```json
{ "id": "r_abc123", "lang": "en", "genre": "news|opinion|social|unknown",
  "spans": [{"start":120,"end":168,"quote":"...","label":"fear_appeal","color":"red",
             "confidence":0.82,"explanation":"plain-language why","reported_speech":false,
             "experimental":false}],
  "profile": {"words":812,"flagged_share":0.06,"density_per_1000":4.9,
              "counts":{"fear_appeal":2},"summary":"neutral template sentence(s)"},
  "innocent_explanations": ["..."],
  "receipts": [...], "mode": "llm|heuristics_only", "notes": [],
  "cost": {"input_tokens":0,"output_tokens":0,"cached":true} }
```

### Pipeline
1. **segment** → paragraphs/sentences with ids (`p3s2`), language (en/et/ru/other). *(done in M1)*
2. **heuristics** (free) → hints, not verdicts. *(done in M1)*
3. **tagger** (LLM): static, cache-friendly system prompt (taxonomy + rules first); user message
   = numbered sentences + hints. Output JSON array
   `[{sentence_id, quote, label, confidence, explanation}]` or `[]`. Chunk ~1200 words/call.
   Robust JSON parsing, one retry, fail soft to heuristics for that chunk.
4. **validate** → verbatim match, our offsets, reported-speech flag, count rejections. *(done in M1)*
5. **gate** → `techniques.yaml` (enabled, min_confidence per sensitivity, experimental).
6. **profile** → genre, coverage, density, counts, template summary, innocent explanations.

### Taxonomy v1 (`config/techniques.yaml`)
Techniques: `loaded_language`, `name_calling`, `fear_appeal`, `exaggeration_minimisation`,
`whataboutism`, `false_dilemma`, `questionable_authority`, `causal_oversimplification`,
`bandwagon`, `doubt_casting`. Claim labels: `unsupported_claim` (amber), `supported_claim`
(green), `balance` (blue). Each: `{label, color, definition, example, enabled,
min_confidence:{strict,balanced,broad}, dev_precision:null, experimental:true}`.

### Caching & freshness ("our memory is a database, not a chat history")
- **Technique analysis never goes stale**: cache per paragraph, key =
  `sha256(normalize(paragraph)) + model + prompt_version + taxonomy_version`, no TTL.
  `normalize` = NFKC, strip zero-width chars (U+200B–U+200D, U+2060, U+FEFF), collapse whitespace.
  Store `seen_count` and `distinct_domains` per paragraph hash ("seen on N pages").
- `private=true` → analyze but do not write to the shared cache. UI checkbox
  "Don't store this text" (default off), explained in one line under the textarea.
- **Receipt TTLs** (soft = serve + refresh in background, hard = refresh first): lookalike: until
  outlets.yaml changes · rdap 7 d · first archive / first cert 7 d · fact_checks 12 h ·
  syndication 2 h if content < 48 h old else 24 h.
- **Single-flight** per cache key (asyncio future) — never duplicate LLM/search calls.
- **Negative caching** 10 min. **Budget-aware TTLs**: >70% of daily cap → soft TTLs ×2;
  100% → heuristics-only + banner.
- Receipts show `checked_at`; "Refresh" max once per 15 min per item globally.
- Demo sample texts are pre-warmed into the cache at startup (no LLM cost for judges' first clicks
  after the first run).

### Budget guard & rate limit
- Log every LLM/embedding call (model, tokens, latency, ok) in SQLite. `/api/health` shows today's
  usage and mode.
- Rate limits per client IP (from Traefik `X-Forwarded-For`): analyze 30/h, swarm 10/h,
  receipts 60/h, feedback 30/h. Friendly 429 messages.

### `POST /api/receipts` (also powers `/source`)
Request `{url}` or `{domain}`. Each receipt `{type, status: found|not_found|not_checked|error,
text (plain English), evidence_url, checked_at}`. Concurrent, 8 s timeouts.
- `domain_age` (RDAP `https://rdap.org/domain/{d}`) · `first_seen_archive` (Wayback CDX) ·
  `first_certificate` (crt.sh, tolerate timeouts) · `lookalike` (vs `outlets.yaml`: edit
  distance, homoglyphs, TLD swaps, hyphen/word insertion) · `fact_checks` (Google Fact Check
  Tools, only if key) · `syndication` (only if SEARCH_PROVIDER; distinguish wire syndication
  from copy networks).
- **AI-generated-text detection is deliberately NOT included** — detectors are unreliable and
  known to be biased against non-native writers. Say so in /methodology.

---

## 5. Backend spec — Swarm mode (`POST /api/swarm`)

### Input
- CSV upload or JSON: required `id, author, timestamp (ISO 8601 or unix), text`;
  optional `url, author_created_at, platform`. Max 2000 posts / 2 MB. Validate and report
  rejected rows. Processed **in memory**; nothing stored unless the user clicks Share.
- Options: `deep: bool` (embedding-based paraphrase detection; costs credits, capped at 2000
  embeddings per request and shown in the UI), `tag_clusters: bool` (run the technique tagger
  on each flagged cluster's representative text; max 10 LLM calls).

### Signal families (deterministic unless noted)
- **T — text reuse**: normalize (as §4 + lowercase, strip URLs/@mentions for comparison),
  character 5-gram shingles, MinHash LSH (`datasketch`), near-duplicate if Jaccard ≥ 0.6.
  Only posts with ≥ 8 words count as evidence (short texts like "so true!" or "happy new year"
  collide innocently). Exact duplicates of ≥ 8 words always count. `deep=true`: embeddings,
  cosine ≥ 0.92, posts ≥ 12 words, marked as weaker "paraphrase" evidence.
  Clusters = connected components over near-duplicate pairs **from different authors**.
- **S — timing synchrony** (per cluster): tightest window containing ≥ 60% of the cluster's
  posts; compare against the dataset's overall posting rate (Poisson tail probability).
  Signal if p < 0.001 and ≥ 3 distinct authors. Also per-author cadence regularity
  (coefficient of variation of inter-post intervals < 0.2 with ≥ 5 posts) as supporting evidence.
- **A — account similarity** (per cluster): username pattern similarity (shared stem + digits,
  Levenshtein ≤ 2 between stems), creation dates clustered within 7 days (if provided), share of
  cluster authors whose posts are mostly (≥ 70%) cluster content.
- **M — amplification**: the same non-mainstream URL/hashtag/mention pushed by ≥ 5 cluster
  authors; mainstream outlets (from `outlets.yaml`) do not count.

### Verdict per cluster (never per person)
- `likely_coordinated`: T + at least one of {S, A, M}
- `worth_watching`: exactly one family
- `organic_looking`: reuse explained by an innocent pattern (below) and no S/A
- `insufficient_data`: < 3 distinct authors
- **Innocent-pattern checks** (downgrade one level + list in `innocent_explanations`):
  cluster text ≈ title of a linked mainstream article (headline sharing); very short or generic
  text; quoted official statement (quotation marks + attribution); event hashtag burst affecting
  many unrelated clusters at the same time.
- Output per cluster: id, verdict, families present, evidence for / evidence against,
  innocent explanations, authors (role: first poster / amplifier), post count, time span,
  representative text, timeline points, optional technique tags. Language: behaviours, not
  labels ("these 14 accounts posted near-identical text within 6 minutes").

---

## 6. Web app spec (`web/`, served by FastAPI, plain HTML/CSS/vanilla JS)

- `/` — hero + punchline; tabs or cards linking the three tools; Analyze: textarea + URL field,
  sensitivity, "Don't store this text", 3 sample texts (manipulative, neutral wire-style, opinion).
  Result: highlights (colour + underline style + icon), legend, tooltips (label, definition, why,
  confidence, reported-speech badge), profile card, innocent explanations, receipts (if URL),
  "Report a wrong label", "Share result".
- `/source` — URL/domain → receipts with status icons, "checked X ago", Refresh.
- `/swarm` — upload/paste, "Load sample dataset" (from the benchmark, includes campaigns AND hard
  negatives so judges see both a hit and a correctly ignored lookalike), options (deep, tag);
  results: cluster cards sorted by verdict, simple timeline chart (inline SVG or a small
  vendored chart lib), evidence for/against, innocent explanations, author table, JSON download.
- `/metrics` — renders `eval/results/latest.json` + `eval/results/swarm_latest.json`.
- `/methodology` — renders `docs/METHODOLOGY.md`.
- `/api` — what the API does + curl examples; link to `/docs`.
- Accessible, keyboard-usable, dark-mode friendly, works on mobile.

---

## 7. Evaluation spec

### Analyze (techniques)
- Gold JSONL `{id, lang, genre, text, spans:[{start,end,label}]}`; team annotates a sheet
  (`id, lang, genre, text, quote, label`) → `sheet_to_jsonl.py` (reports unfound quotes; seeded
  70/30 split into dev/test; test LOCKED, only `run_eval.py --final`).
- Metrics: span P/R/F1 per label (same label, overlap ≥ 50% of shorter span); **clean-text FPR**
  (share of neutral paragraphs with ≥ 1 red/amber span) + flags per 1000 words; **symmetry flip
  rate** (mirrored pairs → label sets differ); validator rejection rate; abstention; Cohen's kappa
  where annotators overlap; mean tokens & latency; `--models` bake-off (F1 vs cost).
- Writes `eval/results/latest.json` (git commit + timestamp) and updates `dev_precision` /
  `experimental` in `techniques.yaml`.

### Swarm (coordination)
- `eval/swarm/make_benchmark.py` (seeded, reproducible): organic background posts from a public
  short-text dataset (document which in METHODOLOGY), with synthetic authors and timestamps
  (diurnal Poisson). Inject ~8 campaigns (5–40 accounts each) at three difficulty levels:
  `copy` (identical), `template` (slot words, emoji, hashtags), `paraphrase` (LLM-generated with
  the **dev** key — ask before generating, cache to disk, ≤ 100 calls total). Add **hard
  negatives**: organic users sharing the same news headline + mainstream URL within an hour;
  an event-hashtag burst; generic short phrases. Hold out half the campaigns + negatives as test.
- `run_swarm_eval.py`: author-level precision / recall for campaign membership, cluster purity,
  **FPR on organic authors**, **FPR on hard negatives** (reported separately), results by
  difficulty level, with and without `deep`. Writes `eval/results/swarm_latest.json`.
- Be explicit in /metrics: *the swarm benchmark is synthetic; it measures the mechanics, not
  real-world prevalence.*

---

## 8. Deployment spec (unchanged, working since M0)

- VPS: Hostinger KVM 2. Existing containers `traefik-traefik-1` and `hermes-agent-*` — **never
  modify them**. Project dir on server: `/docker/stoprop`.
- Traefik (host network, entrypoints `web`/`websecure`, certresolver `letsencrypt`) reaches the
  container directly — **no shared network, never a `ports:` section**. Compose:
  ```yaml
  services:
    stoprop:
      build:
        context: .
        args:
          - GIT_COMMIT
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
- uvicorn: `--host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'`.
- Deploy flow: PC `bash deploy.sh` pushes `main` + `main:live` (5 retries) and polls
  `/api/health` until `commit` = `git rev-parse --short=8 HEAD`. VPS cron every minute runs
  `scripts/server-deploy.sh`: fetch `origin/live` → if changed: `git reset --hard`, build with
  `GIT_COMMIT`, health-check via `docker compose exec`, **auto-rollback** if unhealthy, flock
  against parallel runs. **Never `git clean`** on the server. Log: `/var/log/stoprop-deploy.log`.
- `.env` only on the server (`chmod 600`); `.env.local` (dev key) only on the PC; both gitignored
  and in `.dockerignore` (with `.git`, `.venv`, `data/`, `eval/data/`, `__pycache__`).
- Env changes on the server need `cd /docker/stoprop && docker compose up -d` (no new commit).
- Memory: keep the container lean (2 vCPU / 8 GB shared with Traefik + Hermes). Swarm requests
  must finish in < 20 s for 2000 posts without deep mode.

---

## 9. Milestones (in order; stop and report after each)

- ✅ **M0** walking skeleton, deploy pipeline. ✅ **M1** segment, heuristics, validate, profile,
  heuristics-only analyze, web analyzer UI.
- **M2 (target Sat 17:00)** — reconcile code with v2 (remove extension bits) → LLM tagger via
  AI Router, prompt file, per-paragraph cache + single-flight + private mode, budget guard,
  rate limits, sensitivity gating, innocent explanations, pre-warmed demo samples. LLM mocked in
  tests; one announced real call.
- **M3 (target 19:00)** — SSRF-safe URL fetch + extraction; receipts v1 (rdap, wayback, crtsh,
  lookalike, factcheck if key); `/api/receipts`; `/source` page; receipts in analyze results;
  TTLs, Refresh button. Tests for every SSRF block case (network mocked).
- **M4 (target 21:30)** — `sheet_to_jsonl.py`, `run_eval.py`, `/metrics` (analyze part), small
  bake-off (announce call count first), update `techniques.yaml` gating.
- **M5 (target Sun 01:00)** — Swarm mode: ingest, T/S/A/M signals, clustering, verdicts,
  innocent-pattern checks, `/api/swarm`, `/swarm` page with sample dataset, benchmark generator
  + `run_swarm_eval.py`, swarm section on `/metrics`.
- **M6 (Sun, freeze 08:30)** — `/methodology` (incl. "Known limitations" and why no AI-text
  detection), `/api` page, README, share links, mobile check, final `--final` eval (announce
  calls), PROGRESS.md, tag `v1.0`.
- **Stretch (only if all above done)**: syndication search receipt; semantic claim memory;
  "watch boards" (a few curated RSS feeds per theme, refreshed hourly per theme, shared).

## 10. Non-goals
- No browser extension. No user accounts, no server-side reading history.
- No verdicts about people/outlets ("bot", "propagandist"); no single overall score.
- No AI-generated text/image detection.
- No autonomous agent loops in the request path (cost, latency, prompt injection).