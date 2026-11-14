# Stoprop — build guide (Claude Code in VS Code)

This guide is for **you** (Aleksandr). It has three parts:

- **Part A** — one-time setup on your laptop and server (~20 min)
- **Part B** — the prompts to paste into Claude Code, one milestone at a time, each with a
  "how to check it worked" list
- **Part C** — rules, recovery and troubleshooting

`CLAUDE.md` (the spec) goes in the project folder; Claude Code reads it automatically.
This file (`KICKOFF.md`) can sit next to it — Claude Code doesn't need it, you do.

Timeline target (Saturday → Sunday):

| Milestone | What | Target |
|---|---|---|
| M0 | Skeleton live on https://stoprop.tech | 14:45 |
| M1 | Analyzer works without AI (heuristics) + web UI highlights | 16:15 |
| M2 | AI tagging, cache, budget guard | 17:45 |
| M3 | Receipts + safe URL fetching | 19:30 |
| M4 | Evaluation + /metrics page | 21:30 |
| M5 | Browser extension MVP | 00:00 |
| M6 | Themes, history, extension dashboard (optional) | 02:00 |
| M7 | Methodology, README, final eval, freeze | Sun 08:30 |

If you fall more than ~1 h behind, skip M6 first, then shrink M5 (selection only). **Never skip M4.**

---

## Part A — one-time setup

### How deploying works (read once)
```
YOUR PC                         GITHUB (private repo)            VPS (every minute, cron)
develop + test + commit  ──►    main  (backup, push often)
bash deploy.sh           ──►    live  (= production)   ◄──────  server-deploy.sh pulls `live`,
   └─ polls https://stoprop.tech/api/health                      rebuilds, health-checks,
      until it shows your commit → "LIVE"                         auto-rolls back if broken
```
You never need a stable SSH connection to deploy. SSH is only for one-time setup and
reading logs — and you'll do that inside **tmux**, so a dropped connection doesn't kill anything.

### A1. PC prerequisites (check, don't reinstall)
Open **Git Bash** (Windows) or a terminal (Mac/Linux):
```bash
git --version
python --version || python3 --version     # 3.11+ is fine
ssh -V
code --version                            # VS Code
```
Docker on the PC is **optional** (the server builds).

### A2. SSH key + resilient SSH config on the PC
```bash
ls ~/.ssh/id_ed25519.pub || ssh-keygen -t ed25519 -C "aleksandr-pc"   # Enter through prompts
cat ~/.ssh/id_ed25519.pub
```
Add the printed line in hPanel → VPS → **SSH keys** (or on the server:
`echo 'PASTE_LINE' >> ~/.ssh/authorized_keys`). Then add a short alias with keepalives:
```bash
cat >> ~/.ssh/config <<'EOF'
Host stoprop
  HostName 179.67.199.93
  User root
  ServerAliveInterval 15
  ServerAliveCountMax 4
  ConnectTimeout 10
EOF
ssh stoprop 'hostname'        # must print srv2159153 without asking for a password
```
**Always work inside tmux on the server:** `ssh stoprop` → `tmux new -As stoprop`.
If the connection drops: `ssh stoprop` → `tmux new -As stoprop` again → you're back exactly
where you were, and anything that was running kept running.

### A3. Hostinger housekeeping (5 min, in hPanel)
1. **Firewall**: drop TCP **32768** (Hermes's raw HTTP port). Keep 22, 80, 443 open.
2. **AI Router keys**: create `stoprop-prod` (daily limit **500**) and `stoprop-dev`
   (daily limit **800**). Keep the values somewhere safe (password manager), not in chat.
3. **DNS for stoprop.tech**: `A @ → 179.67.199.93`, `AAAA @ → 2a02:4780:7e:3fa::1`,
   `CNAME www → stoprop.tech`. Delete any default parking/builder A or CNAME records.
   Check from the laptop: `nslookup stoprop.tech` → shows 179.67.199.93.

### A4. GitHub repo + project folder on the PC
1. On github.com create a **private** repo `stoprop` — **empty** (no README, no .gitignore).
2. On the PC:
```bash
mkdir -p ~/stoprop && cd ~/stoprop
git init -b main
git config core.autocrlf input          # keep LF line endings (Windows)
```
3. Put **CLAUDE.md** and **KICKOFF.md** into `~/stoprop`, then:
```bash
git add CLAUDE.md KICKOFF.md && git commit -m "Project brief"
git remote add origin https://github.com/YOUR_USER/stoprop.git
git push -u origin main                 # Git for Windows opens a browser login the first time
code .
```

### A5. Start Claude Code in VS Code
- Close any VS Code **Remote-SSH** windows — we work locally now.
- Open the Claude Code panel in the `~/stoprop` folder.
- Use your **own Claude subscription** — do **not** point Claude Code at the Hostinger router
  (that spends the product's credits).
- **Permissions:** keep the default "ask before running commands". Read each command before
  approving. Never enable bypass/auto-accept for `ssh`, `git push`, `deploy.sh` or `rm`.
- OK to "always allow": `pytest`, `python -m ...`, `pip install` inside `.venv`,
  `git add`, `git commit`, `git status`, `git diff`, `git log`, `ls`.

---

## Part B — milestone prompts

Paste **one prompt at a time**. Wait until Claude Code says the milestone is done and you've
ticked the checklist. Then commit (Claude Code should do it; verify with `git log --oneline`).

> Tip: if a session gets long/slow or starts forgetting things, start a **new session** and
> paste the **Resume prompt** (Part C1). That's what `docs/PROGRESS.md` is for.

---

### M0 — walking skeleton (target 14:45)

**Paste into Claude Code:**
```
Read CLAUDE.md fully before doing anything. We are doing milestone M0 only.

Build the walking skeleton:
1. Repo structure exactly as in CLAUDE.md §3 (empty modules are fine where not needed yet),
   .gitignore (.env, .env.local, data/, .venv/, __pycache__/, eval/data/test/),
   .gitattributes (LF for *.sh, Dockerfile, *.py, *.yml, *.yaml), .dockerignore per §8,
   .env.example (all vars from §4, no real values), docs/PROGRESS.md, docs/DECISIONS.md.
2. backend/requirements.txt (pinned versions), a FastAPI app with:
   - GET /api/health -> {"status":"ok","version":..., "commit": <GIT_COMMIT env>, "budget":{"mode":"ok"}}
   - GET / -> placeholder landing page (plain HTML from web/) with the line
     "Stoprop: see the spin before it sees you. We flag the tactics, not the opinions."
3. Dockerfile (python:3.12-slim, non-root user, ARG/ENV GIT_COMMIT, uvicorn with the flags
   from §8) and docker-compose.yml EXACTLY as in §8 (build args, Traefik labels, no ports:).
4. deploy.sh and scripts/server-deploy.sh exactly as in §8.
5. scripts/router_smoke.py: one tiny chat completion via the OpenAI Python SDK using
   ROUTER_BASE_URL / ROUTER_API_KEY / MODEL_TAGGER from env; prints the reply and the usage block.
6. Create a .venv, install requirements, add a pytest test for /api/health, run it.
7. Update docs/PROGRESS.md, git commit, and `git push origin main` (ask me first).

Then STOP. Do not push to `live` yet — I first have to set up the server using KICKOFF.md.
Summarize what you built and anything I need to know for the server setup.
```

**Then you set up the server (one time, ~10 min).** Do it inside tmux:
```bash
ssh stoprop
tmux new -As stoprop
```
On the server:
```bash
# 1) a read-only deploy key so the server can pull from your private repo
ssh-keygen -t ed25519 -f ~/.ssh/stoprop_deploy -N "" -C "stoprop-vps"
cat ~/.ssh/stoprop_deploy.pub
```
Copy that line → GitHub repo → **Settings → Deploy keys → Add deploy key** → paste,
title `vps`, leave **"Allow write access" OFF**.
```bash
# 2) tell SSH to use that key for GitHub
cat >> ~/.ssh/config <<'EOF'
Host github-stoprop
  HostName github.com
  User git
  IdentityFile ~/.ssh/stoprop_deploy
  IdentitiesOnly yes
EOF
ssh -T github-stoprop            # type "yes" once; expect "...successfully authenticated..."
```
Back on the **PC** (Git Bash, in ~/stoprop), create the `live` branch for the first time:
```bash
git push origin main:live
```
Back on the **server**:
```bash
# 3) clone the live branch into the place Traefik/Docker Manager expect
git clone -b live github-stoprop:YOUR_USER/stoprop.git /docker/stoprop

# 4) secrets: create .env by hand (template = .env.example). Paste the stoprop-PROD key.
cp /docker/stoprop/.env.example /docker/stoprop/.env
nano /docker/stoprop/.env
chmod 600 /docker/stoprop/.env

# 5) first deploy, watch it happen
bash /docker/stoprop/scripts/server-deploy.sh --force

# 6) auto-deploy every minute from now on
(crontab -l 2>/dev/null; echo '* * * * * bash /docker/stoprop/scripts/server-deploy.sh >> /var/log/stoprop-deploy.log 2>&1') | crontab -
crontab -l                        # shows the line once
```
From now on, deploying = on the PC: `bash deploy.sh` → wait for `LIVE: <hash>`.

**✅ M0 checklist**
- [ ] `curl -s https://stoprop.tech/api/health` returns JSON with your commit hash
      (first request may take 10–30 s while Let's Encrypt issues the certificate)
- [ ] https://stoprop.tech shows the punchline, padlock is valid
- [ ] Router smoke test: on the server `cd /docker/stoprop && docker compose exec stoprop python scripts/router_smoke.py`
      → a reply + usage
- [ ] On the server `ss -ltnp | grep 8000` prints **nothing** (app not on a public port)
- [ ] Test from your **phone on mobile data**
- [ ] Make a trivial change on the PC (e.g. landing text), commit, `bash deploy.sh` → `LIVE: …`
      within ~1–2 min. **This proves the whole pipeline.**

---

### M1 — analyzer without AI + web UI (target 16:15)

**Paste:**
```
Continue with milestone M1 from CLAUDE.md. Read docs/PROGRESS.md first.

Implement, with unit tests:
- pipeline/segment.py: paragraphs -> sentences with stable ids (p{n}s{m}), original char
  offsets preserved, language detection (en/et/ru/other).
- pipeline/heuristics.py: the free lexical features from §4 step 2, returned as hints with
  sentence ids. Small word lists for EN, ET, RU in backend/config/lexicons/*.txt.
- pipeline/validate.py: verbatim span validation (whitespace/quote-normalized match inside the
  given sentence), offsets computed by our code, reported-speech detection (span inside
  quotation marks attributed to a third party: “ ” " " « » „ “). Thorough tests: exact match,
  whitespace differences, smart quotes, quote not present -> rejected, repeated phrase in one
  sentence, RU and ET text.
- pipeline/profile.py: genre guess (news/opinion/social/unknown, rule-based), word count,
  flagged_share, density_per_1000, counts, neutral template summary.
- POST /api/analyze working in mode "heuristics_only" (no LLM yet) with the response schema
  from §4. Heuristic hints may produce only low-confidence amber "experimental" spans or none —
  keep them conservative.
- web/: analyzer page per §5 (textarea + URL field disabled until M3, 3 sample texts:
  manipulative, neutral wire-style, opinion). Render highlights with colour + underline style
  + icon, legend, tooltip on hover/click (label, definition, why, confidence,
  reported-speech badge), profile card. Accessible, dark-mode friendly, no build step.
- Run all tests, update PROGRESS.md, commit. Then tell me to deploy and what to check.
```

**✅ M1 checklist**
- [ ] `pytest` all green (ask Claude Code to show the summary line)
- [ ] After `bash deploy.sh` → `LIVE`: the 3 sample texts analyze instantly; highlights + tooltips visible
- [ ] Neutral sample shows few or no highlights (this matters — false positives!)
- [ ] Paste a Russian and an Estonian paragraph — no crash, language detected

---

### M2 — AI tagging, cache, budget guard (target 17:45)

**Paste:**
```
Continue with milestone M2 from CLAUDE.md. Read docs/PROGRESS.md first.

Implement:
- llm/client.py: OpenAI SDK against ROUTER_BASE_URL, timeouts (30 s), 1 retry on 5xx/timeout,
  robust JSON extraction, logs every call (model, tokens in/out, latency, ok/error) to SQLite.
- prompts/tagger_system.md: static and cache-friendly (taxonomy from techniques.yaml rendered
  in, definitions + one short example each, the rules: label HOW not WHAT position, never
  judge the author, return [] if nothing applies, quote must be copied exactly from the
  sentence, mark claims as unsupported/supported per definitions, balance markers).
  The user message contains numbered sentences + heuristic hints. Output: JSON array per §4.
- pipeline/tagger.py: chunk by paragraphs (~1200 words/call), call model, validate every span
  with validate.py, count validator_rejections, apply gating from techniques.yaml +
  sensitivity. Fail soft to heuristics for a chunk if the LLM fails.
- Caching & freshness exactly as in CLAUDE.md §4 "Caching & freshness": per-paragraph cache
  (normalize: NFKC + strip zero-width chars), single-flight per key, seen_count /
  distinct_domains, private=true bypasses the shared cache.
- llm/budget.py: daily token cap from env; /api/health shows today's usage and mode;
  over cap -> heuristics_only + UI banner.
- ratelimit.py: 30 analyses/hour per client IP (X-Forwarded-For from Traefik) and per
  X-Install-Token header if present; 429 with a friendly message.
- Tests with the LLM MOCKED (no real calls in tests). Then ONE real call on sample text 1 to
  show it works — tell me before you make it.
- Update PROGRESS.md, commit, tell me to deploy and what to check.
```

**✅ M2 checklist**
- [ ] Analyze sample 1 → AI spans with explanations; response says `"mode": "llm"`
- [ ] Analyze the **same text again** → `"cached": true`, returns instantly
- [ ] `/api/health` shows today's token usage
- [ ] hPanel → AI Router: usage appears under the **stoprop-prod** key, cost looks sane
- [ ] Neutral sample still shows few/no red spans

---

### M3 — receipts + safe URL fetching (target 19:30)

**Paste:**
```
Continue with milestone M3 from CLAUDE.md. Read docs/PROGRESS.md first.

Implement:
- pipeline/extract.py: SSRF-safe URL fetching exactly per §4 (http/https only; resolve DNS and
  block private, loopback, link-local, CGNAT 100.64/10, multicast, 169.254.169.254 and IPv6
  equivalents incl. ::1, fc00::/7, fe80::/10, IPv4-mapped IPv6; re-check after every redirect,
  max 3; 2 MB cap; 8 s timeout; only text/html). Main-text extraction with trafilatura or
  readability-lxml. Tests for every blocked case.
- receipts/: rdap, wayback (first capture), crtsh (first cert, tolerate timeouts),
  lookalike (outlets.yaml seeded with ~40 outlets: Estonian (err.ee, postimees.ee, delfi.ee,
  lsm.lv, lrt.lt...), major EU/US (bbc, reuters, apnews, theguardian, spiegel, lemonde,
  nytimes, washingtonpost, dw, euronews...), with edit distance, homoglyphs, TLD swaps,
  hyphen/word insertion). factcheck and syndication only if keys set, else status
  "not_checked". Per-type TTLs + soft/hard TTL + negative caching per CLAUDE.md.
- POST /api/receipts and include receipts in /api/analyze when a URL is given.
- Web: enable the URL field; receipts panel with plain-English lines, status icons,
  "checked X ago", Refresh button (global 15-min limit per item).
- Tests (network mocked), update PROGRESS.md, commit, tell me to deploy and what to check.
```

**✅ M3 checklist**
- [ ] Analyze a real ERR / BBC article URL → text extracted, receipts show domain age etc.
- [ ] Try `http://127.0.0.1`, `http://169.254.169.254/`, `http://[::1]/`, `http://localhost:8000`
      → all **rejected** with a clear message (you're the red teamer — try a few more!)
- [ ] A made-up lookalike (e.g. type `err-news.ee` into the domain receipt test) → flagged
- [ ] Receipts that weren't checked say **"not checked"**, not hidden

---

### M4 — evaluation + /metrics (target 21:30)

**Before this:** teammates should have the gold sheet started (columns
`id, lang, genre, text, quote, label`; two people independently label the same ~50 rows
for agreement; plus a tab of neutral "clean" paragraphs and a tab of mirrored pairs).
Export each tab as CSV into `eval/data/raw/`.

**Paste:**
```
Continue with milestone M4 from CLAUDE.md. Read docs/PROGRESS.md first.

Implement per §7:
- eval/sheet_to_jsonl.py: CSV -> JSONL with offsets; report rows whose quote is not found;
  random but seeded split 70/30 into data/dev and data/test (test = locked).
- eval/run_eval.py with the metrics listed in §7 (span P/R/F1 per label with >=50% overlap,
  clean-text FPR + flags per 1000 words, symmetry flip rate, validator rejection rate,
  abstention, Cohen's kappa where two annotators overlap, mean tokens/credits & latency),
  --models bake-off mode, --final for the test split, writes eval/results/latest.json
  (+ per-model files) with git commit + timestamp, updates dev_precision/experimental in
  techniques.yaml.
- BEFORE any real LLM run: print the number of calls and estimated tokens and wait for my OK.
  Use the dev key (I'll export ROUTER_API_KEY for the dev key locally).
- /metrics page rendering latest.json: tables, a small F1-vs-cost chart for the bake-off,
  plain-language notes on what each metric means, and a "What we don't detect well" section
  generated from the weakest labels.
- Update PROGRESS.md, commit, tell me to deploy and what to check.
```

**✅ M4 checklist**
- [ ] Small dev run works and the numbers look plausible (not all 1.0, not all 0)
- [ ] Bake-off on 4 models done (you approved the call count) → pick `MODEL_TAGGER`,
      update `/docker/stoprop/.env` on the server, then apply it with
      `cd /docker/stoprop && docker compose up -d` (env changes don't need a new commit)
- [ ] https://stoprop.tech/metrics shows the tables + commit hash + date
- [ ] Techniques below 0.70 dev precision are marked experimental (hidden by default)

---

### M5 — browser extension MVP (target 00:00)

**Paste:**
```
Continue with milestone M5 from CLAUDE.md §6. Read docs/PROGRESS.md first.

Build the Chrome MV3 extension in extension/ (no build step, vendored Readability.js with its
license file):
- context menu on selection "Analyze with Stoprop"; toolbar action "Analyze this page"
- highlights with the CSS Custom Highlight API mapped from our offsets back to text nodes
  (handle text split across many nodes); legend + tooltips consistent with the web app
- side panel: findings, explanations, profile, receipts
- options page: API base URL (default https://stoprop.tech), sensitivity, enabled labels,
  private mode, "send domain for receipts" (default on for public pages)
- random install token in chrome.storage.local, sent as X-Install-Token
- backend: CORS for chrome-extension://<id> from EXTENSION_ORIGINS
- web /extension page: download zip (build a zip into web/static via a script) + 4-step
  "Load unpacked" instructions with screenshots placeholders
- Update PROGRESS.md, commit, tell me how to load it in Chrome and what to test.
```

**✅ M5 checklist**
- [ ] chrome://extensions → Developer mode → Load unpacked → `extension/` loads without errors
- [ ] Copy the extension ID into `EXTENSION_ORIGINS` in the server `.env`, then
      `cd /docker/stoprop && docker compose up -d`
- [ ] Select text on any news site → right-click → side panel shows findings
- [ ] "Analyze this page" highlights spans in place on 2–3 different sites
- [ ] Private mode on → analysis works, and a second identical request is NOT cached

---

### M6 — themes, history, dashboard (optional, target 02:00)

**Paste:**
```
Continue with milestone M6 from CLAUDE.md §6. Read docs/PROGRESS.md first.
Themes (name + keywords in ET/RU/EN) in options; optional auto-analyze on theme match
(off by default, max N per hour); local history in IndexedDB as specified (no full text,
no full URL); extension dashboard.html with techniques over time, themes encountered,
domains with repeated bad receipts, recurring identical paragraphs. Everything local-only.
Update PROGRESS.md, commit.
```

---

### M7 — finish (Sunday, freeze 08:30)

**Paste:**
```
Milestone M7. Read docs/PROGRESS.md first.
1. docs/METHODOLOGY.md + /methodology page: pipeline, principles from CLAUDE.md §1, caching,
   privacy, evaluation protocol, and an honest "Known limitations" section.
2. README.md: what Stoprop is, live URLs, how to run locally, how to deploy, how to load the
   extension, architecture diagram (ASCII), credits/models used and why.
3. Polish demo samples on the landing page; check mobile layout.
4. Tell me the exact command for the FINAL eval (--final on the locked test split) with the
   number of calls; run only after my OK; publish results to /metrics.
5. Update PROGRESS.md, commit, tag v1.0.
```

After M7: **feature freeze 08:30**, final deploy, submit by **09:45**. No deploys during peer
review.

---

---

## Part C — rules, recovery, troubleshooting

### C1. Resume prompt (new session / Claude Code got confused)
```
Read CLAUDE.md and docs/PROGRESS.md. Summarize in 5 bullets where we are, what is broken,
and what the next step of the current milestone is. Do not change anything until I confirm.
```

### C2. Undo a bad change
```
Something broke after your last change. Show me `git log --oneline -5` and `git diff HEAD~1 --stat`,
explain what changed, and propose either a fix or reverting to the last good commit.
```

### C3. Deploy failed — paste this to Claude Code together with the output of:
```bash
ssh stoprop 'tail -40 /var/log/stoprop-deploy.log'
ssh stoprop 'cd /docker/stoprop && docker compose ps && docker compose logs --tail 80 stoprop'
ssh stoprop 'docker logs traefik-traefik-1 --tail 50'
```
Typical causes:
| Symptom | Likely cause |
|---|---|
| 404 from Traefik | Host rule / domain mismatch, or container unhealthy |
| Certificate error | DNS for stoprop.tech not propagated yet → use the srv… URL meanwhile |
| 502 Bad Gateway | App crashed or not listening on 0.0.0.0:8000 — check stoprop logs |
| Container restarting | Missing env var in `/docker/stoprop/.env`, or a Python error |
| `deploy.sh` never says LIVE | Deploy log shows `FAILED … rolling back` → the new build was unhealthy; the old version is still live. Paste the log to Claude Code |
| `\r: command not found` in the log | Windows line endings — check `.gitattributes`, re-commit the script |
| `git push` keeps failing | Venue network — `deploy.sh` retries 5×; or push from your phone's hotspot |

### C4. Golden rules for the weekend
1. **One milestone at a time.** Deploy and tick the checklist before the next prompt.
2. **Commit after every working step.** `git log --oneline` should grow steadily.
3. **No real LLM calls in tests.** Real calls only when Claude Code announced the count.
4. **Secrets never in chat, never in git.** `.env` lives only on the server (and your password manager).
5. **The neutral sample is your canary.** If it lights up red after a change, something regressed.
6. **When behind: cut M6, then shrink M5. Never cut M4.**