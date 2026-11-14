"""Seeded 40-scenario Swarm stress benchmark (20 coordinated + 20 innocent controls)."""
from __future__ import annotations

import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(HERE), str(ROOT / "backend")]

from app.swarm.ingest import Post  # noqa: E402
from innocent_cases import T0, bg, build_innocent  # noqa: E402


def build_scenarios(seed: int = 2026) -> list[dict]:
    rng = random.Random(seed)
    ma = "City hall secretly signed away forty million euros of port revenue behind closed doors tonight."
    mb = "Ordinary families are paying the new winter grid surcharge while insiders pocket every single cent."
    mc = "Independent auditors were locked out of the municipal transit tender before Friday's emergency vote."
    ul, us = "https://port-leaks-exposed.org/f", "https://shadow-briefing.net/r"
    specs = [
        ("coord_01_fast_burst", "8-account 2-minute copy burst",
         bg(rng, 80, "c01") + [Post(f"c1_{k}", f"citizen_{k}x", T0 + 9000 + k * 15, ma) for k in range(8)]),
        ("coord_02_small_burst_4", "4-account 90-second synchronized burst",
         bg(rng, 90, "c02") + [Post(f"c2_{k}", f"voter_{k}_k", T0 + 11000 + k * 22, mc) for k in range(4)]),
        ("coord_03_lookalike_handles", "6 look-alike newly registered accounts",
         bg(rng, 75, "c03") + [Post(f"c3_{k}", f"harbour_truth_{120 + k * 17}", T0 + k * 3200, ma, author_created_at=T0 - 2 * 86400 + k * 3600) for k in range(6)]),
        ("coord_04_link_push_domain", "6 accounts pushing same non-mainstream leak link",
         bg(rng, 75, "c04") + [Post(f"c4_{k}", f"indy_{k}_v", T0 + k * 3100, f"{ma} {ul}", url=ul) for k in range(6)]),
        ("coord_05_template_slots", "7-account near-duplicate slot template burst",
         bg(rng, 80, "c05") + [Post(f"c5_{k}", f"watch_{200 + k}", T0 + 12000 + k * 20, f"Why is nobody asking about the secret {['harbour deal', 'port contract', 'dredging pact'][k % 3]} signed behind closed doors tonight without a vote?") for k in range(7)]),
        ("coord_06_telegram_mirror", "6 Telegram mirror channels forwarding campaign link",
         bg(rng, 65, "c06") + [Post(f"c6_{k}", f"@baltic_mirror_{10 + k}", T0 + 6000 + k * 25, f"{mb} {us}", platform="telegram") for k in range(6)]),
        ("coord_07_two_narratives", "Same 6 accounts running two synchronized talking points",
         bg(rng, 85, "c07") + [Post(f"c7a_{k}", f"grid_alert{30 + k}", T0 + 5000 + k * 18, ma) for k in range(6)]
         + [Post(f"c7b_{k}", f"grid_alert{30 + k}", T0 + 8400 + k * 18, mb) for k in range(6)]),
        ("coord_08_hashtag_campaign", "6 accounts pushing non-event campaign hashtag",
         bg(rng, 75, "c08") + [Post(f"c8_{k}", f"taxpayer_{k}z", T0 + k * 2700, f"{mc} #AuditThePortDealNow") for k in range(6)]),
        ("coord_09_mention_brigade", "5 accounts copy-pasting pressure message at same target",
         bg(rng, 75, "c09") + [Post(f"c9_{k}", f"concern_{k}q", T0 + k * 2900, f"@cityauditor {mc}") for k in range(5)]),
        ("coord_10_noisy_180_feed", "5-account 60-second burst buried in 180 organic posts",
         bg(rng, 180, "c10", 240.0) + [Post(f"c10_{k}", f"rapid_{k}m", T0 + 18000 + k * 11, mb) for k in range(5)]),
    ] + _more_coordinated(rng, ma, mb, mc)
    return [{"id": cid, "kind": "coordinated", "label": lbl, "posts": pts} for cid, lbl, pts in specs] + build_innocent(rng)


def _more_coordinated(rng: random.Random, ma: str, mb: str, mc: str) -> list[tuple[str, str, list[Post]]]:
    ug = "https://grid-truth-watch.info/s"
    return [
        ("coord_11_single_purpose", "6 single-purpose accounts repeating talking point",
         bg(rng, 70, "c11") + [Post(f"c11_{k}_{m}", f"cell_unit_{40 + k}", T0 + k * 2500 + m * 600, ma) for k in range(6) for m in range(2)]),
        ("coord_12_new_cohort_week", "5 unrelated handles registered in same 48h window",
         bg(rng, 75, "c12") + [Post(f"c12_{k}", ["oak_tree", "blue_river", "north_wind", "red_stone", "calm_bay"][k], T0 + k * 3400, mb, author_created_at=T0 - 86400 + k * 5400) for k in range(5)]),
        ("coord_13_cross_platform", "Cross-platform Reddit + Bluesky + Telegram copy burst",
         bg(rng, 70, "c13") + [Post(f"c13_{k}", f"net_actor_{k}", T0 + 7500 + k * 19, mc, platform=("reddit", "bluesky", "telegram")[k % 3]) for k in range(6)]),
        ("coord_14_url_utm_variants", "6 accounts sharing campaign URL with tracking params",
         bg(rng, 70, "c14") + [Post(f"c14_{k}", f"share_u_{k}", T0 + k * 2600, f"{mb} {ug}?utm_source=s{k}", url=f"{ug}?utm_source=s{k}") for k in range(6)]),
        ("coord_15_two_waves", "Two tight bursts 3 hours apart from 6 accounts",
         bg(rng, 85, "c15") + [Post(f"c15_{k}", f"wave_acct_{k}", T0 + (4000 if k < 4 else 14800) + k * 14, ma) for k in range(6)]),
        ("coord_16_prefix_edit", "Near-duplicate core claim with varied intros in 90s burst",
         bg(rng, 80, "c16") + [Post(f"c16_{k}", f"alert_p_{k}", T0 + 9500 + k * 16, f"{['Listen:', 'Read:', 'Urgent:', 'Share:', 'Look:'][k % 5]} {mc}") for k in range(5)]),
        ("coord_17_borderline_3acct", "Borderline 3-account 30-second burst in 100-post thread",
         bg(rng, 100, "c17") + [Post(f"c17_{k}", f"trio_{k}_p", T0 + 15000 + k * 9, ma) for k in range(3)]),
        ("coord_18_heavy_paraphrase", "Hard limit: heavily rewritten talking point (no shared 5-grams)",
         bg(rng, 75, "c18") + [
             Post("c18_0", "p_a1", T0 + 6000, "Forty million from harbour fees vanished behind closed doors at city hall tonight."),
             Post("c18_1", "p_a2", T0 + 6015, "Municipal leaders quietly gave away port income worth 40m euros without oversight."),
             Post("c18_2", "p_a3", T0 + 6030, "Secret late-night deal hands over tens of millions in dockland money to insiders."),
             Post("c18_3", "p_a4", T0 + 6045, "Taxpayers just lost forty million in maritime port funds during a closed session."),
         ]),
        ("coord_19_slow_drip_no_aux", "Hard limit: 3 veteran accounts, hours apart, no 2nd signal",
         bg(rng, 75, "c19") + [Post(f"c19_{k}", ["old_timer_a", "steady_reader", "local_voice"][k], T0 + k * 14000, mc, author_created_at=T0 - (900 + k * 250) * 86400) for k in range(3)]),
        ("coord_20_punct_padded_burst", "6 accounts padding copied text with punctuation in 2-min burst",
         bg(rng, 75, "c20") + [Post(f"c20_{k}", f"flash_u_{k}", T0 + 10200 + k * 17, f"{mb} {'!' * (k % 3 + 1)}") for k in range(6)]),
    ]
