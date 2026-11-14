"""HTTP session for swarm collectors: a request budget, an overall deadline, the SSRF-safe client.

Every URL passes extract.check_url (scheme, port, no credentials, no local/numeric hosts) and every
connection goes through extract's GuardedBackend (public IPs only), which matters for Mastodon where
the instance host is user-supplied. No redirects are followed (APIs answer directly). Error messages
are generic and never contain the URL (the YouTube key travels in the query string).
"""
from __future__ import annotations

import asyncio
import json
import time

import httpx2

from app.pipeline import extract

MAX_REQUESTS = 40
DEADLINE_S = 25.0
REQUEST_TIMEOUT_S = 8.0
MAX_BYTES = 6_000_000


class CollectorError(Exception):
    """User-facing failure. `status` is the HTTP status we return to the browser."""

    def __init__(self, message: str, status: int = 502, code: str = "error"):
        super().__init__(message)
        self.message, self.status, self.code = message, status, code


class BudgetExhausted(Exception):
    """Request budget or deadline reached: stop collecting, keep what we have."""


class RateLimited(Exception):
    pass


class HTTPStatus(Exception):
    def __init__(self, status: int, body=None):
        super().__init__(f"HTTP {status}")
        self.status = status
        self.body = body  # parsed JSON error body when available (never shown verbatim)


def _default_client() -> httpx2.AsyncClient:
    return extract.make_client()


# Tests replace this with a factory returning a client on an httpx2.MockTransport.
client_factory = _default_client


class Session:
    def __init__(self, max_requests: int = MAX_REQUESTS, deadline_s: float = DEADLINE_S, progress=None):
        self.max_requests = max_requests
        self.deadline = time.monotonic() + deadline_s
        self.requests = 0
        self.progress = progress or (lambda msg: None)
        self._client: httpx2.AsyncClient | None = None

    async def __aenter__(self):
        self._client = client_factory()
        return self

    async def __aexit__(self, *exc):
        if self._client is not None:
            await self._client.aclose()

    def remaining(self) -> float:
        return self.deadline - time.monotonic()

    def can_request(self) -> bool:
        return self.requests < self.max_requests and self.remaining() > 1.0

    async def get_json(self, url: str, params=None, headers: dict | None = None):
        return await self.request_json("GET", url, params=params, headers=headers)

    async def post_json(self, url: str, payload: dict, headers: dict | None = None, timeout_s: float | None = None):
        return await self.request_json("POST", url, json_body=payload, headers=headers, timeout_s=timeout_s)

    async def request_json(self, method: str, url: str, *, params=None, json_body=None, headers: dict | None = None,
                           timeout_s: float | None = None):
        if self.requests >= self.max_requests:
            raise BudgetExhausted("request limit")
        if self.remaining() <= 1.0:
            raise BudgetExhausted("time limit")
        try:
            extract.check_url(url)
        except extract.FetchError as exc:
            raise CollectorError(exc.message, 400, "blocked") from None
        self.requests += 1
        try:
            async with asyncio.timeout(min(timeout_s or REQUEST_TIMEOUT_S, self.remaining())):
                async with self._client.stream(method, url, params=params, json=json_body,
                                               headers={"Accept": "application/json", "User-Agent": extract.USER_AGENT,
                                                        **(headers or {})}) as resp:
                    if resp.status_code == 429:
                        raise RateLimited()
                    if resp.status_code >= 400:
                        raw = bytearray()
                        async for chunk in resp.aiter_bytes():
                            raw += chunk
                            if len(raw) > 65536:
                                break
                        try:
                            err = json.loads(bytes(raw).decode("utf-8", errors="replace"))
                        except ValueError:
                            err = None
                        raise HTTPStatus(resp.status_code, err)
                    body = bytearray()
                    async for chunk in resp.aiter_bytes():
                        body += chunk
                        if len(body) > MAX_BYTES:
                            raise CollectorError("The platform sent more data than we accept in one response.", 502)
        except TimeoutError:
            if self.remaining() <= 1.0:
                raise BudgetExhausted("time limit") from None
            raise CollectorError("The platform did not answer in time. Please try again.", 504, "timeout") from None
        except (httpx2.HTTPError, extract.FetchError) as exc:
            if isinstance(exc, extract.FetchError) and exc.code == "blocked":
                raise CollectorError(exc.message, 400, "blocked") from None
            raise CollectorError("Could not reach the platform. Please try again later.", 502, "connect") from None
        try:
            return json.loads(bytes(body).decode("utf-8", errors="replace") or "null")
        except ValueError:
            raise CollectorError("The platform sent an answer we could not read.", 502, "bad_json") from None
