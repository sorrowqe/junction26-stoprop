"""T — text reuse (CLAUDE.md §5).

Comparison text = §4 normalize (NFKC, zero-width stripped, whitespace collapsed) + lowercase, URLs and
@mentions removed. Character 5-gram shingles; MinHash LSH (datasketch) proposes candidates and the exact
Jaccard similarity of the shingle sets decides (>= 0.6). Only posts with >= 8 words count as evidence
(short texts like "so true!" collide innocently); exact duplicates of >= 8 words always count.
Pairs are only formed between DIFFERENT authors. deep=True adds embedding "paraphrase" pairs
(posts >= 12 words, cosine >= 0.92), reported as weaker evidence.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from datasketch import MinHash, MinHashLSH

from app.pipeline.tagger import normalize
from app.swarm.ingest import Post

SHINGLE = 5
JACCARD = 0.6
MIN_WORDS = 8
NUM_PERM = 128
LSH_THRESHOLD = 0.45            # candidate recall margin below JACCARD; exact Jaccard decides
PARAPHRASE_MIN_WORDS = 12
COSINE = 0.92
MAX_EMBEDDINGS = 2000

_URL = re.compile(r"https?://\S+|www\.\S+", re.I)
_MENTION = re.compile(r"(?<![\w@])@\w+")
_WORD = re.compile(r"\w+")


@dataclass(frozen=True)
class Pair:
    a: int              # post index
    b: int
    kind: str           # exact | near | paraphrase
    score: float        # Jaccard (exact/near) or cosine (paraphrase)


def compare_text(text: str) -> str:
    t = normalize(text).lower()
    t = _URL.sub(" ", t)
    t = _MENTION.sub(" ", t)
    return " ".join(t.split())


def word_count(text: str) -> int:
    return len(_WORD.findall(text))


def shingles(text: str) -> set[str]:
    if len(text) < SHINGLE:
        return {text} if text else set()
    return {text[i:i + SHINGLE] for i in range(len(text) - SHINGLE + 1)}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _cross_author(group_a: list[int], group_b: list[int], authors: list[str]) -> tuple[int, int] | None:
    for i in group_a:
        for j in group_b:
            if i != j and authors[i] != authors[j]:
                return i, j
    return None


def find_pairs(posts: list[Post]) -> tuple[list[Pair], list[str]]:
    """Spanning evidence pairs (enough to build connected components) + the comparison texts."""
    norm = [compare_text(p.text) for p in posts]
    authors = [p.author.lower() for p in posts]
    groups: dict[str, list[int]] = defaultdict(list)
    for i, t in enumerate(norm):
        if word_count(t) >= MIN_WORDS:
            groups[t].append(i)

    pairs: list[Pair] = []
    # exact duplicates: link every post to a post of another author with the same text
    for members in groups.values():
        if len({authors[i] for i in members}) < 2:
            continue
        anchor = members[0]
        other = next(i for i in members if authors[i] != authors[anchor])
        for i in members[1:]:
            j = anchor if authors[i] != authors[anchor] else other
            if i != j:
                pairs.append(Pair(min(i, j), max(i, j), "exact", 1.0))

    # near duplicates between distinct texts: MinHash LSH candidates, exact Jaccard decides
    texts = list(groups)
    sh = [shingles(t) for t in texts]
    lsh = MinHashLSH(threshold=LSH_THRESHOLD, num_perm=NUM_PERM)
    for k, s in enumerate(sh):
        m = MinHash(num_perm=NUM_PERM)
        m.update_batch([x.encode("utf-8") for x in s])
        for c in lsh.query(m):
            score = jaccard(s, sh[c])
            if score >= JACCARD:
                link = _cross_author(groups[texts[c]], groups[texts[k]], authors)
                if link:
                    pairs.append(Pair(min(link), max(link), "near", round(score, 3)))
        lsh.insert(k, m)
    return pairs, norm


async def paraphrase_pairs(posts: list[Post], norm: list[str], linked: set[tuple[int, int]], embed) -> tuple[list[Pair], dict]:
    """deep=True: embedding pairs among posts >= 12 words (one embedding per distinct text, capped)."""
    import numpy as np

    by_text: dict[str, list[int]] = defaultdict(list)
    for i, t in enumerate(norm):
        if word_count(t) >= PARAPHRASE_MIN_WORDS:
            by_text[t].append(i)
    texts = list(by_text)[:MAX_EMBEDDINGS]
    info = {"embeddings": len(texts), "capped": len(by_text) > MAX_EMBEDDINGS}
    if len(texts) < 2:
        return [], info
    vectors, meta = await embed(texts)
    info.update(meta)
    v = np.asarray(vectors, dtype=np.float32)
    v /= np.linalg.norm(v, axis=1, keepdims=True) + 1e-12
    sims = v @ v.T
    authors = [p.author.lower() for p in posts]
    out = []
    idx_a, idx_b = np.where(np.triu(sims, 1) >= COSINE)
    for a, b in zip(idx_a.tolist(), idx_b.tolist()):
        link = _cross_author(by_text[texts[a]], by_text[texts[b]], authors)
        if link and (min(link), max(link)) not in linked:
            out.append(Pair(min(link), max(link), "paraphrase", round(float(sims[a, b]), 3)))
    return out, info
