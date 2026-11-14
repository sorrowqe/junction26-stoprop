"""Fingerprint: 2-3 distinctive sentences of a post, used as exact-phrase search queries.

One cheap LLM call (MODEL_SUMMARY) proposes sentences; code verifies each is verbatim in the text and has
>= 8 words (CLAUDE.md §1 rule 2: the model proposes, code verifies). If the call fails, is not configured or
the budget is used up, a deterministic rarity score picks them instead.
"""
from __future__ import annotations

import math
import re

from app import config
from app.llm import budget
from app.llm import client as llm
from app.pipeline.segment import segment

MIN_WORDS = 8
MAX_PICK = 3
QUERY_MAX_WORDS = 24
MAX_INPUT_CHARS = 5000
SYSTEM = """You choose search fingerprints for tracing where a text was copied.
From the user's text, copy 2 or 3 sentences VERBATIM (exactly as written) whose wording is most distinctive:
unusual phrasing, specific names, numbers or claims that other people would be unlikely to write independently.
Each sentence must have at least 8 words. Avoid greetings, generic phrases, common idioms, hashtag lists and
well-known quotations. Do not judge the content. Return only a JSON array of strings."""

_URL = re.compile(r"https?://\S+|www\.\S+", re.I)
_MENTION = re.compile(r"(?<![\w@])@[\w.]+")
_WORD = re.compile(r"[^\W_]+(?:['’][^\W_]+)?", re.UNICODE)
COMMON = set("""the a an and or but if then so of to in on at for from by with about as is are was were be been being it its
this that these those i you he she we they me him her us them my your our their not no yes do does did have has had will
would can could should just very really all any some more most much many other such only also than too what who how
when where why there here up down out over again new one two first last time people year years day today way get got
make made said says like know think want see go going come back good great well even still because into after before
while through during between under around without within against again same each every both few own off""".split())


def clean(text: str) -> str:
    t = _URL.sub(" ", text or "")
    t = _MENTION.sub(" ", t)
    return re.sub(r"[ \t]+", " ", t).strip()


def words(s: str) -> list[str]:
    return _WORD.findall(s)


def norm(s: str) -> str:
    """Comparison form: lowercase letters/digits only, single spaces (quotes, dashes, case ignored)."""
    return " ".join(w.lower().replace("’", "'") for w in words(s))


def candidates(text: str) -> list[str]:
    seg = segment(text[:MAX_INPUT_CHARS])
    out = []
    for s in seg.sentences:
        t = " ".join(s.text.split())
        if len(words(t)) >= MIN_WORDS:
            out.append(t)
    if not out and len(words(text)) >= MIN_WORDS:      # one long line without sentence ends
        out.append(" ".join(text.split()))
    return out


def rarity(sentence: str) -> float:
    ws = words(sentence)
    score = 0.0
    for i, w in enumerate(ws):
        lw = w.lower()
        if lw in COMMON or len(lw) < 4:
            continue
        score += 1.0 + (0.5 if len(lw) >= 8 else 0) + (1.0 if any(ch.isdigit() for ch in w) else 0) \
            + (0.7 if i > 0 and w[0].isupper() else 0)
    return score / math.sqrt(len(ws)) if ws else 0.0


def heuristic(text: str, k: int = MAX_PICK) -> list[str]:
    cands = candidates(text)
    ranked = sorted(range(len(cands)), key=lambda i: (-rarity(cands[i]), i))
    return [cands[i] for i in sorted(ranked[:k])]


def query_phrase(sentence: str) -> str:
    """Exact-phrase query text: no quote marks, trailing punctuation removed, at most 24 words."""
    s = re.sub(r"[\"“”„«»]", " ", sentence)
    s = " ".join(s.split()).rstrip(".!?;:,… ")
    ws = s.split(" ")
    return " ".join(ws[:QUERY_MAX_WORDS])


def _verified(text: str, picks) -> list[str]:
    body = norm(text)
    out = []
    for p in picks if isinstance(picks, list) else []:
        if not isinstance(p, str):
            continue
        p = " ".join(p.split())
        if len(words(p)) >= MIN_WORDS and norm(p) and norm(p) in body and norm(p) not in {norm(x) for x in out}:
            out.append(p)
    return out[:MAX_PICK]


async def pick(text: str) -> tuple[list[str], dict]:
    """(fingerprints, meta). meta: method llm|heuristic|mixed, llm_calls, credits, note."""
    text = clean(text)[:MAX_INPUT_CHARS]
    meta = {"method": "heuristic", "llm_calls": 0, "credits": 0.0, "input_tokens": 0, "output_tokens": 0, "note": None}
    picks: list[str] = []
    ok, reason = budget.llm_available()
    if ok:
        try:
            out = await llm.chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": text}],
                                 model=config.model_summary(), max_tokens=400, kind="trace_fingerprint")
            meta.update(llm_calls=1, credits=out.credits, input_tokens=out.input_tokens, output_tokens=out.output_tokens)
            picks = _verified(text, llm.extract_json_array(out.text))
            meta["method"] = "llm"
            if not picks:
                meta["note"] = "The AI's picks were not found word for word in the text, so a word-rarity rule chose instead."
        except llm.LLMError:
            meta["note"] = "The AI was not available, so a word-rarity rule chose the sentences."
    else:
        meta["note"] = ("AI is not configured on this server" if reason == "no_key" else "The daily AI budget is used up") + \
            ", so a word-rarity rule chose the sentences."
    if len(picks) < 2:
        extra = [h for h in heuristic(text) if norm(h) not in {norm(p) for p in picks}]
        if picks and extra:
            meta["method"] = "mixed"
        elif not picks:
            meta["method"] = "heuristic"
        picks = (picks + extra)[:MAX_PICK]
    return picks, meta
