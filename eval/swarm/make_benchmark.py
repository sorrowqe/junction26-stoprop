"""Seeded synthetic Swarm benchmark generator (12 scenarios: 6 coordinated + 6 innocent)."""
from __future__ import annotations

import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.swarm.ingest import Post  # noqa: E402

T0 = 1_794_600_000.0
VOCAB = (
    "council budget harbour transit bridge school clinic park housing solar grid tariff library road "
    "winter repair ferry station schedule permit hearing committee vote district".split()
)


def _bg(rng: random.Random, n: int = 70, prefix: str = "user") -> list[Post]:
    return [
        Post(
            id=f"bg_{i}",
            author=f"{prefix}_{i}_{rng.choice(['anna', 'mark', 'liis', 'tom', 'katrin', 'jaan'])}",
            ts=T0 + i * 420.0 + rng.uniform(0, 90),
            text=" ".join(rng.sample(VOCAB, 11)) + ".",
            author_created_at=T0 - (200 + i * 15) * 86400,
        )
        for i in range(n)
    ]


def build_scenarios(seed: int = 2026) -> list[dict]:
    rng = random.Random(seed)
    msg_a = "City hall secretly signed away forty million euros of port revenue behind closed doors tonight."
    msg_b = "Ordinary families are paying the new winter grid surcharge while insiders pocket every single cent."
    sc = []

    # 6 Coordinated campaigns
    s1 = _bg(rng, 75, "reader") + [Post(f"c1_{k}", f"citizen_{k}x", T0 + 9000 + k * 18, msg_a) for k in range(8)]
    sc.append({"id": "coord_timing_burst", "kind": "coordinated", "label": "Synchronized 2-minute copy burst", "posts": s1})

    s2 = _bg(rng, 70, "member") + [
        Post(f"c2_{k}", f"harbour_truth_{120 + k * 17}", T0 + k * 2800, msg_a, author_created_at=T0 - 2 * 86400 + k * 3600)
        for k in range(6)
    ]
    sc.append({"id": "coord_account_pattern", "kind": "coordinated", "label": "Look-alike + newly created accounts", "posts": s2})

    s3 = _bg(rng, 70, "poster") + [
        Post(f"c3_{k}", f"independent_{k}_voice", T0 + k * 3100, f"{msg_a} https://port-leaks-exposed.org/files",
             url="https://port-leaks-exposed.org/files") for k in range(6)
    ]
    sc.append({"id": "coord_link_push", "kind": "coordinated", "label": "Coordinated non-mainstream link push", "posts": s3})

    slots = ["harbour contract", "port agreement", "dredging deal"]
    s4 = _bg(rng, 75, "local") + [
        Post(f"c4_{k}", f"patriot_watch{200 + k}", T0 + 12000 + k * 22,
             f"Why is nobody asking about the secret {slots[k % 3]} signed behind closed doors tonight without a public vote?")
        for k in range(7)
    ]
    sc.append({"id": "coord_template_near", "kind": "coordinated", "label": "Near-duplicate template burst", "posts": s4})

    s5 = _bg(rng, 60, "chan_misc") + [
        Post(f"c5_{k}", f"@baltic_front_{10 + k}", T0 + 6000 + k * 25,
             f"{msg_b} https://shadow-briefing.net/report", platform="telegram") for k in range(6)
    ]
    sc.append({"id": "coord_cross_channel", "kind": "coordinated", "label": "Cross-channel mirror wave", "posts": s5})

    s6 = _bg(rng, 80, "forum")
    for k in range(6):
        s6.append(Post(f"c6a_{k}", f"grid_alert{30 + k}", T0 + 5000 + k * 20, msg_a))
        s6.append(Post(f"c6b_{k}", f"grid_alert{30 + k}", T0 + 8000 + k * 20, msg_b))
    sc.append({"id": "coord_multi_message", "kind": "coordinated", "label": "Two-message campaign by same accounts", "posts": s6})

    # 6 Innocent / organic hard negatives
    sc.append({"id": "clean_organic_thread", "kind": "innocent", "label": "Organic neighbourhood discussion", "posts": _bg(rng, 90, "discuss")})

    s8 = _bg(rng, 70, "newsfan") + [
        Post(f"in8_{k}", f"reader_{k}_ee", T0 + k * 2400,
             "Parliament approves next year state budget after third reading https://news.err.ee/1609001/parliament-approves-state-budget",
             url="https://news.err.ee/1609001/parliament-approves-state-budget") for k in range(6)
    ]
    sc.append({"id": "clean_headline_sharing", "kind": "innocent", "label": "Mainstream news headline sharing", "posts": s8})

    s9 = _bg(rng, 70, "civic") + [
        Post(f"in9_{k}", f"observer_{k}_uk", T0 + k * 2600,
             'Prime Minister Kallas said: "We will invest heavily in grid resilience and regional defence this winter."')
        for k in range(5)
    ]
    sc.append({"id": "clean_quoted_statement", "kind": "innocent", "label": "Shared official quoted statement", "posts": s9})

    s10 = _bg(rng, 40, "fest")
    for k in range(18):
        s10.append(Post(f"ev_{k}", f"choirfan_{k}", T0 + 4000 + k * 60, " ".join(rng.sample(VOCAB, 9)) + " #SongFestival2026"))
    for k in range(5):
        s10.append(Post(f"evc_{k}", f"attendee_{k}", T0 + 4100 + k * 90,
                        "What an unforgettable final concert under the arch tonight with everyone singing #SongFestival2026"))
    sc.append({"id": "clean_event_hashtag", "kind": "innocent", "label": "Live-event hashtag crowd", "posts": s10})

    wire_txt = "Central bank policymakers kept benchmark interest rates unchanged at four percent following Thursday's meeting."
    s11 = _bg(rng, 60, "econ") + [
        Post("w0", "reuters.com", T0 + 1000, wire_txt, url="https://www.reuters.com/markets/rates-2026/"),
        *[Post(f"w{k}", f"regional_paper_{k}", T0 + 2500 * k, wire_txt, url=f"https://paper{k}.example/story") for k in range(1, 5)],
    ]
    sc.append({"id": "clean_wire_syndication", "kind": "innocent", "label": "News-agency wire syndication", "posts": s11})

    s12 = _bg(rng, 60, "polite") + [Post(f"sh_{k}", f"guest_{k}", T0 + 3000 + k * 15, "Thank you so much for sharing this today!") for k in range(8)]
    sc.append({"id": "clean_short_generic", "kind": "innocent", "label": "Short generic courtesy replies", "posts": s12})
    return sc
