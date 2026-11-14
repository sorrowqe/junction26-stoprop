# Methodology

Stoprop covers all four tracks of the Hostinger anti-propaganda & bad-actor challenge — **Name the technique** (`/`), **Trust the source** (`/source`), **Spot the swarm** (`/swarm`), and **Clean the traffic** (`/traffic`) — governed by strict false-positive guardrails and exact-quote verification. See [`web/methodology.html`](../web/methodology.html) (live at [`/methodology`](https://stoprop.tech/methodology)) for the reader-facing overview.

## Swarm mode: where the posts come from

Swarm mode analyses a batch of posts. The detection never knows how the batch arrived: every input
becomes the same list of posts (id, author, time, text, optional link and account creation date).

| Input | How Stoprop reads it | What we get | Limits |
|---|---|---|---|
| **Reddit** thread or subreddit link | Public Atom `.rss` feed (`reddit.com/r/….rss`, XXE-safe XML parser, no login) | Post + comment tree or latest subreddit posts with authors & UTC timestamps | Public posts only |
| **Telegram** channel link(s) (`t.me/…`) | Public channel web preview (`t.me/s/…`, single channel or multi-channel batch) | Recent public channel messages, timestamps, forwarded/shared links | Public channels with web preview enabled |
| **Hacker News** item link | Official Algolia HN Search API (`hn.algolia.com/api/v1/items/{id}`) | Story + nested comment tree with authors & timestamps | Public items |
| **Bluesky** post link | Public AppView API `public.api.bsky.app` (no login): `getPostThread` (depth 1000; branches the server cuts short are re-fetched), `getQuotes` (up to 300 quote posts), `getProfiles` only for missing creation dates | The post, all replies, quote posts, account creation dates | Times are the post's own `createdAt` (set by the author's app) |
| **Mastodon** post link (any server) | That server's public REST API: `/api/v1/statuses/{id}` and `/context` | The post and its replies, account creation dates (date only for remote accounts) | Many servers share at most **60 replies** with visitors who are not logged in; we say so when the cap is hit. Servers that require login are reported as such. |
| **YouTube** video link | YouTube Data API v3 with the server's `GOOGLE_API_KEY`: `videos`, `commentThreads` (100 per page, newest first), `comments` for up to 5 long reply threads, `channels` for creation dates | Comments, replies, channel creation dates | Needs a key on the server (otherwise "not configured"); each call uses 1 unit of the key's daily quota (10,000 by default) |
| **Several links** (up to 50, mixed) | The same APIs, one post per link, batched where the API allows (Bluesky 25 per call, YouTube 50) | One post per link | Unreadable links are listed with a reason |
| **File upload** (CSV / JSON) | Parsed in the browser request | Whatever the user exported | 2000 posts / 2 MB |

Limits for every link run: **2000 posts, 40 API requests, 25 seconds**. When a limit is reached we keep what
was collected and say so. Requests carry a descriptive User-Agent (`StopropBot/… (+https://stoprop.tech/methodology)`),
and a "slow down" answer (HTTP 429) stops collection instead of retrying. Posts with only images or video
(no text) are left out and counted in a note.

**Security.** Mastodon server names are typed in by users, so every collector request goes through the same
SSRF-safe client as URL analysis: http(s) only, standard ports, no credentials in links, DNS answers must be
public addresses, connections go only to the vetted address, no redirects. API keys stay on the server and
are never logged or returned.

**Privacy.** Collected posts are processed in memory. To make a repeated check instant, a run's collected
posts are kept in memory for 10 minutes per link and then dropped; nothing is written to disk. The demo
example "a real Bluesky thread" is a stored snapshot (taken 2026-11-14 of a public post by the official
Bluesky account) so demos never fetch live; account names other than the original poster are replaced by
pseudonyms, texts and times are unchanged.

### What we cannot access directly, and why

- **X (Twitter)**: no free public access to posts and replies; reading requires a paid developer account and login.
- **Facebook, Instagram, Threads (Meta)**: no access to posts and comments without login and approved app access.
- **TikTok**: the research API is open only to approved academic researchers.

For these login-walled platforms, users can paste a post's text into `/swarm` to trace where copies spread across the open web and public forums, or upload a research CSV/JSON export. We do not scrape logged-in pages or work around access rules.

### Campaigns

Clusters are formed per message (copied or near-identical text). When the author sets of two or more
clusters overlap strongly (Jaccard ≥ 0.5), they are shown as one **campaign** with its message variants.
Merging does not change any verdict: the campaign shows its strongest variant's verdict, and each
variant's evidence is listed.

## Trace a post

Principle: **we investigate the message, not the messenger.** Trace starts from one post (pasted text, or a
Bluesky / Mastodon / YouTube link read with the swarm collectors) and asks one question: where else does this
exact text appear?

What it does:

1. **Fingerprint.** One small AI call (`MODEL_SUMMARY`) picks 2–3 sentences with distinctive wording (≥ 8 words).
   Code checks that every pick appears word for word in the post; if the AI is unavailable, not configured, over
   budget or its picks fail the check, a deterministic word-rarity rule chooses instead (the result says which).
2. **Exact-phrase search**, at most 6 searches and 30 seconds per trace:
   - open web: search-engine results through Oxylabs' Web API `/v1/search` with the sentence in quotes
     (`SEARCH_PROVIDER=oxylabs`, `SEARCH_API_KEY`); we use the result URL, domain, title and snippet, and a date
     only when the snippet starts with one;
   - Bluesky: the public post search (`app.bsky.feed.searchPosts` on api.bsky.app), no login;
   - Mastodon: **not searched**, because full-text post search needs a logged-in account on every server.
3. **Verification.** The search proposes, code verifies: a Bluesky result counts as a copy only if the sentence is
   in its text word for word (or the whole post is copied); a web result only if at least 6 consecutive words of
   the sentence appear in its title or snippet. Results that merely quote or mention the post are counted
   separately and not treated as copies; unconfirmed web results are listed but not analysed.
4. **Swarm signals on the copies.** Confirmed copies with a known time form one text-reuse group, and the normal
   swarm rules run on it: timing synchrony, account similarity (from the names and creation dates the search
   returned with each post), amplification, innocent explanations and the verdict rules. A new innocent
   explanation applies here and in swarm mode: **syndication**, when the earliest copy is on (or links to) a news
   agency or a press-release service, because republishing that copy is normal.
5. **Website checks.** For up to 8 websites carrying the text: domain age (RDAP, cached as on /source) and the
   look-alike-name check against known outlets.
6. **Spread view.** First seen, a timeline, websites and accounts that carry the text, the verdict, evidence for
   and against, innocent explanations, and what was and was not searched.

What it deliberately does not do:

- **No identity research.** We never look up who runs an account or a website beyond what the search API returns
  with the post itself (name, post time, account creation date). No profile pages, follower lists, WHOIS owner
  data or other enrichment.
- **No cross-platform account linking.** A Bluesky account and a website are never treated as the same actor,
  even if names look alike.
- **No logged-in access and no scraping of closed platforms.** Only public search interfaces.
- **Nothing stored.** The post, the fingerprints and the results are processed in memory and returned once; trace
  does not use the 10-minute collection cache. (Domain-age receipts are public facts about domains and share the
  /source cache.)
- **Limits:** 5 traces per hour per IP, 6 searches and 30 seconds per trace.

Known limits: search engines index only part of the web and may miss very recent copies; snippet dates are often
missing, so web copies without a date are listed but cannot be placed on the timeline; Bluesky search returns at
most 100 results per sentence; a text that many people quote independently (a slogan, a headline) will show
spread without coordination, which is why innocent explanations are always listed.

## Verify claims

"Verify claims" on an analysis checks up to 3 factual claims against recent news; it never says "true" or "false".
Bounded pipeline (no agent loop, <= 38 s): one AI call extracts the claims, the cited outlet and the exact
attribution phrase (checked word for word); a lenient search of the cited outlet's domains (last 30 days, any key
entity) and a news search across outlets (last 14 days) run via the Brave Search API (primary, with `extra_snippets`
and automatic failover to Oxylabs AI Studio Instant Search, or free GDELT DOC 2.0 when no search keys are set); at
most 5 articles are read with the SSRF-safe fetcher (falling back to Oxylabs AI Studio headless Markdown scraping for up
to 2 bot-blocked pages, or Brave's verbatim `extra_snippets` when a page is paywalled) plus up to 8 more headlines from
the cited outlet; one AI call per claim judges supports / contradicts / unrelated, and every evidence quote must appear
word for word in the article, search excerpt, or headline, otherwise the judgement is discarded. Google Fact Check Tools
is searched when a key is configured. Note: domain-age fallbacks on country TLDs without RDAP (such as `.ee`) read only
the registration timestamp from port-43 WHOIS, never registrant/owner data.

Verdicts: **cited source not found** (the index has recent articles from that outlet on the topic, but none says it;
also adds a red "unverifiable attribution" span on the attribution phrase), **contradicted**, **corroborated**
(N independent outlets; an outlet's own sister domains count once), **no reports found** (with the caveat that it
may be too recent or not indexed), **could not check** (search busy or failed, or no articles from the cited outlet
on the topic at all; never turned into "not found"). Results are cached for one hour; 10 checks per hour per IP.

Lessons from live testing (2026-11-14): a true ERR report published the same day was not yet in the index; an
exact-keyword search would have marked it "not found". Hence the lenient outlet search, the rule that zero
articles from the outlet means "could not check", and an automatic undated open-web fallback when the recent
14-day window returns zero hits on older claims or archived hoaxes.

## Clean the traffic (`/traffic` & `POST /api/traffic`)

Parses Nginx/Apache Combined & Common access logs, Caddy/Traefik structured JSON logs, or CSV exports in memory (up to 10,000 lines / 2 MB, never stored). Top candidate public IPs are enriched concurrently (24h SQLite cache) via:
- **Shodan InternetDB** (`internetdb.shodan.io/{ip}`): open ports, threat tags (`tor`, `scanner`, `vpn`, `proxy`, `compromised`, `cloud`), rDNS hostnames, and CVE vulnerabilities.
- **RIPE NCC RIPEstat** (`stat.ripe.net`): Autonomous System Number (ASN) and network holder, used to verify whether `Googlebot` (`AS15169`), `Bingbot` (`AS8075`), or `Applebot` (`AS714`) User-Agents actually originate from their official networks.
- **VirusTotal v3** (optional when `VIRUSTOTAL_API_KEY` is set): malicious/suspicious security-engine votes.

Actors are classified into `credential_stuffing`, `vuln_scanner`, `spoofed_crawler`, `ai_or_bulk_scraper`, `verified_crawler`, or `human_visitor`. Shared office/campus NAT IPs, browsers loading static CSS/JS/image assets, and ASN-verified search crawlers are protected from false blocks (`12/12` innocent controls protected in `eval/traffic/run_traffic_eval.py`). Ready-to-paste Hostinger `.htaccess`, Nginx, and Linux `ufw` block rules are generated only for `malicious` (`block`) actors.
