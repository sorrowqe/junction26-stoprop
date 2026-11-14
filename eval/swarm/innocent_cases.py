"""20 innocent / hard-negative Swarm benchmark scenarios."""
from __future__ import annotations

import random

from app.swarm.ingest import Post

T0 = 1_794_600_000.0
VOCAB = (
    "council budget harbour transit bridge school clinic park housing solar grid tariff library road "
    "winter repair ferry station schedule permit hearing committee vote district market cycling river "
    "museum hospital zoning water heating rail tunnel".split()
)


def bg(rng: random.Random, n: int = 80, prefix: str = "user", step: float = 420.0) -> list[Post]:
    names = ("anna", "mark", "liis", "tom", "katrin", "jaan", "elena", "david", "sofia", "lucas")
    return [
        Post(f"{prefix}_b{i}", f"{prefix}_{i}_{rng.choice(names)}", T0 + i * step + rng.uniform(0, 95),
             " ".join(rng.sample(VOCAB, 12)) + ".", author_created_at=T0 - (180 + i * 12) * 86400)
        for i in range(n)
    ]


def build_innocent(rng: random.Random) -> list[dict]:
    wire = "Central bank policymakers kept benchmark interest rates unchanged at four percent following Thursday's meeting."
    h1, u1 = "Parliament approves next year state budget after third reading", "https://news.err.ee/1609001/budget"
    h2, u2 = "European Space Agency satellite enters orbit after Guiana launch", "https://www.bbc.com/news/science-101"
    h3, u3 = "Euro zone inflation eases to two percent in October flash estimate", "https://www.reuters.com/world/europe/cpi/"
    q1 = 'Prime Minister Kallas said: "We will invest heavily in grid resilience and regional defence this winter."'
    q2 = 'Governor Muller said: "Headline inflation should return toward two percent by late autumn."'
    items = [
        ("clean_01_organic_90", "Pure organic local forum thread (90 posts)", bg(rng, 90, "in01")),
        ("clean_02_organic_160", "Busy organic AMA thread (160 posts)", bg(rng, 160, "in02", 180.0)),
        ("clean_03_err_headline", "Readers sharing ERR public-broadcaster headline",
         bg(rng, 70, "in03") + [Post(f"i3_{k}", f"reader_{k}_ee", T0 + k * 2400, f"{h1} {u1}", url=u1) for k in range(6)]),
        ("clean_04_bbc_breaking", "Breaking BBC headline shared during fast news hour",
         bg(rng, 80, "in04") + [Post(f"i4_{k}", ["alice_w", "ben_k", "claire_m", "dan_p", "eva_r"][k], T0 + 7000 + k * 35, f"{h2} {u2}", url=u2) for k in range(5)]),
        ("clean_05_reuters_link", "Reuters market headline shared by 6 finance readers",
         bg(rng, 75, "in05") + [Post(f"i5_{k}", ["fin_ted", "macro_sue", "bond_jay", "fx_nina", "eq_omar", "rate_pia"][k], T0 + k * 2100, f"{h3} {u3}", url=u3) for k in range(6)]),
        ("clean_06_official_quote", "Attributed prime-minister press quote shared verbatim",
         bg(rng, 70, "in06") + [Post(f"i6_{k}", ["civic_a", "press_b", "note_c", "desk_d", "view_e"][k], T0 + k * 2600, q1) for k in range(5)]),
        ("clean_07_bank_quote", "Attributed central-bank governor quote",
         bg(rng, 70, "in07") + [Post(f"i7_{k}", ["econ_l", "bank_m", "news_n", "data_o", "chart_p"][k], T0 + 5000 + k * 40, q2) for k in range(5)]),
        ("clean_08_song_festival", "Live-event concert crowd (#SongFestival2026)",
         bg(rng, 45, "in08") + [Post(f"e8_{k}", f"fan_{k}_{k*3}", T0 + 4000 + k * 55, " ".join(rng.sample(VOCAB, 9)) + " #SongFestival2026") for k in range(20)]
         + [Post(f"e8c_{k}", ["mia_t", "karl_s", "ott_p", "anu_l", "priit_v"][k], T0 + 4100 + k * 85, "What an unforgettable final concert under the arch tonight with everyone singing #SongFestival2026") for k in range(5)]),
        ("clean_09_football_goal", "Live football match #UCLFinal crowd reactions",
         bg(rng, 40, "in09") + [Post(f"f9_{k}", f"sup_{k}", T0 + 3000 + k * 45, " ".join(rng.sample(VOCAB, 9)) + " #UCLFinal") for k in range(22)]
         + [Post(f"f9c_{k}", ["goal_dan", "fc_sara", "pitch_leo", "cup_max"][k], T0 + 3200 + k * 30, "Unbelievable equalizer in the eighty ninth minute of extra time tonight #UCLFinal") for k in range(4)]),
        ("clean_10_wire_syndication", "Reuters wire lead syndicated by 4 regional papers",
         bg(rng, 65, "in10") + [Post("w0", "reuters.com", T0 + 1000, wire, url="https://www.reuters.com/markets/rates/")]
         + [Post(f"w{k}", f"paper_{k}", T0 + 2500 * k, wire, url=f"https://paper{k}.example/s") for k in range(1, 5)]),
    ] + _more_innocent(rng)
    return [{"id": i, "kind": "innocent", "label": lbl, "posts": pts} for i, lbl, pts in items]


def _more_innocent(rng: random.Random) -> list[tuple[str, str, list[Post]]]:
    ap = "Coastal ferry operators resumed morning crossings Friday after gale warnings were lifted across the strait."
    h4, u4 = "Why European cities are redesigning waterfront transit around electric ferries", "https://www.theguardian.com/cities/ferries"
    q3 = 'Orwell wrote: "Political language is designed to make lies sound truthful and murder respectable."'
    return [
        ("clean_11_ap_syndication", "Associated Press weather report syndicated",
         bg(rng, 65, "in11") + [Post("ap0", "apnews.com", T0 + 800, ap, url="https://apnews.com/article/ferry")]
         + [Post(f"ap{k}", ["harbour_gaz", "island_rad", "coast_chron"][k - 1], T0 + 1800 * k, ap) for k in range(1, 4)]),
        ("clean_12_short_thanks", "Short courtesy replies (<8 words)",
         bg(rng, 65, "in12") + [Post(f"s12_{k}", f"guest_{k}", T0 + 3000 + k * 12, "Thank you so much for sharing this!") for k in range(10)]),
        ("clean_13_short_congrats", "Short congratulatory thread replies (<8 words)",
         bg(rng, 65, "in13") + [Post(f"s13_{k}", f"pal_{k}", T0 + 4000 + k * 10, "Huge congratulations to the entire team!") for k in range(9)]),
        ("clean_14_same_author_thread", "Single author posting a 10-part thread rapid-fire",
         bg(rng, 70, "in14") + [Post(f"t14_{k}", "journalist_martin", T0 + 6000 + k * 8, "Thread update on the municipal harbour transit budget vote happening right now in committee.") for k in range(10)]),
        ("clean_15_two_users_quoting", "Only 2 users quoting same sentence (<3 accounts)",
         bg(rng, 70, "in15") + [Post(f"p15_{k}", ["user_one", "user_two"][k], T0 + 5000 + k * 5, "The new cycling bridge across the harbour opens to pedestrians on Monday morning.") for k in range(2)]),
        ("clean_16_same_topic_unique", "10 citizens debating same topic in their own words",
         bg(rng, 60, "in16") + [Post(f"t16_{k}", f"res_{k}", T0 + 5000 + k * 25, " ".join(rng.sample(VOCAB, 13)) + " harbour budget.") for k in range(10)]),
        ("clean_17_guardian_column", "Guardian column link shared with headline",
         bg(rng, 70, "in17") + [Post(f"i17_{k}", ["rd_a", "rd_b", "rd_c", "rd_d", "rd_e"][k], T0 + k * 3300, f"{h4} {u4}", url=u4) for k in range(5)]),
        ("clean_18_book_quote", "Book-club readers sharing an attributed literary quote",
         bg(rng, 70, "in18") + [Post(f"i18_{k}", ["bk_a", "lt_b", "pg_c", "rd_d"][k], T0 + k * 4100, q3) for k in range(4)]),
        ("clean_19_slow_proverb_3", "3 unrelated veteran users sharing proverb over 2 days",
         bg(rng, 75, "in19") + [Post(f"i19_{k}", ["hiking_dad", "tea_lover", "jazz_cat"][k], T0 + k * 9500, "Measure twice and cut once before rushing into any ten year municipal infrastructure contract.", author_created_at=T0 - (500 + k * 300) * 86400) for k in range(3)]),
        ("clean_20_varied_link_reviews", "6 developers sharing release URL with distinct reviews",
         bg(rng, 65, "in20") + [Post(f"i20_{k}", ["dev_alex", "dev_sam", "dev_rob", "dev_cas", "dev_mor", "dev_ril"][k], T0 + 4000 + k * 900, f"{' '.join(rng.sample(VOCAB, 11))} https://example-oss.org/v4", url="https://example-oss.org/v4") for k in range(6)]),
    ]