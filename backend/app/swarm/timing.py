"""S — timing synchrony (CLAUDE.md §5).

Per cluster: the tightest window containing >= 60 % of the cluster's posts, compared with the dataset's
overall posting rate (posts per second over the whole batch, at least one hour). Under a Poisson model
with that rate, P(at least k posts in a window that short) must be < 0.001, with >= 3 distinct authors
inside the window. Per-author cadence regularity (coefficient of variation of inter-post intervals < 0.2
over >= 5 posts) is reported as supporting evidence only.
"""
from __future__ import annotations

import math
import statistics

from scipy.stats import poisson

SHARE = 0.6
P_MAX = 0.001
MIN_AUTHORS = 3
MIN_WINDOW_S = 60.0          # timestamps are often rounded to the minute
CADENCE_MIN_POSTS = 5
CADENCE_CV = 0.2


def dataset_rate(timestamps: list[float]) -> float:
    if not timestamps:
        return 0.0
    span = max(max(timestamps) - min(timestamps), 3600.0)
    return len(timestamps) / span


def tightest_window(ts: list[float], k: int) -> tuple[float, int]:
    """(width in seconds, start index) of the tightest window holding k of the sorted timestamps."""
    best, start = math.inf, 0
    for i in range(len(ts) - k + 1):
        w = ts[i + k - 1] - ts[i]
        if w < best:
            best, start = w, i
    return best, start


def synchrony(posts: list, rate: float) -> dict:
    """posts: cluster posts (with .ts, .author). Returns the S evidence dict."""
    items = sorted(posts, key=lambda p: p.ts)
    n = len(items)
    k = max(MIN_AUTHORS, math.ceil(SHARE * n))
    if n < MIN_AUTHORS or k > n:
        return {"present": False, "reason": "too few posts"}
    width, start = tightest_window([p.ts for p in items], k)
    window = items[start:start + k]
    authors = len({p.author.lower() for p in window})
    w = max(width, MIN_WINDOW_S)
    mu = rate * w
    p = float(poisson.sf(k - 1, mu)) if mu > 0 else 0.0
    return {"present": p < P_MAX and authors >= MIN_AUTHORS, "posts_in_window": k, "share": SHARE,
            "window_s": round(width, 1), "authors_in_window": authors, "expected": round(mu, 3), "p": p,
            "window_start": window[0].ts, "window_end": window[-1].ts}


def cadence(timestamps: list[float]) -> dict | None:
    """Regular posting: CV of inter-post intervals over >= 5 posts. None if not enough posts."""
    ts = sorted(timestamps)
    if len(ts) < CADENCE_MIN_POSTS:
        return None
    gaps = [b - a for a, b in zip(ts, ts[1:])]
    mean = statistics.fmean(gaps)
    if mean <= 0:
        return {"regular": True, "cv": 0.0, "mean_gap_s": 0.0, "posts": len(ts)}
    cv = statistics.pstdev(gaps) / mean
    return {"regular": cv < CADENCE_CV, "cv": round(cv, 3), "mean_gap_s": round(mean, 1), "posts": len(ts)}
