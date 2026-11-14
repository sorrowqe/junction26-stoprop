"""A — account similarity (CLAUDE.md §5). Per cluster; describes accounts' behaviour, never who they are.

- username pattern: names ending in >= 2 digits whose stems are within Levenshtein distance <= 2
  (<= 1 for stems of 3-5 characters, exact for shorter ones), >= 3 cluster authors in one group;
- creation dates: >= 3 authors (and >= half of those with a date) created within 7 days of each other;
- single-purpose accounts: >= 3 authors with >= 3 posts in the batch of which >= 70 % are cluster posts.
"""
from __future__ import annotations

import re

MIN_GROUP = 3
CREATION_WINDOW_S = 7 * 86400
DEDICATED_SHARE = 0.7
DEDICATED_MIN_POSTS = 3
_NAME = re.compile(r"^(.*?)[\W_]*(\d{2,})$")


def split_username(name: str) -> tuple[str, str]:
    """('truth_patriot', '481') for 'Truth_Patriot481'; (name, '') when there is no digit suffix."""
    n = name.strip().lstrip("@").lower()
    m = _NAME.match(n)
    if not m or not m.group(1):
        return n, ""
    return re.sub(r"[\W_]+", "_", m.group(1)).strip("_"), m.group(2)


def levenshtein(a: str, b: str, limit: int = 3) -> int:
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) > limit:
            return limit + 1
        prev = cur
    return prev[-1]


def _max_distance(a: str, b: str) -> int:
    shorter = min(len(a), len(b))
    return 2 if shorter >= 6 else 1 if shorter >= 3 else 0


def username_groups(authors: list[str]) -> list[list[str]]:
    """Groups (largest first) of authors with digit-suffixed names and similar stems."""
    named = [(a, split_username(a)[0]) for a in sorted(set(authors)) if split_username(a)[1]]
    parent = list(range(len(named)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for i in range(len(named)):
        for j in range(i + 1, len(named)):
            a, b = named[i][1], named[j][1]
            if levenshtein(a, b, 2) <= _max_distance(a, b):
                parent[find(i)] = find(j)
    groups: dict[int, list[str]] = {}
    for i, (name, _) in enumerate(named):
        groups.setdefault(find(i), []).append(name)
    return sorted((g for g in groups.values() if len(g) >= 2), key=len, reverse=True)


def creation_window(created: dict[str, float]) -> tuple[int, list[str]]:
    """Largest number of authors created within CREATION_WINDOW_S of each other."""
    items = sorted(created.items(), key=lambda kv: kv[1])
    best: list[str] = []
    j = 0
    for i in range(len(items)):
        while items[i][1] - items[j][1] > CREATION_WINDOW_S:
            j += 1
        if i - j + 1 > len(best):
            best = [a for a, _ in items[j:i + 1]]
    return len(best), best


def similarity(cluster_authors: list[str], created: dict[str, float], posts_per_author: dict[str, int],
               cluster_posts_per_author: dict[str, int]) -> dict:
    groups = username_groups(cluster_authors)
    names = {"present": bool(groups) and len(groups[0]) >= MIN_GROUP, "group": groups[0] if groups else []}
    known = {a: t for a, t in created.items() if a in set(cluster_authors) and t is not None}
    n_created, created_group = creation_window(known)
    creation = {"present": n_created >= MIN_GROUP and n_created * 2 >= len(known), "known": len(known),
                "within_7_days": n_created, "authors": created_group}
    dedicated = [a for a in cluster_authors if posts_per_author.get(a, 0) >= DEDICATED_MIN_POSTS
                 and cluster_posts_per_author.get(a, 0) / posts_per_author[a] >= DEDICATED_SHARE]
    single = {"present": len(dedicated) >= MIN_GROUP, "authors": dedicated,
              "eligible": sum(posts_per_author.get(a, 0) >= DEDICATED_MIN_POSTS for a in cluster_authors)}
    return {"present": names["present"] or creation["present"] or single["present"],
            "usernames": names, "creation": creation, "single_purpose": single}
