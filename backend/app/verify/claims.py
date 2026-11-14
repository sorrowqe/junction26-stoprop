"""Claim extraction for verification: one MODEL_SUMMARY call -> <= 3 check-worthy claims.

Each claim: a short self-contained statement, key entities, search keywords, the cited source (outlet name)
and the exact attribution phrase ("according to Delfi news"), which code must find verbatim in the text
(else it is dropped). The outlet is mapped to its domains through config/outlets.yaml; an unknown outlet keeps
the model's domain guess only if it is a valid public domain name.
"""
from __future__ import annotations

import re

from app import config
from app.llm import client as llm
from app.receipts import domain as dom
from app.receipts.lookalike import outlets
from app.trace.fingerprint import norm

MAX_CLAIMS = 3
SYSTEM = """You prepare factual claims for checking against news reports. From the user's text, extract at most 3
check-worthy factual claims: concrete events, actions or numbers that news outlets would report (not opinions,
predictions or insults). Return only a JSON array of objects with these keys:
- "claim": the claim as one short, self-contained English sentence;
- "entities": 2 to 4 key names (people, places, organisations);
- "query": 3 to 6 English search keywords for news search, most specific first, no quotes or operators;
- "cited_source": the outlet or organisation the text says the claim comes from, or null;
- "cited_domain": that outlet's website domain if you know it (e.g. "delfi.ee"), or null;
- "attribution": the exact words in the text that attribute the claim (e.g. "according to Delfi news"),
  copied character for character, or null.
Do not judge whether claims are true. Return [] if there is nothing check-worthy."""
_GENERIC = {"news", "the", "media", "press", "online", "tv", "radio", "daily", "times", "post", "estonia", "latvia",
            "lithuania", "public", "broadcasting"}


def outlet_domains(name: str | None, guess: str | None) -> tuple[list[str], str | None]:
    """(domains, outlet name) for a cited source. Matches outlets.yaml names on distinctive words."""
    if name:
        words = {w for w in re.findall(r"[a-z0-9]+", name.lower()) if w not in _GENERIC and len(w) >= 3}
        hits = []
        for o in outlets():
            o_words = set(re.findall(r"[a-z0-9]+", o["name"].lower())) | set(o["labels"])
            if words & (o_words - _GENERIC):
                hits.append(o)
        if hits:
            return sorted({d for o in hits for d in o["domains"]}), " / ".join(o["name"] for o in hits)
    if guess:
        try:
            host = dom.normalize_host(guess)
            return [dom.registrable(host)], name
        except ValueError:
            pass
    return [], name


def _locate(text: str, phrase: str | None) -> tuple[int, int] | None:
    if not phrase or len(phrase.strip()) < 4:
        return None
    i = text.find(phrase)
    if i >= 0:
        return i, i + len(phrase)
    i = text.lower().find(phrase.lower())
    return (i, i + len(phrase)) if i >= 0 else None


def clean(text: str, items) -> list[dict]:
    out, seen = [], set()
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict) or not isinstance(it.get("claim"), str) or not it["claim"].strip():
            continue
        if norm(it["claim"]) in seen:
            continue
        seen.add(norm(it["claim"]))
        query = it.get("query") if isinstance(it.get("query"), str) else ""
        query = " ".join(re.sub(r'["()]', " ", query).split()[:6])
        ents = [e for e in it.get("entities") or [] if isinstance(e, str)][:4]
        if not query:
            query = " ".join(ents)
        span = _locate(text, it.get("attribution") if isinstance(it.get("attribution"), str) else None)
        cited = it.get("cited_source") if isinstance(it.get("cited_source"), str) else None
        domains, outlet = outlet_domains(cited, it.get("cited_domain") if isinstance(it.get("cited_domain"), str) else None)
        out.append({"claim": it["claim"].strip()[:300], "entities": ents, "query": query,
                    "cited_source": outlet or cited, "cited_domains": domains,
                    "attribution": text[span[0]:span[1]] if span else None,
                    "attribution_span": list(span) if span else None})
        if len(out) == MAX_CLAIMS:
            break
    return out


async def extract(text: str) -> tuple[list[dict], dict]:
    out = await llm.chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": text[:6000]}],
                         model=config.model_summary(), max_tokens=700, kind="verify_claims")
    meta = {"llm_calls": 1, "credits": out.credits, "input_tokens": out.input_tokens, "output_tokens": out.output_tokens}
    return clean(text, llm.extract_json_array(out.text)), meta
