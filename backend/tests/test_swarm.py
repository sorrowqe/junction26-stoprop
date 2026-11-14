"""Swarm mode (CLAUDE.md §5): ingest, every signal family, verdict rules, every innocent pattern, API.
Small handcrafted fixtures; no network, no model calls (deep/tag use injected fakes)."""
import asyncio
import json
import random
import time

import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app
from app.swarm import accounts, amplification as amp, clusters, explain, ingest, textreuse, timing
from app.swarm.ingest import Post

client = TestClient(app)
T0 = 1_794_300_000.0  # 2026-11-11
LONG = "The energy levy is a scam to fund the council's friends and nobody will tell you about it"


def post(i, author, t, text, url=None, created=None):
    return Post(id=str(i), author=author, ts=T0 + t, text=text, url=url, author_created_at=created)


def run(posts, **kw):
    return asyncio.run(clusters.analyze_batch(ingest.Batch(posts=posts, rows_total=len(posts)), **kw))


def background(n=60, start=0, hours=48, seed=1):
    """Unrelated filler posts spread over two days (sets the batch's normal posting pace)."""
    rng = random.Random(seed)
    words = ("apple river window garden yellow music station winter coffee bridge market silver forest "
             "engine paper mountain candle letter orange harbour ticket blanket violin pepper lantern").split()
    return [post(f"bg{start + k}", f"person_{chr(97 + k % 26)}{k}", rng.uniform(0, hours * 3600),
                 " ".join(rng.sample(words, 10))) for k in range(n)]


# ------------------------------------------------------------------ ingest

def test_ingest_csv_with_aliases_bom_and_row_report():
    text = ("﻿id,username,created_at,content,url\n"
            "1,alice,2026-11-14T10:00:00Z,Hello there,\n"
            "2,bob,1794650000,Unix seconds,https://x.example/a\n"
            "3,carol,1794650000123,Unix milliseconds,\n"
            "4,dave,not a time,Bad time,\n"
            "1,erin,2026-11-14T10:00:00Z,Duplicate id,\n"
            "6,,2026-11-14T10:00:00Z,No author,\n")
    b = ingest.parse_text(text)
    assert [p.id for p in b.posts] == ["1", "2", "3"]
    assert b.posts[2].ts == pytest.approx(1794650000.123)
    assert b.rejected == [{"row": 4, "reason": "timestamp is not ISO 8601 or unix time"},
                          {"row": 5, "reason": "duplicate id 1"}, {"row": 6, "reason": "missing author"}]
    assert b.rows_total == 6 and b.rejected_total == 3


def test_ingest_json_forms_and_naive_time_is_utc():
    rows = [{"id": 1, "author": "@a", "timestamp": "2026-11-14 10:00:00", "text": "x"}]
    for data in (rows, {"posts": rows}):
        b = ingest.parse_text(json.dumps(data))
        assert b.posts[0].author == "a" and b.posts[0].ts == ingest.parse_time("2026-11-14T10:00:00+00:00")


@pytest.mark.parametrize("text, msg", [
    ("", "empty"), ("[1, 2", "Not valid JSON"), ('{"x": 1}', "list of posts"),
    ("name,when\nx,y\n", "missing: id, author, timestamp, text"),
])
def test_ingest_unusable_uploads(text, msg):
    with pytest.raises(ingest.IngestError, match=msg):
        ingest.parse_text(text)


def test_ingest_limits():
    rows = [{"id": i, "author": "a", "timestamp": 1794650000 + i, "text": "t"} for i in range(ingest.MAX_POSTS + 1)]
    with pytest.raises(ingest.IngestError, match="2000"):
        ingest.from_json(rows)
    with pytest.raises(ingest.IngestError, match="2 MB"):
        ingest.parse_text("id,author,timestamp,text\n" + "x" * (ingest.MAX_BYTES + 1))


# -------------------------------------------------------------- T text reuse

def test_text_reuse_exact_near_short_and_same_author():
    posts = [post(1, "a", 0, LONG + " https://one.example/x"), post(2, "b", 10, LONG + " https://two.example/y @someone"),
             post(3, "c", 20, LONG.replace("scam", "fraud")),            # near-identical
             post(4, "d", 30, "so true!"), post(5, "e", 40, "so true!"),  # short: never evidence
             post(6, "a", 50, "A completely different post about the weather in the harbour today, nothing else")]
    pairs, norm = textreuse.find_pairs(posts)
    kinds = {(p.a, p.b): p.kind for p in pairs}
    assert kinds[(0, 1)] == "exact"            # URLs and @mentions are ignored for comparison
    assert any(k == "near" and 2 in pair for pair, k in kinds.items())
    assert not any(3 in pair or 4 in pair or 5 in pair for pair in kinds)
    # same author copying themselves is not cross-account reuse
    same = textreuse.find_pairs([post(1, "a", 0, LONG), post(2, "A", 5, LONG)])[0]
    assert same == []


def test_jaccard_threshold():
    a = textreuse.shingles(textreuse.compare_text(LONG))
    b = textreuse.shingles(textreuse.compare_text("Totally unrelated: the museum opens a new photography exhibition next week"))
    assert textreuse.jaccard(a, a) == 1.0 and textreuse.jaccard(a, b) < 0.2


def test_paraphrase_pairs_with_fake_embeddings():
    texts = ["The council is secretly funding its friends with the new energy levy money, wake up people",
             "With the fresh energy levy cash the council quietly bankrolls its own friends, open your eyes",
             "Our cat spent the whole day asleep on the warm radiator again, as usual in November"]
    posts = [post(i, f"u{i}", i, t) for i, t in enumerate(texts)]
    norm = [textreuse.compare_text(t) for t in texts]

    async def embed(batch):
        vecs = {norm[0]: [1.0, 0.0, 0.1], norm[1]: [0.98, 0.05, 0.12], norm[2]: [0.0, 1.0, 0.0]}
        return [vecs[t] for t in batch], {"credits": 0.01}
    pairs, info = asyncio.run(textreuse.paraphrase_pairs(posts, norm, set(), embed))
    assert [(p.a, p.b, p.kind) for p in pairs] == [(0, 1, "paraphrase")] and info["embeddings"] == 3


# ---------------------------------------------------------------- S timing

def test_synchrony_burst_vs_spread():
    rate = timing.dataset_rate([T0, T0 + 48 * 3600] + [T0 + k * 3600 for k in range(100)])
    burst = [post(i, f"acc{i}", 30 * i, LONG) for i in range(8)]                 # 8 posts in 4 minutes
    spread = [post(i, f"acc{i}", 6 * 3600 * i, LONG) for i in range(8)]          # every 6 hours
    s = timing.synchrony(burst, rate)
    assert s["present"] and s["p"] < 0.001 and s["authors_in_window"] >= 3
    assert not timing.synchrony(spread, rate)["present"]


def test_synchrony_needs_three_authors():
    rate = timing.dataset_rate([T0, T0 + 48 * 3600])
    two = [post(i, "a" if i % 2 else "b", 10 * i, LONG) for i in range(8)]
    assert not timing.synchrony(two, rate)["present"]


def test_cadence_regular_vs_irregular():
    assert timing.cadence([T0 + 600 * k for k in range(6)])["regular"] is True
    assert timing.cadence([T0, T0 + 60, T0 + 4000, T0 + 4100, T0 + 20000, T0 + 20050])["regular"] is False
    assert timing.cadence([T0, T0 + 60]) is None


# -------------------------------------------------------------- A accounts

def test_username_patterns():
    assert accounts.split_username("Baltic_Truth481") == ("baltic_truth", "481")
    assert accounts.split_username("maria_k") == ("maria_k", "")
    groups = accounts.username_groups(["baltic_truth_481", "baltictruth_22", "baltic_trut_9001", "maria_k", "ann_12", "dan_99"])
    assert sorted(groups[0]) == ["baltic_trut_9001", "baltic_truth_481", "baltictruth_22"]
    assert ["ann_12", "dan_99"] not in groups  # short stems must match exactly
    assert accounts.levenshtein("kitten", "sitting") == 3


def test_account_similarity_signals():
    authors = ["x1", "y2", "z3", "w4"]
    created = {"x1": T0, "y2": T0 + 86400, "z3": T0 + 3 * 86400, "w4": T0 - 900 * 86400}
    sig = accounts.similarity(authors, created, {"x1": 4, "y2": 3, "z3": 5, "w4": 9}, {"x1": 4, "y2": 3, "z3": 4, "w4": 1})
    assert sig["creation"]["present"] and sig["creation"]["within_7_days"] == 3
    assert sig["single_purpose"]["present"] and sorted(sig["single_purpose"]["authors"]) == ["x1", "y2", "z3"]
    assert not sig["usernames"]["present"] and sig["present"]
    quiet = accounts.similarity(authors, {}, {a: 1 for a in authors}, {a: 1 for a in authors})
    assert not quiet["present"]  # one post each: not "single-purpose", no dates


# ----------------------------------------------------------- M amplification

def test_amplification_fringe_vs_mainstream():
    fringe = [post(i, f"u{i}", i, f"read this https://levy-truth.info/proof?utm_source=x{i} #StopTheLevy") for i in range(5)]
    out = amp.amplification(fringe)
    assert out["present"] and {"kind": "url", "value": "levy-truth.info/proof", "authors": 5} in out["pushed"]
    main = [post(i, f"u{i}", i, "news https://www.bbc.com/news/world-123") for i in range(6)]
    out = amp.amplification(main)
    assert not out["present"] and out["mainstream"][0]["value"] == "bbc.com/news/world-123"
    assert not amp.amplification(fringe[:4])["present"]  # needs >= 5 accounts


# ------------------------------------------------------------- verdict rules

@pytest.mark.parametrize("n, s, a, m, innocent, expected", [
    (2, True, True, True, False, "insufficient_data"),
    (5, False, False, False, False, "worth_watching"),
    (5, True, False, False, False, "likely_coordinated"),
    (5, False, False, True, False, "likely_coordinated"),
    (5, False, False, True, True, "organic_looking"),      # innocent and no S/A
    (5, True, False, False, True, "worth_watching"),       # innocent but timing: one level down
    (5, False, True, True, True, "worth_watching"),
    (5, False, False, False, True, "organic_looking"),
])
def test_verdict_rules(n, s, a, m, innocent, expected):
    assert clusters.verdict(n, s, a, m, innocent)[0] == expected


# ---------------------------------------------------------- innocent patterns

def test_innocent_headline_sharing():
    url = "https://www.bbc.com/news/world-europe-council-approves-new-wind-farm-saaremaa"
    posts = [post(i, f"u{i}", 60 * i, f"Council approves new wind farm off Saaremaa coast after two-year review {url}")
             for i in range(6)]
    rep = textreuse.compare_text(posts[0].text)
    assert clusters.headline_sharing(posts, rep, [11] * 6) == "bbc.com"
    fringe = [post(i, f"u{i}", 60 * i, "Council approves new wind farm https://windtruth.example/x") for i in range(6)]
    assert clusters.headline_sharing(fringe, rep, [11] * 6) is None


def test_innocent_short_or_generic():
    assert clusters.short_generic("so true", [2, 2, 3]) == "median 2 words"
    assert clusters.short_generic("thank you so much for sharing this with all of us today", [12, 12, 12])
    assert clusters.short_generic(textreuse.compare_text(LONG), [17, 17, 17]) is None


def test_innocent_quoted_statement():
    quoted = [post(i, f"u{i}", i, '"We will keep the levy until 2028," the finance minister said on Monday.') for i in range(4)]
    assert clusters.quoted_statement(quoted) == "4 of 4 posts"
    assert clusters.quoted_statement([post(1, "u", 1, LONG)]) is None


def test_innocent_event_hashtag_burst_end_to_end():
    posts = background(40)
    t = 30 * 3600
    cheer = "Tonight's winner absolutely deserves it, what a performance from start to finish #EurovisionFinal"
    posts += [post(f"c{i}", f"fan{i}", t + 600 * i, cheer) for i in range(4)]
    rng = random.Random(3)
    vocab = "stage song voice lights dance jury televote host flag dress violin drums ballad chorus crowd".split()
    posts += [post(f"o{i}", f"viewer{i}", t + 200 * i, " ".join(rng.sample(vocab, 9)) + " #EurovisionFinal")
              for i in range(14)]
    out = run(posts)
    c = next(c for c in out["clusters"] if "eurovision" in c["representative_text"].lower())
    assert c["innocent_patterns"] == ["event_hashtag"] and c["verdict"] == "organic_looking"
    assert any("Event hashtag burst" in x for x in c["innocent_explanations"])


# ------------------------------------------------------------- end to end

def test_campaign_is_likely_coordinated_with_behaviour_wording():
    posts = background(80)
    camp = [post(f"k{i}", f"baltic_truth_{100 + 37 * i}", 20 * 3600 + 25 * i,
                 LONG + " https://levy-truth.info/proof #StopTheLevy", created=T0 - 3 * 86400 + 3600 * i) for i in range(10)]
    out = run(posts + camp)
    c = out["clusters"][0]
    assert c["verdict"] == "likely_coordinated" and set(c["families"]) == {"T", "S", "A", "M"}
    assert c["headline"] == "10 accounts posted identical text within 4 minutes."
    assert c["authors"][0]["role"] == "first poster" and c["authors"][1]["role"] == "amplifier"
    blob = json.dumps(out).lower()
    for word in ("bot", "troll", "propagandist", "fake account", "liar"):
        assert word not in blob


def test_template_campaign_needs_second_family():
    posts = background(80)
    deals = ["the harbour deal", "the port contract", "the harbour contract"]
    camp = [post(f"t{i}", f"person{i}x", 6 * 3600 * i, f"Why is nobody talking about {deals[i % 3]}? The mayor signed it "
                 "in secret and the money went abroad. Ask questions now") for i in range(6)]
    c = run(posts + camp)["clusters"][0]
    assert c["verdict"] == "worth_watching" and c["families"] == ["T"]   # text reuse alone is never "likely"
    assert any("Timing looks ordinary" in x for x in c["evidence_against"])


def test_sample_dataset_verdicts():
    text = (config.WEB_DIR / "static" / "swarm_sample.csv").read_text(encoding="utf-8")
    out = run(ingest.parse_text(text).posts)
    by = {}
    for c in out["clusters"]:
        by.setdefault(c["verdict"], []).append(c)
    assert len(by["likely_coordinated"]) >= 2
    assert any("headline_sharing" in c["innocent_patterns"] for c in out["clusters"])
    assert any("event_hashtag" in c["innocent_patterns"] and c["verdict"] == "organic_looking" for c in out["clusters"])
    assert any("short_generic" in c["innocent_patterns"] and c["verdict"] == "organic_looking" for c in out["clusters"])
    assert all(c["verdict"] != "likely_coordinated" for c in out["clusters"] if c["innocent_patterns"])


def test_tag_clusters_is_capped_at_ten():
    posts = background(30)
    rng = random.Random(5)
    vocab = [f"topic{n}" for n in range(400)]
    for k in range(12):
        text = " ".join(rng.sample(vocab, 12))  # 12 unrelated messages, each copied by 5 accounts
        posts += [post(f"g{k}-{i}", f"grp{k}_{i}", 10 * 3600 + k * 1000 + 20 * i, text) for i in range(5)]
    calls = []

    async def tagger(text):
        calls.append(text)
        return ["loaded_language"], {"llm_calls": 1, "credits": 0.1}
    out = run(posts, tag_clusters=True, tagger=tagger)
    assert len(calls) == clusters.MAX_TAG_CLUSTERS == 10 and out["cost"]["llm_calls"] == 10
    assert out["clusters"][0]["techniques"] == ["loaded_language"]


def test_duration_wording():
    assert explain.duration(30) == "30 seconds" and explain.duration(360) == "6 minutes"
    assert explain.duration(3 * 3600) == "3 hours" and explain.duration(3 * 86400) == "3 days"


def test_two_thousand_posts_fast_enough():
    rng = random.Random(7)
    vocab = [f"w{k}" for k in range(3000)]
    posts = [post(i, f"user{i % 700}", rng.uniform(0, 7 * 86400), " ".join(rng.sample(vocab, 14))) for i in range(1950)]
    posts += [post(f"c{i}", f"camp_{i}", 3 * 86400 + 20 * i, LONG) for i in range(50)]
    t = time.monotonic()
    out = run(posts)
    assert time.monotonic() - t < 20 and out["summary"]["posts"] == 2000
    assert out["clusters"][0]["author_count"] == 50


# -------------------------------------------------------------------- API

CSV = "id,author,timestamp,text\n" + "\n".join(
    f'{i},acc_{i},2026-11-14T10:0{i}:00Z,"{LONG}"' for i in range(6))


def test_api_json_and_raw_csv():
    r = client.post("/api/swarm", json={"data": CSV})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["summary"]["posts"] == 6 and body["clusters"][0]["author_count"] == 6
    r2 = client.post("/api/swarm", content=CSV.encode(), headers={"Content-Type": "text/csv"})
    assert r2.status_code == 200 and r2.json()["summary"]["posts"] == 6
    assert client.post("/api/swarm", json={"posts": [{"id": 1, "author": "a", "timestamp": 1794650000, "text": "x"}]}).status_code == 200


def test_api_errors_do_not_consume_rate_limit():
    for _ in range(12):
        assert client.post("/api/swarm", json={"data": "nonsense"}).status_code == 400
    assert client.post("/api/swarm", content=b"x" * (ingest.MAX_BYTES + 70000),
                       headers={"Content-Type": "text/csv"}).status_code == 413
    bad = client.post("/api/swarm", json={"data": "id,author,timestamp,text\n1,a,never,x\n"})
    assert bad.status_code == 400 and "row 1: timestamp" in bad.json()["detail"]
    assert client.post("/api/swarm", json={"data": CSV}).status_code == 200


def test_api_rate_limit_10_per_hour():
    for _ in range(10):
        assert client.post("/api/swarm", json={"data": CSV}, headers={"X-Forwarded-For": "203.0.113.50"}).status_code == 200
    r = client.post("/api/swarm", json={"data": CSV}, headers={"X-Forwarded-For": "203.0.113.50"})
    assert r.status_code == 429 and "swarm runs" in r.json()["detail"]


def test_api_deep_without_key_falls_back_with_note():
    body = client.post("/api/swarm", json={"data": CSV, "deep": True, "tag_clusters": True}).json()
    assert body["options"] == {"deep": True, "tag_clusters": True} and body["cost"]["embeddings"] == 0
    assert any("Deep mode" in n for n in body["notes"]) and any("tagging is not available" in n for n in body["notes"])


def test_api_deep_with_fake_embeddings(monkeypatch):
    from app.llm import client as llm
    monkeypatch.setenv("ROUTER_API_KEY", "sk-test-not-real")
    seen = []

    async def fake_embed(texts, *, model):
        seen.append(model)
        return [[1.0, 0.0] for _ in texts], {"credits": 0.02, "input_tokens": 10, "calls": 1}
    monkeypatch.setattr(llm, "embed", fake_embed)
    texts = ["The levy money quietly ends up with the council's friends, and nobody is asking why",
             "Council insiders are pocketing the energy levy cash while ordinary families pay the bill",
             "Follow the money: every euro of the new levy flows to companies close to city hall",
             "They call it an energy levy but it is really a gift to friends of the council",
             "Your levy payments are funding the mayor's allies, not cleaner power for this town",
             "Ask yourself who profits from the levy: the council's business partners, not you"]
    rows = "\n".join(f'{i},acc_{i},2026-11-14T10:0{i}:00Z,"{t}"' for i, t in enumerate(texts))
    body = client.post("/api/swarm", json={"data": "id,author,timestamp,text\n" + rows, "deep": True}).json()
    assert seen == ["text-embedding-3-small-eu"] and body["cost"]["embeddings"] == 6
    assert any(c["signals"]["T"]["pairs"].get("paraphrase") for c in body["clusters"])


def test_swarm_page_and_sample_served():
    page = client.get("/swarm").text
    assert "Paste a link" in page and "Try an example" in page and "/static/swarm.js" in page
    assert client.get("/static/swarm_sample.csv").text.startswith("id,author,timestamp,text")
    snap = client.get("/static/examples/bluesky_thread.json").json()
    assert snap["source"]["platform"] == "bluesky" and "pseudonyms" in snap["source"]["note"]
    assert all(p["text"].strip() for p in snap["posts"])
    out = client.post("/api/swarm", json={"data": json.dumps(snap)}).json()
    assert out["summary"]["rejected"] == 0 and out["summary"]["posts"] == len(snap["posts"])
