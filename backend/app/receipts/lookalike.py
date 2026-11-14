"""Look-alike / clone domain check against config/outlets.yaml (pure, offline, no cache needed).

Detects: look-alike characters (homoglyphs, punycode), the outlet name under a different domain
ending ("TLD swap", e.g. spiegel.ltd), the outlet name used as a subdomain of another domain
(bbc.co.uk.example.com), one/two-letter typos, and extra words or hyphens (err-news.ee).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from app.receipts.domain import split_registrable, to_unicode

OUTLETS_PATH = Path(__file__).resolve().parents[2] / "config" / "outlets.yaml"
EXTRA_WORDS = {
    "news", "world", "today", "live", "online", "info", "daily", "media", "breaking", "report", "latest",
    "uk", "us", "eu", "int", "global", "official", "real", "the", "my", "24", "247", "now", "tv", "press",
    "uudised", "novosti", "nachrichten", "info24", "update", "updates", "web", "site",
}
_CONFUSABLE = {
    # Cyrillic / Greek look-alikes -> Latin
    "а": "a", "в": "b", "е": "e", "к": "k", "м": "m", "н": "h", "о": "o", "р": "p", "с": "c", "т": "t",
    "у": "y", "х": "x", "і": "i", "ј": "j", "ԁ": "d", "ѕ": "s", "ԛ": "q", "ԝ": "w", "ɡ": "g",
    "α": "a", "ο": "o", "ρ": "p", "ε": "e", "ι": "i", "κ": "k", "ν": "v", "τ": "t", "υ": "u",
    # digits that read as letters
    "0": "o", "1": "l", "3": "e", "5": "s", "|": "l",
    # accented Latin
    "á": "a", "à": "a", "â": "a", "ä": "a", "å": "a", "é": "e", "è": "e", "ê": "e", "ë": "e", "í": "i",
    "ì": "i", "ï": "i", "ó": "o", "ò": "o", "ô": "o", "ö": "o", "õ": "o", "ú": "u", "ù": "u", "ü": "u",
    "ç": "c", "ñ": "n", "ý": "y", "ł": "l",
}


@lru_cache(maxsize=1)
def outlets() -> list[dict]:
    data = yaml.safe_load(OUTLETS_PATH.read_text(encoding="utf-8"))
    out = []
    for o in data["outlets"]:
        doms = [d.lower() for d in o["domains"]]
        out.append({"name": o["name"], "domains": doms, "labels": sorted({split_registrable(d)[1] for d in doms})})
    return out


@lru_cache(maxsize=1)
def official_domains() -> dict[str, str]:
    return {d: o["name"] for o in outlets() for d in o["domains"]}


def skeleton(label: str) -> str:
    s = "".join(_CONFUSABLE.get(ch, ch) for ch in label.lower())
    return s.replace("rn", "m").replace("vv", "w").replace("cl", "d").replace("-", "")


def damerau(a: str, b: str, limit: int = 3) -> int:
    """Optimal string alignment distance (adjacent swaps count as 1), early exit above limit."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev2: list[int] | None = None
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if prev2 is not None and i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        prev2, prev = prev, cur
    return prev[-1]


def _reasons(host: str, reg: str, label: str, outlet: dict) -> list[str]:
    reasons = []
    uni_label = to_unicode(label) if label.startswith("xn--") else label
    host_labels = host.split(".")[:-1]
    for ol in outlet["labels"]:
        if uni_label != ol and skeleton(uni_label) == skeleton(ol):
            reasons.append("uses look-alike characters or a hyphen")
        if len(ol) < 3:  # 2-letter names (dw, ft, ap, dr, hs) collide with too many unrelated domains
            continue
        if label == ol:
            reasons.append("uses the same name under a different domain ending")
        elif ol in host_labels[:-1]:
            reasons.append("puts the outlet's name in front of a different domain")
        if len(ol) >= 5 and label != ol:
            d = damerau(label, ol)
            if d == 1 or (len(ol) >= 9 and d == 2):
                reasons.append("differs by one or two letters")
        if label != ol:
            parts = label.split("-")
            if len(parts) > 1 and ol in parts:
                reasons.append("adds extra words to the outlet's name")
            else:
                for word in EXTRA_WORDS:
                    if label in (ol + word, word + ol):
                        reasons.append("adds extra words to the outlet's name")
                        break
    return list(dict.fromkeys(reasons))


def check(host: str) -> dict:
    """Receipt dict (without checked_at)."""
    reg, label, _suffix = split_registrable(host)
    official = official_domains()
    total = len(outlets())
    if reg in official:
        return {
            "status": "found", "flag": "ok",
            "text": f"This is an official domain of {official[reg]}.",
            "evidence_url": None, "details": {"official": official[reg]},
        }
    matches = []
    for o in outlets():
        reasons = _reasons(host, reg, label, o)
        if reasons:
            matches.append({"outlet": o["name"], "official_domain": o["domains"][0], "reasons": reasons})
    if not matches:
        return {
            "status": "not_found", "flag": "neutral",
            "text": f"Doesn't resemble any of the {total} news outlets we track.",
            "evidence_url": None, "details": {"outlets_tracked": total},
        }
    first = matches[0]
    more = f" (also resembles {', '.join(m['outlet'] for m in matches[1:3])})" if len(matches) > 1 else ""
    return {
        "status": "found", "flag": "warning",
        "text": (f"This domain resembles {first['outlet']} ({first['official_domain']}): it "
                 f"{' and '.join(first['reasons'])}{more}. Clone sites copy a real outlet's look to borrow its "
                 "credibility; check the address carefully."),
        "evidence_url": f"https://{first['official_domain']}/",
        "details": {"matches": matches[:3]},
    }
