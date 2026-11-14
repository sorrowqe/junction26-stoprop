"""syndication receipt: exact-phrase search for copies of an article's wording on other domains.

Uses Brave Search when configured (`BRAVE_API_KEY`, 0 Oxylabs credits, <1 s) when an article's
distinctive sentence is available (Analyze URL). On bare domain checks (`/source` without article text)
or when search is not configured, reports `not_checked` honestly.
"""
from __future__ import annotations

from app import config
from app.pipeline import websearch
from app.swarm.collectors.http import Session
from app.trace import fingerprint


def configured() -> bool:
    return config.active_search_provider() == "brave" and bool(config.brave_api_key())


def not_checked(*, domain_only: bool = False) -> dict:
    if domain_only and configured():
        return {"status": "not_checked", "flag": "neutral", "evidence_url": None,
                "text": "Paste a specific article link (instead of just a domain name) to check whether its wording appears verbatim on other sites."}
    return {"status": "not_checked", "flag": "neutral", "evidence_url": None,
            "text": "Search for copies of this text on other sites is not configured on this server, so it was not checked."}


async def check(registrable_domain: str, quote: str) -> dict:
    phrase = fingerprint.query_phrase(quote)
    if len(phrase.split()) < 6 or not configured():
        return not_checked(domain_only=True)
    try:
        async with Session(max_requests=2, deadline_s=5.0) as s:
            hits = await websearch.brave_search(s, f'"{phrase}"', kind="web", limit=15)
    except websearch.SearchProviderError:
        return {"status": "error", "flag": "neutral", "evidence_url": None,
                "text": "The open-web copy search did not answer in time. Try Refresh later."}

    norm_words = fingerprint.norm(phrase).split()
    win = " ".join(norm_words[:min(6, len(norm_words))])
    other_domains: list[str] = []
    top_url = None
    for h in hits:
        if h["domain"] == registrable_domain:
            continue
        hay = fingerprint.norm((h.get("title") or "") + " " + (h.get("snippet") or ""))
        if win and win not in hay:
            continue
        if h["domain"] not in other_domains:
            other_domains.append(h["domain"])
            if top_url is None:
                top_url = h["url"]

    if not other_domains:
        return {"status": "not_found", "flag": "neutral", "evidence_url": None,
                "text": "No verbatim copies of the sampled sentence were found on other indexed websites."}
    sample = ", ".join(other_domains[:3]) + ("…" if len(other_domains) > 3 else "")
    n = len(other_domains)
    return {
        "status": "found",
        "flag": "neutral",
        "evidence_url": top_url,
        "details": {"domains": other_domains[:10], "count": n},
        "text": f"Exact wording from this page also appears on {n} other website{'s' if n != 1 else ''} ({sample}). "
                "Shared wording can reflect normal wire-service syndication, quotes, or copied content.",
    }
