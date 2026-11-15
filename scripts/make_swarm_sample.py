"""Generate the hand-made /swarm sample dataset (English, seeded, ~300 posts) -> web/static/swarm_sample.csv.

  python scripts/make_swarm_sample.py

Contents (all fictional): organic background chatter, two obvious campaigns (copy-paste with patterned,
recently created accounts and a fringe link; a slot-word template pushing a mention + hashtag), one
headline-sharing burst (mainstream link, within an hour), one event-hashtag burst (many unrelated
accounts, different texts, a few identical cheers), and generic short phrases. Used before the M5
benchmark exists; the benchmark sample replaces it in Part B.
"""
from __future__ import annotations

import csv
import itertools
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "web" / "static" / "swarm_sample.csv"
SEED = 2026
START = datetime(2026, 11, 10, tzinfo=timezone.utc)
DAYS = 3

OPENERS = [
    "Rain again this morning, so the bike stayed home.", "Finally tried the new ramen place near the station.",
    "Our team lost the derby last night by a single goal.", "The library extended its weekend opening hours.",
    "My sourdough starter survived another week somehow.", "Traffic on the ring road was terrible today.",
    "Spent the afternoon fixing the garden fence with my dad.", "The tram was ten minutes late and packed.",
    "Got my first frost warning on the phone today.", "The school concert was louder than expected.",
    "Picked up a second-hand camera at the flea market.", "Our cat discovered the radiator and will not move.",
    "The new bakery on the corner sold out by nine.", "Read the whole novel in one sitting yesterday.",
    "The gym was strangely empty at lunchtime.", "Power was out for an hour in our street.",
    "Finished the last episode of that detective series.", "The market had the first winter apples today.",
    "My neighbour started learning the trumpet.", "Walked along the harbour at sunset for once.",
    "The office coffee machine broke again.", "Booked train tickets for the holidays already.",
    "Took the kids ice skating for the first time.", "The bus driver waited for me, small miracle.",
    "Planted tulip bulbs before the ground freezes.", "Our quiz team came second at the pub.",
    "Cleaned out the garage and found my old skis.", "The museum has a free entry evening this month.",
    "Tried knitting and produced something sock-shaped.", "The football pitch is finally getting floodlights.",
    "Had soup at the market hall with old friends.", "The weather app promised sun and delivered fog.",
]
MIDDLES = [
    "Honestly it made the whole day better.", "Not sure how I feel about it yet.",
    "Prices have gone up a lot since summer though.", "The staff were friendly and patient with everyone.",
    "It reminded me how small this city really is.", "Next time I will bring a thermos.",
    "My phone battery did not survive the cold.", "Somebody should write a guide for beginners.",
    "The queue moved faster than I feared.", "I took far too many photos again.",
    "It was cheaper than the place we usually go.", "The kids want to do it every weekend now.",
    "Apparently this happens every November here.", "My hands are still freezing hours later.",
    "It took three tries but I got there.", "A stranger helped me carry everything home.",
    "The playlist in the background was excellent.", "Learned more in one hour than in a month.",
    "There were dogs everywhere, which was great.", "The lighting was beautiful for photos.",
    "We stayed much longer than we planned.", "I underestimated how tiring it would be.",
    "Half the city seemed to have the same idea.", "I would pay double for that view.",
    "The instructions were written for experts only.", "It felt like the first proper winter day.",
    "Everyone was talking about the same thing.", "I need better gloves before December.",
    "The smell of cinnamon followed me home.", "It is surprisingly relaxing once you start.",
    "Still recovering from the walk uphill.", "The neighbours joined in by the end.",
]
CLOSERS = [
    "Anyone else been there recently?", "Recommendations welcome for next time.",
    "Back to work tomorrow, sadly.", "Hope your week is going well.",
    "Will report back after the weekend.", "Let me know if you want the address.",
    "Coffee first, everything else later.", "What are your plans for Sunday?",
    "Photos in the comments if anyone cares.", "Small joys, as they say.",
    "Now to find somewhere warm.", "Same again next month I think.",
    "Tell me I am not the only one.", "Anyway, time to make dinner.",
    "Glad I did not stay in bed.", "The weekend cannot come soon enough.",
    "Off to bed early tonight.", "Any tips from people who know more?",
    "Worth it, would do it again.", "Not complaining, just sharing.",
    "Keeping my fingers crossed for tomorrow.", "That is all my news for today.",
    "Someone remind me to buy salt for the steps.", "Tea and a blanket now.",
    "Life is good in small doses.", "Maybe I will try the other one next.",
    "Thanks to everyone who helped out.", "Back with more updates later.",
    "Hard to beat an evening like that.", "Already looking forward to spring.",
    "Can recommend if you are nearby.", "Fingers crossed the forecast is wrong.",
]
GENERIC_SHORT = ["So true!", "Good morning everyone", "Happy Friday!", "Can't wait for the weekend",
                 "This is the way", "Thanks for sharing", "Agreed 100%", "Well said", "Love this", "lol same"]
GENERIC_LONG = "Thank you so much for sharing this with all of us today"
FIRST = ["maria", "james", "kevin", "claire", "olga", "tom", "anna", "peter", "sofia", "mark", "chloe", "ivan", "laura",
         "martin", "eva", "andrew", "nina", "lucas", "helen", "samir", "kate", "michael", "julia", "thomas", "emma",
         "ryan", "irina", "karl", "elena", "marco", "tara", "paul", "kristin", "jonas", "amy", "david", "maya"]
LAST = ["k", "miller", "chen", "patel", "garcia", "walker", "reed", "BROOKS".lower(), "petrov", "smith", "berg", "silva", "kim"]


def iso(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def organic_time(rng: random.Random) -> datetime:
    """Diurnal: mostly 07-23 UTC, uniform within the hour."""
    hours = list(range(24))
    weights = [0.2, 0.1, 0.1, 0.1, 0.1, 0.2, 0.5, 1.0, 1.4, 1.3, 1.2, 1.2, 1.4, 1.3, 1.2, 1.2, 1.3, 1.5, 1.8, 2.0,
               2.0, 1.7, 1.2, 0.6]
    day = rng.randrange(DAYS)
    hour = rng.choices(hours, weights)[0]
    return START + timedelta(days=day, hours=hour, seconds=rng.randrange(3600))


def organic_texts(rng: random.Random, n: int) -> list[str]:
    """Three-sentence posts; any two posts share at most one sentence (no accidental text reuse)."""
    combos = list(itertools.product(range(len(OPENERS)), range(len(MIDDLES)), range(len(CLOSERS))))
    rng.shuffle(combos)
    used_pairs: set = set()
    out = []
    for o, m, c in combos:
        keys = {("om", o, m), ("oc", o, c), ("mc", m, c)}
        if keys & used_pairs:
            continue
        used_pairs |= keys
        out.append(f"{OPENERS[o]} {MIDDLES[m]} {CLOSERS[c]}")
        if len(out) == n:
            break
    return out


def main() -> None:
    rng = random.Random(SEED)
    rows = []
    names = sorted({f"{f}_{l}" for f in FIRST for l in LAST})
    rng.shuffle(names)
    organic_authors = names[:120]
    created = {a: iso(datetime(rng.randrange(2011, 2026), rng.randrange(1, 13), rng.randrange(1, 28), tzinfo=timezone.utc))
               for a in names}

    def add(author, t, text, url="", author_created=None):
        rows.append({"id": f"p{len(rows) + 1:04d}", "author": author, "timestamp": iso(t), "text": text, "url": url,
                     "author_created_at": author_created or created.get(author, "")})

    # organic background: 150 distinct posts + 25 generic short ones
    for text in organic_texts(rng, 150):
        add(rng.choice(organic_authors), organic_time(rng), text)
    for _ in range(25):
        add(rng.choice(organic_authors), organic_time(rng), rng.choice(GENERIC_SHORT))
    # generic longer phrase written independently by 4 people on different days
    for k, a in enumerate(organic_authors[:4]):
        add(a, START + timedelta(days=k % DAYS, hours=9 + 3 * k, minutes=rng.randrange(60)), GENERIC_LONG)

    # campaign 1: copy-paste, patterned names, created within 4 days, fringe link, tight burst + follow-ups
    c1 = ["civic_truth_481", "civic_truth_77", "civictruth_2290", "civic_truths_12", "civicc_truth_903",
          "civic_truth_5518", "civic_trut_64", "civic_truth_3307", "civic_truths_88", "civic_truth_1945",
          "civc_truth_206", "civic_truth_733"]
    c1_text = ("The energy levy is a scam to fund the council's friends and nobody will tell you. "
               "Share before they delete it! https://levy-truth.info/proof #StopTheLevy")
    c1_follow = ["The energy levy is a scam to fund the council's friends and the media stays silent. "
                 "Read it before they delete it https://levy-truth.info/proof #StopTheLevy",
                 "Still nobody explains where the energy levy money goes. Council friends get rich, you pay. "
                 "https://levy-truth.info/proof #StopTheLevy"]
    burst = START + timedelta(days=1, hours=14, minutes=3)
    for k, a in enumerate(c1):
        made = iso(datetime(2026, 11, 1, tzinfo=timezone.utc) + timedelta(hours=rng.randrange(0, 96)))
        add(a, burst + timedelta(seconds=rng.randrange(0, 420)), c1_text, author_created=made)
        for j, text in enumerate(c1_follow):
            add(a, START + timedelta(days=1 + j, hours=19 + j, minutes=rng.randrange(0, 50)), text, author_created=made)

    # campaign 2: slot-word template, unrelated-looking accounts, spread over ~3 hours, pushes @mention + #hashtag
    c2 = names[130:139]
    deals = ["the harbour deal", "the port contract", "the harbour contract"]
    who = ["Mayor Vance", "the mayor", "city hall"]
    when = ["in secret", "behind closed doors", "without any debate"]
    t2 = START + timedelta(days=2, hours=8)
    for k, a in enumerate(c2):
        text = (f"Why is nobody talking about {deals[k % 3]}? {who[(k // 3) % 3].capitalize() if k % 2 else who[(k // 3) % 3]} "
                f"signed it {when[k % 3]} and the money went abroad. Ask questions now! @CityHallWatch #HarbourGate")
        add(a, t2 + timedelta(minutes=k * 21 + rng.randrange(0, 9)), text)

    # hard negative: headline sharing (mainstream link, within an hour, a few with own comments)
    headline = "Council approves new offshore wind farm in North Sea after two-year review"
    url = "https://www.bbc.com/news/science-environment-council-approves-new-wind-farm-north-sea"
    t3 = START + timedelta(days=1, hours=7, minutes=12)
    extras = ["", "", "", "", " Good news for clean power.", "", " Finally.", "", "", " About time!", "", "", "", ""]
    for k, a in enumerate(organic_authors[20:34]):
        add(a, t3 + timedelta(minutes=rng.randrange(0, 55)), f"{headline}{extras[k]} {url}", url)

    # hard negative: event hashtag burst (many accounts, different texts) + a few identical cheers
    t4 = START + timedelta(days=2, hours=19)
    cheers = ["What an opening performance from the band tonight", "The staging this year is absolutely wild",
              "My whole family is shouting at the TV right now", "That key change gave me goosebumps honestly",
              "Voting lines are open, get your phones out people", "The host jokes are not landing but who cares",
              "Best interval act I have seen in years, no contest", "Our flat party has reached peak volume",
              "Somebody please explain the jury points to me", "The dancers in the third song deserve a raise",
              "I have changed my favourite four times already", "Grandma is voting for the one with the violin",
              "Snacks are gone and we are only halfway through", "That ballad was beautiful but too slow for me",
              "The wind machine budget must be enormous", "Cannot believe the lyrics of that last one",
              "Neighbours just knocked to ask who is winning", "The camera work tonight is really impressive",
              "Our group chat has exploded with opinions", "Pretty sure the favourite will win anyway",
              "The pyrotechnics nearly set my screen on fire", "Watching with friends from three countries tonight"]
    for k, a in enumerate(organic_authors[40:66]):
        if k < 4:
            text = "Tonight's winner absolutely deserves it, what a performance from start to finish #EurovisionFinal"
        else:
            text = f"{cheers[k - 4]} #EurovisionFinal"
        add(a, t4 + timedelta(minutes=rng.randrange(0, 110)), text)

    rows.sort(key=lambda r: r["timestamp"])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["id", "author", "timestamp", "text", "url", "author_created_at"],
                           lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {OUT} ({len(rows)} posts, {len({r['author'] for r in rows})} authors)")


if __name__ == "__main__":
    main()
