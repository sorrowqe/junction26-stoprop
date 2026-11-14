"""Per-client-IP sliding-window rate limits (in memory, single process).

Client IP: the RIGHTMOST X-Forwarded-For entry, i.e. the one our own proxy (Traefik) added.
Entries further left are supplied by the client and can be spoofed, so we never use them.
Without the header (local runs/tests) we use the socket peer address.
"""
from __future__ import annotations

import math
import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

WINDOW_S = 3600
LIMITS = {"analyze": 30, "swarm": 10, "receipts": 60, "feedback": 30}
NOUNS = {"analyze": "analyses", "swarm": "swarm runs", "receipts": "source checks", "feedback": "reports"}


def client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for", "")
    parts = [p.strip() for p in xff.split(",") if p.strip()]
    if parts:
        return parts[-1]
    return request.client.host if request.client else "unknown"


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[tuple[str, str], deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, bucket: str, key: str, now: float | None = None) -> tuple[bool, int]:
        """Record a request; return (allowed, retry_after_seconds)."""
        limit = LIMITS[bucket]
        now = time.monotonic() if now is None else now
        with self._lock:
            q = self._hits[(bucket, key)]
            while q and q[0] <= now - WINDOW_S:
                q.popleft()
            if len(q) >= limit:
                return False, max(1, math.ceil(q[0] + WINDOW_S - now))
            q.append(now)
            if len(self._hits) > 50_000:  # crude memory guard
                for k in [k for k, v in self._hits.items() if not v]:
                    del self._hits[k]
            return True, 0

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()


def enforce(bucket: str, request: Request) -> None:
    ok, retry_after = limiter.hit(bucket, client_ip(request))
    if not ok:
        minutes = max(1, math.ceil(retry_after / 60))
        raise HTTPException(
            status_code=429,
            detail=(f"You have reached the limit of {LIMITS[bucket]} {NOUNS[bucket]} per hour from your network. "
                    f"Please try again in about {minutes} minute{'s' if minutes != 1 else ''}."),
            headers={"Retry-After": str(retry_after)},
        )
