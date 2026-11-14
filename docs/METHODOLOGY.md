# Methodology

The full write-up (pipeline, principles, caching, privacy, evaluation protocol, why there is no
AI-generated-text detection, known limitations) is completed in M6. This file already documents the
swarm-mode data sources.

## Swarm mode: where the posts come from

Swarm mode analyses a batch of posts. The detection never knows how the batch arrived: every input
becomes the same list of posts (id, author, time, text, optional link and account creation date).

| Input | How Stoprop reads it | What we get | Limits |
|---|---|---|---|
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

### What we cannot access, and why

- **X (Twitter)**: no free public access to posts and replies; reading requires a paid developer account and login.
- **Facebook, Instagram, Threads (Meta)**: no access to posts and comments without login and approved app access.
- **TikTok**: the research API is open only to approved academic researchers.
- **Reddit**: requires registered API access and blocks anonymous automated reading.

For these platforms users can export posts themselves (or use a research data export) and upload a CSV/JSON
file. We do not scrape logged-in pages or work around access rules.

### Campaigns

Clusters are formed per message (copied or near-identical text). When the author sets of two or more
clusters overlap strongly (Jaccard ≥ 0.5), they are shown as one **campaign** with its message variants.
Merging does not change any verdict: the campaign shows its strongest variant's verdict, and each
variant's evidence is listed.
