"""Clusters, verdicts and innocent-pattern checks (CLAUDE.md §1 rule 5, §5). Verdicts are per cluster,
never per person.

Cluster = connected component over text-reuse pairs (T) between different authors.
Verdict:
  insufficient_data   < 3 distinct authors
  likely_coordinated  T + at least one independent family (S timing, A accounts, M amplification)
  worth_watching      exactly one family (T only)
Innocent patterns (headline sharing with a mainstream link, short/generic text, quoted attributed
statement, event-hashtag burst): with no S and no A -> organic_looking; otherwise downgrade one level.
"""
from __future__ import annotations

import asyncio
import re
import statistics
import time
import uuid
from collections import Counter, defaultdict

from app.pipeline import lexicons
from app.swarm import accounts, amplification as amp, explain, textreuse, timing
from app.swarm.ingest import Batch

VERDICT_ORDER = ("likely_coordinated", "worth_watching", "organic_looking", "insufficient_data")
MIN_AUTHORS = 3
HEADLINE_SHARE = 0.5
HEADLINE_MAX_MEDIAN_WORDS = 25
SHORT_MEDIAN_WORDS = 10
GENERIC_CONTENT_WORDS = 3
QUOTE_SHARE = 0.5
EVENT_TAG_SHARE = 0.5
EVENT_AUTHOR_RATIO = 3
EVENT_MIN_CLUSTERS = 3
EVENT_PAD_S = 3600
MAX_TAG_CLUSTERS = 10
MAX_TIMELINE = 300
_QUOTES = re.compile(r"[\"“”„«»]")
_SLUG_STOP = {"news", "article", "articles", "html", "htm", "story", "stories", "www", "index", "php", "amp",
              "live", "world", "politics", "business", "local", "uk", "us", "en", "com"}
STOPWORDS = set("""a an the and or but if then so of to in on at for from by with about as is are was were be been
being it its this that these those i you he she we they me him her us them my your our their not no yes do does did
have has had will would can could should just very really all any some more most so too what who how when where why
there here up down out over again only own same than s t much many lot""".split())
# phatic / courtesy vocabulary: posts made only of these are "generic" ("Thank you so much for sharing this today")
PHATIC = set("""thank thanks thx sharing share shared please love great good nice awesome amazing agree agreed
happy morning evening night everyone everybody all today tonight friends folks guys congrats congratulations
welcome hello hi hey wow yes true same well said""".split())


def components(n: int, pairs: list) -> list[list[int]]:
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    used = set()
    for p in pairs:
        parent[find(p.a)] = find(p.b)
        used.update((p.a, p.b))
    groups: dict[int, list[int]] = defaultdict(list)
    for i in sorted(used):
        groups[find(i)].append(i)
    return list(groups.values())


def verdict(n_authors: int, s: bool, a: bool, m: bool, innocent: bool) -> tuple[str, str]:
    """(verdict, verdict before innocent-pattern downgrade)."""
    if n_authors < MIN_AUTHORS:
        return "insufficient_data", "insufficient_data"
    base = "likely_coordinated" if (s or a or m) else "worth_watching"
    if not innocent:
        return base, base
    if not s and not a:
        return "organic_looking", base
    return VERDICT_ORDER[VERDICT_ORDER.index(base) + 1], base


# ------------------------------------------------------------- innocent checks

def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def headline_sharing(posts: list, rep_norm: str, word_counts: list[int]) -> str | None:
    mainstream = Counter()
    with_link = 0
    for p in posts:
        urls = [u for u in amp.items(p)["url"] if amp.is_mainstream(u)]
        with_link += bool(urls)
        mainstream.update(urls)
    if not mainstream or with_link < HEADLINE_SHARE * len(posts):
        return None
    url = mainstream.most_common(1)[0][0]
    slug = {w for w in re.findall(r"[a-z]{3,}", url.split("/", 1)[1].lower() if "/" in url else "") if w not in _SLUG_STOP}
    overlap = len(slug & set(_words(rep_norm))) / len(slug) if slug else None
    if (overlap is not None and overlap >= 0.5) or statistics.median(word_counts) <= HEADLINE_MAX_MEDIAN_WORDS:
        return url.split("/", 1)[0]
    return None


def short_generic(rep_norm: str, word_counts: list[int]) -> str | None:
    med = statistics.median(word_counts)
    if med <= SHORT_MEDIAN_WORDS:
        return f"median {med:g} words"
    content = {w for w in _words(rep_norm) if w not in STOPWORDS and w not in PHATIC and not w.isdigit()}
    if len(content) <= GENERIC_CONTENT_WORDS:
        return f"only {len(content)} distinct content words"
    return None


def quoted_statement(posts: list) -> str | None:
    quoted = sum(bool(_QUOTES.search(p.text)) and bool(lexicons.find("*", "speech", p.text)) for p in posts)
    if quoted >= QUOTE_SHARE * len(posts):
        return f"{quoted} of {len(posts)} posts"
    return None


def event_hashtag(cluster: dict, tag_index: dict[str, list[tuple[float, str]]], all_clusters: list[dict]) -> str | None:
    posts = cluster["_posts"]
    counts = Counter(t for p in posts for t in amp.items(p)["hashtag"])
    start, end = cluster["_start"] - EVENT_PAD_S, cluster["_end"] + EVENT_PAD_S
    for tag, n in counts.most_common():
        if n < EVENT_TAG_SHARE * len(posts):
            break
        users = {a for ts, a in tag_index.get(tag, []) if start <= ts <= end}
        others = sum(1 for c in all_clusters if c is not cluster and tag in c["_tags"]
                     and c["_start"] <= end and c["_end"] >= start)
        if len(users) >= EVENT_AUTHOR_RATIO * cluster["author_count"] or others + 1 >= EVENT_MIN_CLUSTERS:
            return tag
    return None


# ------------------------------------------------------------------ main

async def analyze_batch(batch: Batch, *, deep: bool = False, tag_clusters: bool = False,
                        embed=None, tagger=None) -> dict:
    """embed(texts) -> (vectors, meta) and tagger(text) -> (labels, meta) are injected (LLM calls)."""
    t0 = time.monotonic()
    posts = batch.posts
    notes = ["Processed in memory; nothing from this upload was stored."]
    cost = {"embeddings": 0, "llm_calls": 0, "input_tokens": 0, "output_tokens": 0, "credits": 0.0}

    pairs, norm = await asyncio.to_thread(textreuse.find_pairs, posts)  # CPU-bound: keep the event loop free
    word_counts = [textreuse.word_count(t) for t in norm]
    eligible = sum(w >= textreuse.MIN_WORDS for w in word_counts)
    deep_info = None
    if deep:
        if embed is None:
            notes.append("Deep mode (paraphrase detection) is not available on this server right now; "
                         "results use exact and near-identical text only.")
        else:
            try:
                para, deep_info = await textreuse.paraphrase_pairs(posts, norm, {(p.a, p.b) for p in pairs}, embed)
                pairs += para
                cost["embeddings"] = deep_info.get("embeddings", 0)
                cost["credits"] += deep_info.get("credits", 0.0)
                if deep_info.get("capped"):
                    notes.append(f"Deep mode compared only the first {textreuse.MAX_EMBEDDINGS} distinct posts.")
            except Exception as exc:  # noqa: BLE001 - deep mode is optional, fail soft
                notes.append(f"Deep mode failed ({type(exc).__name__}); results use exact and near-identical text only.")

    rate = timing.dataset_rate([p.ts for p in posts])
    posts_per_author = Counter(p.author.lower() for p in posts)
    created: dict[str, float] = {}
    by_author_ts: dict[str, list[float]] = defaultdict(list)
    tag_index: dict[str, list[tuple[float, str]]] = defaultdict(list)
    for p in posts:
        a = p.author.lower()
        by_author_ts[a].append(p.ts)
        if p.author_created_at is not None:
            created.setdefault(a, p.author_created_at)
        for tag in amp.items(p)["hashtag"]:
            tag_index[tag].append((p.ts, a))

    degree = Counter()
    kinds_by_post: dict[int, Counter] = defaultdict(Counter)
    for p in pairs:
        degree[p.a] += 1
        degree[p.b] += 1
        kinds_by_post[p.a][p.kind] += 1

    clusters = []
    for comp in components(len(posts), pairs):
        cp = sorted((posts[i] for i in comp), key=lambda x: x.ts)
        idx = sorted(comp, key=lambda i: posts[i].ts)
        authors_order = list(dict.fromkeys(p.author.lower() for p in cp))
        in_cluster = Counter(p.author.lower() for p in cp)
        kinds = Counter()
        for i in comp:
            kinds.update(kinds_by_post[i])
        rep_i = max(idx, key=lambda i: (degree[i], -posts[i].ts))
        sync = timing.synchrony(cp, rate)
        regular = []
        for a in authors_order:
            cad = timing.cadence(by_author_ts[a])
            if cad and cad["regular"]:
                regular.append({"author": a, **cad})
        sync["regular_cadence"] = regular
        acc = accounts.similarity(authors_order, created, posts_per_author, in_cluster)
        am = amp.amplification(cp)
        clusters.append({
            "_posts": cp, "_start": cp[0].ts, "_end": cp[-1].ts,
            "_tags": {t for p in cp for t in amp.items(p)["hashtag"]},
            "_rep_norm": norm[rep_i], "_wc": [word_counts[i] for i in comp],
            "author_count": len(authors_order), "post_count": len(cp),
            "representative_text": posts[rep_i].text,
            "time_span": {"start": cp[0].ts, "end": cp[-1].ts, "seconds": round(cp[-1].ts - cp[0].ts, 1)},
            "signals": {"T": {"present": True, "pairs": dict(kinds),
                              "paraphrase_only": not kinds.get("exact") and not kinds.get("near")},
                        "S": sync, "A": acc, "M": am},
            "authors": [{"author": next(p.author for p in cp if p.author.lower() == a),
                         "role": "first poster" if k == 0 else "amplifier",
                         "posts_in_cluster": in_cluster[a], "posts_in_batch": posts_per_author[a],
                         "first_post": min(p.ts for p in cp if p.author.lower() == a),
                         "name_pattern": a in set(acc["usernames"]["group"]) and acc["usernames"]["present"]}
                        for k, a in enumerate(authors_order)],
            "timeline": [{"t": p.ts, "author": p.author, "id": p.id} for p in cp[:MAX_TIMELINE]],
        })

    for c in clusters:
        innocent = {}
        detail = headline_sharing(c["_posts"], c["_rep_norm"], c["_wc"])
        if detail:
            innocent["headline_sharing"] = detail
        detail = short_generic(c["_rep_norm"], c["_wc"])
        if detail:
            innocent["short_generic"] = detail
        detail = quoted_statement(c["_posts"])
        if detail:
            innocent["quoted_statement"] = detail
        detail = event_hashtag(c, tag_index, clusters)
        if detail:
            innocent["event_hashtag"] = detail
        sig = c["signals"]
        c["verdict"], c["verdict_before_innocent_check"] = verdict(
            c["author_count"], sig["S"]["present"], sig["A"]["present"], sig["M"]["present"], bool(innocent))
        c["families"] = ["T"] + [f for f in ("S", "A", "M") if sig[f]["present"]]
        c["innocent_patterns"] = sorted(innocent)
        c["innocent_explanations"] = [explain.INNOCENT_TEXT[k].format(detail=v) for k, v in innocent.items()]
        c["headline"] = explain.headline(c["author_count"], c["post_count"], sig["T"]["pairs"], c["time_span"]["seconds"])
        c["evidence_for"], c["evidence_against"] = explain.evidence(c)
        if sig["T"]["paraphrase_only"]:
            c["evidence_against"].append("The text match is only meaning-level (paraphrase), which is weaker evidence "
                                         "than copied text.")

    clusters.sort(key=lambda c: (VERDICT_ORDER.index(c["verdict"]), -c["author_count"], c["_start"]))
    for n, c in enumerate(clusters, start=1):
        c["id"] = f"c{n}"
    # the same accounts behind several message clusters (context only, not a signal family)
    author_sets = [{a["author"].lower() for a in c["authors"]} for c in clusters]
    for i, c in enumerate(clusters):
        shared = [{"cluster": clusters[j]["id"], "authors": len(author_sets[i] & author_sets[j])}
                  for j in range(len(clusters)) if j != i and len(author_sets[i] & author_sets[j]) >= MIN_AUTHORS]
        c["shares_accounts_with"] = shared
        if shared:
            c["evidence_for"].append("The same accounts also posted other repeated messages: " + ", ".join(
                f"{s['authors']} of them in cluster {s['cluster']}" for s in shared) + ".")

    if tag_clusters and tagger is not None:
        flagged = [c for c in clusters if c["verdict"] in ("likely_coordinated", "worth_watching")][:MAX_TAG_CLUSTERS]
        for c in flagged:
            try:
                labels, meta = await tagger(c["representative_text"])
            except Exception:  # noqa: BLE001 - optional, fail soft
                labels, meta = None, {}
            c["techniques"] = labels
            for k in ("llm_calls", "input_tokens", "output_tokens"):
                cost[k] += meta.get(k, 0)
            cost["credits"] += meta.get("credits", 0.0)
    elif tag_clusters:
        notes.append("Technique tagging is not available on this server right now.")

    out_clusters = [{k: v for k, v in c.items() if not k.startswith("_")} for c in clusters]
    by_verdict = Counter(c["verdict"] for c in clusters)
    ts = [p.ts for p in posts]
    short = len(posts) - eligible
    if short:
        notes.append(f"{short} posts have fewer than {textreuse.MIN_WORDS} words; short texts collide innocently, "
                     "so they are not used as text-reuse evidence.")
    cost["credits"] = round(cost["credits"], 4)
    return {
        "id": "s_" + uuid.uuid4().hex[:10],
        "summary": {"posts": len(posts), "authors": len(posts_per_author), "rows": batch.rows_total,
                    "rejected": batch.rejected_total, "eligible_posts": eligible, "clusters": len(clusters),
                    "by_verdict": {v: by_verdict.get(v, 0) for v in VERDICT_ORDER},
                    "time_span": {"start": min(ts), "end": max(ts)} if ts else None},
        "clusters": out_clusters,
        "rejected": batch.rejected,
        "options": {"deep": deep, "tag_clusters": tag_clusters},
        "deep": deep_info,
        "cost": cost,
        "notes": notes,
        "elapsed_ms": round((time.monotonic() - t0) * 1000),
    }
