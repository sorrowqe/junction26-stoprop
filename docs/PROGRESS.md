# Progress

_Last updated: 2026-11-14 (v1.0.0 live on https://stoprop.tech — all milestones M0–M6 & all 4 Hostinger challenge tracks deployed; 606/606 tests passing)_

Spec: CLAUDE.md **v2** + full 4-track Hostinger suite (`/`, `/source`, `/swarm`, `/traffic`, `/metrics`, `/methodology`, `/api`, `/docs`).

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

## M5 Part A DONE (swarm detector + UI, deployed)
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

## M5 Part A2 DONE (link-based collection, deployed)
- `backend/app/swarm/collectors/`: `http.py` (Session: 40 requests, 25 s deadline, SSRF-safe client, no redirects,
  StopropBot User-Agent, 429 -> stop), `bluesky.py` (public AppView: getPostThread depth 1000 + re-fetch of cut
  branches, getQuotes up to 300, getProfiles only for missing createdAt; single mode getProfiles + getPosts 25/call),
  `mastodon.py` (server API status + context; 60-reply cap for logged-out visitors disclosed; 401/403 explained),
  `youtube.py` (Data API v3 with GOOGLE_API_KEY: videos, commentThreads, comments for 5 long reply threads,
  channels 50/call; "not configured" without key; quota/comments-disabled messages), `__init__.py` (classify,
  closed-platform messages for X / Meta / TikTok / Reddit, 10-min in-memory cache, multi-link up to 50).
  Image/video-only posts are left out with a note.
- Campaign merging (`clusters.merge_campaigns`): clusters with author-set Jaccard >= 0.5 -> one campaign card with
  message variants; verdicts unchanged (strongest variant shown).
- `/api/swarm`: `{"url"}` / `{"urls"}` (+ `"stream": true` NDJSON progress); same 10/h limit, invalid/closed
  links do not count. `/api/health.collectors` (youtube configured?).
- `/swarm`: tabs "Paste a link" (default) / "Paste several links" / "Upload a file", "Which links work?",
  examples (fictional campaigns; real Bluesky thread snapshot `web/static/examples/bluesky_thread.json`, account
  names pseudonymized, built from the recorded live run, no live fetch), live progress line, source box, campaigns.
- Live checks (2026-11-14): Bluesky https://bsky.app/profile/bsky.app/post/3mzhhvhjfwk22 -> 639 posts / 547 accounts /
  8 requests, all creation dates, 0 clusters; Mastodon https://mastodon.social/@Mastodon/117117221397911074 ->
  61 posts / 20 accounts / 2 requests, 60-reply cap note, 0 clusters; YouTube NOT run (no GOOGLE_API_KEY on the PC).
- Tests: 546 passed (50 collector tests on anonymized recorded responses; YouTube from documented API shapes).
- `scripts/swarm_live_check.py` (one live run, --record, --replay, --snapshot, --pseudonymize).

## M5 Part A3 DONE (Trace a post, not yet deployed)
- `backend/app/trace/`: `fingerprint.py` (one MODEL_SUMMARY call picks 2-3 sentences >= 8 words, verified verbatim;
  word-rarity fallback; query = <= 24 words, no quote marks), `search.py` (Oxylabs Web API `/v1/search`, Bearer
  SEARCH_API_KEY, quoted query, max 20 results, snippet dates; Bluesky `searchPosts` on api.bsky.app without login
  (public.api.bsky.app answers 403); Mastodon not searched: status search needs login), `run.py` (max 6 searches,
  30 s, every hit verified: Bluesky text contains the sentence or whole post copied, web snippet has >= 6 consecutive
  words; quotes/mentions counted separately; confirmed dated copies -> swarm detector via `analyze_batch(groups=...)`;
  receipts (lookalike + RDAP domain age) for <= 8 websites; spread view: first seen, timeline, websites, accounts).
- New innocent pattern for all swarm runs: syndication (earliest copy on/links to a news agency or press-release site).
- `POST /api/trace` (text|url, stream), 5 traces/hour/IP, nothing stored (collectors called with cache=False).
- `/trace` page + nav link on every page; principle banner "We trace where this text spread. We don't research who is
  behind any account."; docs/METHODOLOGY.md "Trace a post" (what it does / deliberately doesn't do).
- Live check (2026-11-14, NO AI and NO Oxylabs: rarity rule + Bluesky search only): https://bsky.app/profile/bsky.app/post/3mzhhvhjfwk22
  -> 2 searches, 8 results, 1 confirmed copy, 4 quote/mention hits excluded, verdict insufficient_data, 0 credits.
- Tests: 561 passed (15 trace tests; Bluesky search fixtures from the anonymized recorded post shape, Oxylabs from its
  documented response shape; LLM via FakeLLM).
- NOT yet run: the AI fingerprint and Oxylabs web search live (no SEARCH_API_KEY on the PC; needs Aleksandr's OK).

## Display + claim verification + real web search & anti-bot scraping DONE (ready to deploy)
- Display: techniques red, not yet validated dashed, all shown; validated = precision >= 0.70 on >= 15 predictions.
- Verify claims: `backend/app/verify/` (claims, news, judge, run), `POST /api/verify`, "Verify claims" card.
- Multi-tier URL scraping & search (`pipeline/oxylabs.py`, `pipeline/websearch.py`, `pipeline/extract.py`):
  - Fixed `verify/news.py` ignoring `SEARCH_PROVIDER` and fixed `trace/search.py` to use real Oxylabs AI Studio
    (`https://api-aistudio.oxylabs.io`, `x-api-key`) instead of `webapi.oxylabs.io`.
  - Added **Brave Search API** (`BRAVE_API_KEY`) as primary sub-second news & exact-phrase search (`extra_snippets=true`
    verbatim passages + ISO `page_age` dates) with automatic failover to Oxylabs AI Studio `/search/instant`.
  - Added **Oxylabs AI Studio `/scrape`** Tier-2 Markdown fallback (after SSRF + public-DNS checks) for bot-blocked /
    JS-rendered sites on both Analyze URL (`/`) and Verify claims, with 6 h SQLite cache + `OXYLABS_DAILY_CAP=150`.
  - Added Tier-0 social post URL shortcut on Analyze URL (Bluesky, Mastodon, YouTube links read via native collectors).
  - Added port-43 WHOIS registration-date fallback (`rdap.py`) for `.ee` and other ccTLDs without RDAP, plus Brave-backed
    `syndication.py` ("Copies elsewhere") receipt on Analyze URL, and `RATE_LIMIT_MULTIPLIER` for shared venue Wi-Fi.
  - Verified live via `scripts/live_web_smoke.py` (0 AI Router calls): Brave `site:err.ee` 5 hits in 1.75 s, Oxylabs
    scrape `bbc.com/news` 1,419 words in 8.6 s, Bluesky post URL in 0.91 s, `err.ee` domain age `2010-07-04` in 2.95 s.
  - Unit tests: **602 passed** (all network/LLM mocked).
- **Analyzer Recall, Taxonomy & Inline Explanations Upgrade**:
  - Offline error analysis on `eval.db` found old prompt Rule 10 ("0.8 or more") caused `gemini-3.5-flash-lite-eu` to
    score clear detections at `0.80-0.85`, so `strict: 0.90` discarded 73% of detections, while narrow definitions
    missed `fear_appeal` (0/4) and `name_calling` (4/24) and over-triggered `unsupported_claim`.
  - Sharpened `techniques.yaml` definitions + `display_name`s (`Questionable / fabricated authority`, `Name-calling /
    labelling`, `Fear / threat appeal`, `Whataboutism / deflection`, `Causal oversimplification / scapegoating`, `Doubt
    casting / insinuation`), rewrote `tagger_system.md` with explicit boundary rules + calibrated `0.90-0.98` rubric +
    tight `unsupported_claim` rules, added `whataboutism` & `false_dilemma` hints across EN/ET/RU lexicons, added
    ellipsis quote recovery in `validate.py`, and displayed plain-language explanations directly inside Findings on `/`.
- **Check a Source v2** (`/source` + `POST /api/receipts` + IPv4-first `GuardedBackend`):
  - Fixed a major network stall in `extract.py` where `GuardedBackend` tried up to 3 IPv6 `AAAA` records before IPv4 on
    dual-stack domains (stalling on IPv4-only Docker bridges and venue Wi-Fi).
  - Added CertSpotter CT fallback (`crtsh.py`), Brave watchdog/fact-checker search (`factcheck.py`: EUvsDisinfo,
    Propastop, Snopes, PolitiFact, Full Fact, FactCheck.org, Lead Stories, Correctiv), satire & public state-media
    context (`outlets.yaml` + `lookalike.py`), dictionary allowlist (`lemonade.com`), and live page/accountability
    inspection (`pagecheck.py`: redirects, HTTPS, About/Contact/Policy links, article byline/date, brand impersonation,
    automatic article `syndication` check on `/source`).
  - Upgraded `/source` UI with Domain History Timeline SVG, signal summary pills, Live Page & Transparency card,
    expandable watchdog/mirror links, and 1-click bridges to Analyze and Swarm mode.
- Unified **Swarm Mode** + Analyst Charts (`/swarm`, `/trace` -> 302 `/swarm`):
  - Replaced the 3-tab CSV UI and separate `Trace a post` nav tab with a single auto-detecting `/swarm` box (CSV/JSON
    moved into a collapsed *Advanced* drawer; 1-click demos never dump raw CSV).
  - Added public **Reddit** (`reddit.py`: threads & subreddits via Atom `.rss`), **Telegram** (`telegram.py`: single
    channel `t.me/...` + multi-channel cross-campaign batches), and **Hacker News** (`hackernews.py`) collectors.
  - Upgraded spread tracing to accept any article URL or 3+ word topic/claim and search **Open Web & News, Reddit,
    Telegram, Bluesky, and Hacker News** in parallel (verified live: 68 hits across 16 domains & 41 accounts in 3.28 s,
    0 AI Router calls).
  - Replaced unlabeled dot timelines with interactive analyst charts: 24-bin batch volume histogram (likely coordinated
    vs. worth watching vs. unclustered + baseline average line + shared domains/hashtags), stacked cross-platform spread
    histogram + labeled platform swimlanes, 4-tile `T/S/A/M` signal scorecards, account-labeled cluster swimlanes with
    shaded Poisson burst-window bands and relative `+0s..+Nm` axes, and hover/click post inspectors.

## M6 — Production Hardening, Full Benchmarks, UI Redesign & Clean the Traffic (deployed live)
- **Detector & Validator Hardening:** raised `MAX_OUTPUT_TOKENS` to `4000`, added off-by-one same-paragraph unique quote rescue + generic `loaded_language` overlap deduplication in `validate.py`, added structural lexicon pre-anchors (`causal_oversimplification`, `exaggeration`, `false_dilemma`, `whataboutism`), and added undated open-web fallback to `verify/news.py`.
- **Outbound Link-Network Provenance (`pagecheck.py`):** classifies external `<a href>` domains on `/source` into verified public/wire outlets, state-funded media, and look-alike domains.
- **Clean the Traffic (`/traffic`, `POST /api/traffic`):** Nginx/Apache Combined, Caddy/Traefik JSON, and CSV server-log inspector enriched live with **Shodan InternetDB**, **RIPE NCC ASN**, and optional **VirusTotal v3**, generating ready-to-paste Hostinger `.htaccess`, Nginx, and `ufw` rules while protecting shared office NAT and ASN-verified search crawlers.
- **Complete Benchmarks Published on `/metrics`:**
  - 89-passage Dev Split refreshed (`Strict` precision `65.4%`, recall `30.2%`, F1 `41.3%`; `24/25` clean controls passed `96%`).
  - 49-passage Locked Held-Out Test Split (`test_final.json`): `60.0%` overall precision (`80.0%` on multi-technique), `47.2%` F1, `11/11 (100%)` clean controls passed.
  - 40-scenario Swarm Stress Benchmark (`swarm_latest.json`): `100%` precision, `85.0%` recall (`17/20`), `91.9%` F1, `20/20 (100%)` organic crowds passed.
  - 24-scenario Clean-Traffic Benchmark (`traffic_latest.json`): `100%` precision, `100%` recall (`12/12`), `100%` F1, `12/12 (100%)` innocent visitor controls protected.
- **Persistent SQLite Volume & UI Redesign:** switched `docker-compose.yml` to named volume `stoprop_data:/app/data` (`storage: "sqlite"` verified live), redesigned `style.css` across all pages, added `Copy brief` export across all tools, 1-click cross-tool handoffs, and draggable browser bookmarklet.

## UI redesign v3 (2026-11-16, committed locally on `main`, NOT pushed / NOT deployed)
- New design system in `web/static/style.css` (design tokens, light + dark theme, frosted sticky nav, pill buttons,
  switches, glass tooltip, serif reading text for analysed documents) and a shared `web/static/ui.js` loaded in the
  `<head>` of every page: theme toggle, mobile menu, scroll-reveal fade-ins, word-by-word hero headline, segmented
  Sensitivity control (drives the existing `<select id="sensitivity">`), animated border while a request runs,
  copy buttons on code blocks, cross-page fade (CSS view transitions where the browser supports them).
- Home page: animated hero with an illustration of the highlighter, analyzer card, four tool tiles, principles
  section that lights up on scroll, live numbers band (`web/static/home.js`, data from `/api/metrics`), bookmarklet.
- All 7 pages rebuilt on the new layout; every element id is unchanged. Page-script edits are cosmetic only:
  `app.js` (stagger index on highlights), `swarm.js` (chart colours use theme variables), `traffic.js` (active
  format button uses a class instead of inline styles). No backend change.
- Checked locally in headless Chrome at 1440 px and 390 px, light and dark, plus reduced motion: 0 console errors,
  no horizontal scroll on mobile, every block visible after scrolling. Tests: 607 passed.
- To deploy: `bash deploy.sh` (Aleksandr pushes himself).

## UI v4 "editor's desk" (2026-11-16, committed locally on `main`, NOT pushed / NOT deployed)
- Second pass on the look, replacing the v3 visuals (same pages, same element ids, no backend change):
  calm sage paper + dark ink palette (light and dark), Fraunces headings + Instrument Sans text self-hosted in
  `web/static/fonts/`, hairline rules instead of floating cards, paper grain, hand-drawn pencil marks
  (circle round "spin", underlines, the ring on the chosen Sensitivity option, the tick in checkboxes).
- New logo: stop-sign octagon holding quotation marks (`web/static/logo.svg`, `favicon.svg`, inline in every nav).
- Home page: draggable before/after specimen (plain text vs marked-up text), the four tools as a contents list,
  numbered principles, serif numerals for the live benchmark figures.
- Checked locally in headless Chrome (1440 px and 390 px, light and dark, reduced motion): 0 console errors,
  no horizontal scroll on any page at phone width. Tests: 607 passed.
- v5 tweak (same day, after feedback that the sage tint and the soft serif felt too "coffee shop"): neutral paper
  white + true ink palette, headings in the interface sans (Instrument Sans, set tight), and one italic serif
  (Instrument Serif Italic) kept only for accent words. Fraunces removed from `web/static/fonts/`. Logo unchanged.

## Benchmark re-check + README rewrite (2026-11-16)
- Re-ran the offline benchmarks, 0 AI calls: swarm (17/20, 0/20) and traffic (12/12, 0/12) reproduce byte for byte;
  text dev re-scored from the eval cache gives the same figures (Broad P 58.8 / R 41.4 / F1 48.6). The prompt
  version in `eval/results/*.json` (`c7db1a7998`) equals the prompt in the repo, so the text results are current.
- README rewritten from the code and result files. It now states the weak spots too: ~30% recall on SemEval news,
  no technique meets the "validated" bar (>= 0.70 precision on >= 15 flagged spans; every label is
  `experimental: true` in `techniques.yaml`), 5 of 11 symmetry pairs differ, swarm/traffic sets are hand-built.
- Not built from the original brief: `/api/share` + `/r/{id}`, `/api/feedback`, and a visible
  validated / not-yet-validated marker in the UI (a test currently asserts that wording is absent).
- The self-consistency baseline (`self_consistency.json`, 0/11 flips) was measured with an older prompt and has
  not been repeated for the current one.

## Known boundaries (documented on `/methodology`)
- Quantitative text accuracy benchmarks are measured on English (`dev` 89 + `test` 49 passages); other languages use the same conservative `Strict` default.
- Rate limits are per-IP in-memory sliding windows (set `RATE_LIMIT_MULTIPLIER=3` in server `.env` on shared venue Wi-Fi).
- Login-walled social platforms (X/Twitter, Meta, TikTok) are not scraped directly; paste post text into `/swarm` spread search or import CSV/JSON exports.

## How to verify
```bash
.venv/Scripts/python -m pytest -q                                     # 607 passed
curl -s https://stoprop.tech/api/health                               # version 1.0.0, storage: sqlite
```
