"""Plain-English wording for swarm clusters (CLAUDE.md §1 rule 1, §5): describe behaviour, never label
accounts or people ("these 14 accounts posted near-identical text within 6 minutes")."""
from __future__ import annotations

VERDICT_TEXT = {
    "likely_coordinated": "Likely coordinated",
    "worth_watching": "Worth watching",
    "organic_looking": "Looks organic",
    "insufficient_data": "Not enough data",
}


def duration(seconds: float) -> str:
    s = max(0.0, float(seconds))
    if s < 90:
        n, unit = round(s), "second"
    elif s < 90 * 60:
        n, unit = round(s / 60), "minute"
    elif s < 36 * 3600:
        n, unit = round(s / 3600), "hour"
    else:
        n, unit = round(s / 86400), "day"
    return f"{n} {unit}{'' if n == 1 else 's'}"


def reuse_kind(kinds: dict[str, int]) -> str:
    if kinds.get("trace"):
        return "the traced"
    if kinds.get("near"):
        return "near-identical"
    if kinds.get("exact"):
        return "identical" if not kinds.get("paraphrase") else "identical or paraphrased"
    return "similar (paraphrased)"


def headline(n_authors: int, n_posts: int, kinds: dict[str, int], span_s: float) -> str:
    what = reuse_kind(kinds)
    times = "" if n_posts == n_authors else f" {n_posts} times"
    return f"{n_authors} accounts posted {what} text{times} within {duration(span_s)}."


def _p(p: float) -> str:
    return "< 0.000001" if p < 1e-6 else f"{p:.2g}"


def evidence(c: dict) -> tuple[list[str], list[str]]:
    """(evidence_for, evidence_against) for one cluster dict built by clusters.py."""
    pro, con = [], []
    t = c["signals"]["T"]
    kinds = t["pairs"]
    bits = []
    if kinds.get("exact"):
        bits.append(f"{kinds['exact']} identical")
    if kinds.get("near"):
        bits.append(f"{kinds['near']} near-identical (≥ 60% shared character sequences)")
    if kinds.get("paraphrase"):
        bits.append(f"{kinds['paraphrase']} paraphrased (meaning-level match, weaker evidence)")
    if kinds.get("trace"):
        pro.append(f"Text reuse: {c['post_count']} places carry the traced sentence word for word (found by exact-phrase "
                   "search and checked in the text or the search snippet).")
    else:
        pro.append(f"Text reuse across different accounts: {', '.join(bits)} post links.")

    s = c["signals"]["S"]
    if s.get("present"):
        pro.append(f"Timing: {s['posts_in_window']} of {c['post_count']} posts appeared within "
                   f"{duration(s['window_s'])}; at this batch's normal pace about {s['expected']:.2g} would be expected "
                   f"(chance p {_p(s['p'])}).")
    elif "window_s" in s:
        con.append(f"Timing looks ordinary: {s['posts_in_window']} of {c['post_count']} posts took "
                   f"{duration(s['window_s'])}, which this batch's normal pace explains (p {_p(s['p'])}).")
    for a in s.get("regular_cadence", [])[:3]:
        pro.append(f"Very regular posting rhythm: {a['author']} posted {a['posts']} times about every "
                   f"{duration(a['mean_gap_s'])} (variation {round(a['cv'] * 100)}%).")

    a = c["signals"]["A"]
    if a["usernames"]["present"]:
        names = a["usernames"]["group"]
        pro.append(f"{len(names)} account names follow the same pattern (similar word + digits), e.g. "
                   f"{', '.join(names[:3])}.")
    if a["creation"]["present"]:
        pro.append(f"{a['creation']['within_7_days']} of {a['creation']['known']} accounts with a known creation "
                   "date were created within 7 days of each other.")
    if a["single_purpose"]["present"]:
        pro.append(f"{len(a['single_purpose']['authors'])} accounts posted mostly this content "
                   "(at least 70% of their posts in this batch).")
    if not a["present"]:
        missing = "" if a["creation"]["known"] else " (no account creation dates were provided)"
        con.append(f"Accounts look unrelated: no shared naming pattern, creation-date cluster or single-purpose "
                   f"accounts{missing}.")

    m = c["signals"]["M"]
    for item in m["pushed"][:3]:
        kind = {"url": "link", "hashtag": "hashtag", "mention": "mention"}[item["kind"]]
        shown = ("#" if item["kind"] == "hashtag" else "@" if item["kind"] == "mention" else "") + item["value"]
        pro.append(f"Amplification: {item['authors']} accounts pushed the same {kind} {shown}.")
    for item in m["mainstream"][:2]:
        con.append(f"The shared link points to a mainstream outlet ({item['value'].split('/')[0]}), "
                   "which does not count as amplification.")
    if not m["present"] and not m["mainstream"]:
        con.append("No link, hashtag or mention was pushed by 5 or more of these accounts.")
    if c["author_count"] < 3:
        con.append("Fewer than 3 different accounts: too little to judge coordination.")
    return pro, con


INNOCENT_TEXT = {
    "headline_sharing": "Headline sharing: many people post the same news headline with a link to a mainstream "
                        "outlet ({detail}).",
    "short_generic": "Short or generic text ({detail}): such phrases are often written independently by many people.",
    "quoted_statement": "Quoted statement: most posts quote an attributed statement ({detail}); people often share "
                        "the same official quote.",
    "syndication": "Syndication: the text first appeared on {detail}; other sites republishing agency or "
                   "press-release copy is normal.",
    "event_hashtag": "Event hashtag burst: #{detail} was used by many unrelated accounts at the same time, as "
                     "during a live event.",
}
