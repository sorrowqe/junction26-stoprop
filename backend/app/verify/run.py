"""Verify claims: bounded pipeline (no agent loop), <= 30 s.

1. MODEL_SUMMARY extracts <= 3 claims (+ cited source and the verbatim attribution phrase).
2. Attribution check: news search on the cited outlet's domains, last 30 days.
3. Corroboration: news search on all outlets, last 14 days; independent = different registrable domains,
   an outlet's own domains (e.g. delfi.ee / delfi.lt) count once.
4. Google Fact Check Tools (only if FACTCHECK_API_KEY) for existing fact-checks.
5. <= 5 articles in total are read (SSRF-safe) and judged by MODEL_SUMMARY; every evidence quote must be found
   verbatim in the article, else the judgement is discarded.
6. Verdict per claim (never true/false): cited_source_not_found, contradicted, corroborated, no_reports_found,
   or not_checked when the search could not run. A cited source that was not found adds a red
   "unverifiable attribution" span on the attribution phrase.
At most 4 news searches per check (GDELT allows one request per 5 s). Results are cached for 1 hour per text.
"""
from __future__ import annotations

import asyncio
import hashlib
import time
import uuid

from app.llm import budget
from app.llm import client as llm
from app.pipeline import oxylabs
from app.receipts import factcheck
from app.swarm.collectors.http import Session
from app.trace.fingerprint import norm
from app.verify import claims as claims_mod
from app.verify import judge as judge_mod
from app.verify import news

TOTAL_DEADLINE_S = 38.0
DEADLINE_S = 28.0
MAX_SEARCHES = 4
MAX_ARTICLES = 5          # articles READ in full per check
MAX_OXY_ARTICLES = 2      # blocked articles read via Oxylabs AI Studio per check (protects 1000-credit budget)
TITLES_PER_CLAIM = 8      # extra headlines from the cited outlet judged by title/snippets only
ATTRIBUTION_DAYS = 30
CORROBORATION_DAYS = 14
CACHE_TTL_S = 3600
_cache: dict[str, tuple[float, dict]] = {}

VERDICT_TEXT = {
    "cited_source_not_found": "Cited source not found",
    "contradicted": "Contradicted by reports",
    "corroborated": "Corroborated",
    "no_reports_found": "No reports found",
    "not_checked": "Could not check",
}


class VerifyError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message, self.status = message, status


def reset() -> None:
    _cache.clear()


def _family(domain: str, cited: list[str]) -> str:
    """Domains of the same cited outlet count as one source."""
    return "cited" if domain in cited else domain


def decide(claim: dict, attribution: dict, judged: list[dict], factchecks: list[dict]) -> dict:
    """Verdict + plain explanation. attribution: {"needed", "searched", "error"}; judged: articles with stance."""
    cited = claim.get("cited_domains") or []
    supports = [a for a in judged if a["stance"] == "supports"]
    contra = [a for a in judged if a["stance"] == "contradicts"]
    cited_support = [a for a in supports if a["domain"] in cited]
    independent = sorted({_family(a["domain"], cited) for a in supports} - {"cited"})
    n_ind = len(independent)
    if any(a["domain"] in cited for a in contra):
        return {"verdict": "contradicted", "independent_outlets": n_ind,
                "text": f"The cited outlet ({next(a['domain'] for a in contra if a['domain'] in cited)}) reports the "
                        "opposite (see the quoted evidence)."}
    if attribution["needed"] and attribution["searched"] and not cited_support and not attribution.get("results"):
        if n_ind:
            return {"verdict": "corroborated", "independent_outlets": n_ind,
                    "text": f"{n_ind} independent outlet{'s' if n_ind != 1 else ''} report{'s' if n_ind == 1 else ''} it. "
                            f"Our news index has no recent articles from {', '.join(cited)} on this topic, so the "
                            "attribution itself could not be checked."}
        return {"verdict": "not_checked", "independent_outlets": 0,
                "text": f"Our news index has no articles from {', '.join(cited)} on this topic in the last "
                        f"{ATTRIBUTION_DAYS} days, so we cannot tell whether that outlet reported it. It may be too "
                        "recent or not indexed yet; nothing is concluded."}
    if attribution["needed"] and attribution["searched"] and not cited_support:
        where = ", ".join(cited)
        text = (f"{where} has recent articles on this topic in our news index, but none of the ones we checked says "
                "this. The attribution may be wrong or made up, or the report may be missing from the index.")
        if n_ind:
            text += f" {n_ind} other independent outlet{'s' if n_ind != 1 else ''} did report it."
        return {"verdict": "cited_source_not_found", "text": text, "independent_outlets": n_ind}
    if contra:
        return {"verdict": "contradicted", "independent_outlets": n_ind,
                "text": f"{len(contra)} report{'s' if len(contra) != 1 else ''} contradict{'s' if len(contra) == 1 else ''} "
                        "this claim (see the quoted evidence)."}
    if cited_support or n_ind:
        bits = []
        if cited_support:
            bits.append(f"the cited outlet ({cited_support[0]['domain']}) reports it")
        if n_ind:
            bits.append(f"{n_ind} independent outlet{'s' if n_ind != 1 else ''} report{'s' if n_ind == 1 else ''} it")
        return {"verdict": "corroborated", "independent_outlets": n_ind, "text": ("; ".join(bits)).capitalize() + "."}
    if attribution.get("error") or claim.get("_search_error"):
        return {"verdict": "not_checked", "independent_outlets": 0,
                "text": f"The news search could not run ({attribution.get('error') or claim['_search_error']}), "
                        "so nothing is concluded."}
    return {"verdict": "no_reports_found", "independent_outlets": 0,
            "text": f"We found no news reports about this from the last {CORROBORATION_DAYS} days. It may be too "
                    "recent, too local, or not covered by our news index; this is not evidence that it is false."}


async def verify(text: str, progress=None) -> dict:
    say = progress or (lambda m: None)
    t0 = time.monotonic()
    text = (text or "").strip()
    key = hashlib.sha256(norm(text).encode("utf-8")).hexdigest()
    hit = _cache.get(key)
    if hit and hit[0] > time.monotonic():
        return {**hit[1], "cached": True}
    ok, reason = budget.llm_available()
    if not ok:
        raise VerifyError("Claim checking needs the AI, which is not available on this server right now."
                          if reason == "no_key" else "Today's AI budget is used up; claim checking is paused.", 503)
    cost = {"llm_calls": 0, "credits": 0.0, "searches": 0, "articles_read": 0}

    def add(meta):
        cost["llm_calls"] += meta.get("llm_calls", 0)
        cost["credits"] += meta.get("credits", 0.0)

    say("Finding check-worthy claims…")
    try:
        found, meta = await claims_mod.extract(text)
    except llm.LLMError:
        raise VerifyError("The AI did not answer; please try again.", 503) from None
    add(meta)
    notes = [f"News search: {'GDELT news index' if news.backend() == 'gdelt' else 'web search (Oxylabs)'}."]
    if not found:
        result = {"id": "v_" + uuid.uuid4().hex[:10], "claims": [], "spans": [], "notes": notes + [
            "No check-worthy factual claims were found (opinions and predictions are not checked)."],
            "cost": cost, "elapsed_ms": round((time.monotonic() - t0) * 1000), "cached": False}
        _cache[key] = (time.monotonic() + CACHE_TTL_S, result)
        return result

    att_hits: dict[int, list] = {}
    cor_hits: dict[int, list] = {}
    att_state = {i: {"needed": bool(c["cited_domains"]), "searched": False, "error": None, "results": 0}
                 for i, c in enumerate(found)}
    session_budget = max(8.0, min(DEADLINE_S, TOTAL_DEADLINE_S - (time.monotonic() - t0) - 7.0))
    async with Session(max_requests=MAX_SEARCHES * 2 + MAX_ARTICLES, deadline_s=session_budget) as s:
        plan = []
        for i, c in enumerate(found):
            if c["cited_domains"]:
                plan.append(("att", i))
            plan.append(("cor", i))
        async def one(kind: str, i: int):
            c = found[i]
            say(f"Searching news for claim {i + 1}{' on ' + ', '.join(c['cited_domains']) if kind == 'att' else ''}…")
            try:
                if kind == "att":
                    # lenient: any article from the outlet mentioning a key entity; the judge decides
                    q = news.any_of(c["entities"][:3]) or news.clean_keywords(c["query"])
                    att_hits[i] = await news.search(s, q, domains=c["cited_domains"], days=ATTRIBUTION_DAYS, limit=25)
                    att_state[i]["searched"] = True
                    att_state[i]["results"] = len(att_hits[i])
                else:
                    cor_hits[i] = await news.search(s, news.clean_keywords(c["query"]), days=CORROBORATION_DAYS, limit=25)
                cost["searches"] += 1
            except news.SearchUnavailable as exc:
                if kind == "att":
                    att_state[i]["error"] = str(exc)
                else:
                    c["_search_error"] = str(exc)
        # searches overlap: the news module spaces their START times when needed
        await asyncio.gather(*(one(kind, i) for kind, i in plan[:MAX_SEARCHES]))
        for kind, i in plan[MAX_SEARCHES:]:
            if kind == "att":
                att_state[i]["error"] = "search limit for one check reached"
            else:
                found[i].setdefault("_search_error", "search limit for one check reached")

        # pick <= 5 articles: per claim the best cited-outlet hit, then one per independent domain, round-robin
        picks: dict[int, list[dict]] = {i: [] for i in range(len(found))}
        queues = []
        for i, c in enumerate(found):
            q, seen = [], set()
            for a in att_hits.get(i, [])[:2] + cor_hits.get(i, []):
                fam = _family(a["domain"], c["cited_domains"])
                if fam in seen or a["url"] in {x["url"] for x in q}:
                    continue
                seen.add(fam)
                q.append(a)
            queues.append(q)
        total = 0
        while total < MAX_ARTICLES and any(queues):
            for i, q in enumerate(queues):
                if q and total < MAX_ARTICLES:
                    picks[i].append(q.pop(0))
                    total += 1
        chosen = [a for i in picks for a in picks[i]]
        if chosen:
            say(f"Reading {len(chosen)} articles…")
            read = await asyncio.gather(*(judge_mod.read(a, min(4.5, s.remaining() - 1.0), allow_fallback=False) for a in chosen))
            by_url = {a["url"]: a for a in read}
            # Tier-2 fallback: if key articles blocked direct reading, scrape up to MAX_OXY_ARTICLES via Oxylabs AI Studio
            all_cited = {d for c in found for d in c["cited_domains"]}
            blocked = [a for a in read if not a["read"]]
            blocked.sort(key=lambda a: (0 if a.get("domain") in all_cited else 1))
            if blocked and oxylabs.status()["available"] and s.remaining() >= 6.0:
                retry_targets = blocked[:MAX_OXY_ARTICLES]
                say(f"Unblocking {len(retry_targets)} protected article{'s' if len(retry_targets) != 1 else ''}…")
                retried = await asyncio.gather(
                    *(judge_mod.read(a, s.remaining() - 0.5, allow_fallback=True) for a in retry_targets)
                )
                for a in retried:
                    if a["read"]:
                        by_url[a["url"]] = a
            cost["articles_read"] = sum(1 for a in by_url.values() if a["read"])
            for i in picks:
                picks[i] = [by_url[a["url"]] for a in picks[i]]
        # more headlines from the cited outlet, judged by title only (a relevant report may not be the top hit)
        for i in picks:
            have = {a["url"] for a in picks[i]}
            extra = [a for a in att_hits.get(i, []) if a["url"] not in have][:TITLES_PER_CLAIM]
            picks[i] += [{**a, "text": "", "read": False} for a in extra]

    say("Weighing the evidence…")
    results, spans = [], []
    discarded = 0

    async def judged_for(i: int, c: dict):
        if not picks.get(i):
            return [], None
        try:
            return await judge_mod.judge(c, picks[i])
        except llm.LLMError:
            return [], "failed"
    outcomes = await asyncio.gather(*(judged_for(i, c) for i, c in enumerate(found)))
    for i, c in enumerate(found):
        judged, meta = outcomes[i]
        if meta == "failed":
            notes.append(f"The AI could not judge the articles for claim {i + 1}.")
        elif meta:
            add(meta)
            discarded += meta["discarded"]
        checks = []
        if factcheck.configured():
            fc = await factcheck.check(c["claim"])
            checks = (fc.get("details") or {}).get("items", [])
        v = decide(c, att_state[i], judged, checks)
        results.append({"claim": c["claim"], "cited_source": c["cited_source"], "cited_domains": c["cited_domains"],
                        "attribution": c["attribution"], **v, "verdict_text": VERDICT_TEXT[v["verdict"]],
                        "evidence": [a for a in judged if a["stance"] != "unrelated"],
                        "also_found": [a for a in judged if a["stance"] == "unrelated"],
                        "fact_checks": checks, "fact_checks_status": "checked" if factcheck.configured() else "not_configured",
                        "searched": {"attribution": att_state[i], "query": c["query"]}})
        if v["verdict"] == "cited_source_not_found" and c["attribution_span"]:
            a, b = c["attribution_span"]
            spans.append({"start": a, "end": b, "quote": text[a:b], "label": "unverifiable_attribution", "color": "red",
                          "confidence": 1.0, "experimental": True, "reported_speech": False, "source": "verify",
                          "explanation": f"No report from {', '.join(c['cited_domains'])} saying this was found (last "
                                         f"{ATTRIBUTION_DAYS} days).", "sentence_id": None})
    if discarded:
        notes.append(f"{discarded} AI judgement{'s were' if discarded != 1 else ' was'} discarded because the quoted "
                     "evidence was not found word for word in the article.")
    if not factcheck.configured():
        notes.append("Independent Fact Check search was not run: search provider not configured on this server.")
    cost["credits"] = round(cost["credits"], 4)
    result = {"id": "v_" + uuid.uuid4().hex[:10], "claims": results, "spans": spans, "notes": notes, "cost": cost,
              "elapsed_ms": round((time.monotonic() - t0) * 1000), "cached": False}
    _cache[key] = (time.monotonic() + CACHE_TTL_S, result)
    return result
