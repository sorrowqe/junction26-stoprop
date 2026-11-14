"""Tiny HTTP helper for receipt sources (fixed public hosts): 8 s timeout, size cap, no env proxies.

Receipt sources follow redirects (rdap.org redirects to registry servers), so the client uses
the same guarded network backend as URL fetching: it can only ever connect to public IPs.
"""
from __future__ import annotations

import asyncio
import json

import httpx2

from app.pipeline import extract

TIMEOUT_S = 6.0


class SourceError(Exception):
    """A receipt source failed (timeout, HTTP error, bad JSON). Message is generic and safe to show."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class TooLarge(SourceError):
    pass


def _default_client() -> httpx2.AsyncClient:
    client = extract.make_client()
    client.follow_redirects = True
    client.max_redirects = 3
    return client


# Tests replace this with a factory returning a client on an httpx2.MockTransport.
client_factory = _default_client


async def get_json(url: str, *, params: dict | None = None, max_bytes: int = 2_000_000,
                   accept_404: bool = False, timeout_s: float | None = None):
    """GET and parse JSON. Returns None for 404 if accept_404. Raises SourceError / TooLarge."""
    try:
        async with asyncio.timeout(TIMEOUT_S if timeout_s is None else timeout_s):
            async with client_factory() as client:
                async with client.stream("GET", url, params=params, headers={"Accept": "application/json"}) as resp:
                    if resp.status_code == 404 and accept_404:
                        return None
                    if resp.status_code >= 400:
                        raise SourceError(f"HTTP {resp.status_code}", resp.status_code)
                    body = bytearray()
                    async for chunk in resp.aiter_bytes():
                        body += chunk
                        if len(body) > max_bytes:
                            raise TooLarge("response too large")
    except TimeoutError:
        raise SourceError("timeout") from None
    except (httpx2.HTTPError, extract.FetchError) as exc:
        raise SourceError(type(exc).__name__) from None
    try:
        return json.loads(bytes(body).decode("utf-8", errors="replace") or "null")
    except ValueError:
        raise SourceError("bad JSON") from None
