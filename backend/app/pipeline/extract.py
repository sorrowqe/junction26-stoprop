"""SSRF-safe URL fetching + main-text extraction (CLAUDE.md §4 POST /api/analyze `url`).

Defence in depth:
1. `check_url`: http/https only, no credentials, ports 80/443/8080/8443, no single-label or
   local names (localhost, *.local, *.internal ...), no non-canonical numeric hosts
   (2130706433, 0x7f.1, 0177.0.0.1, 127.1), IP literals must be public.
2. `resolve_public`: every DNS answer must be a public address (IPv4-mapped / 6to4 / NAT64 IPv6
   are unpacked and checked as IPv4; Teredo is blocked).
3. `GuardedBackend`: the connection layer resolves again and connects to the vetted IP itself,
   so DNS rebinding between check and connect cannot reach an internal address. TLS still
   verifies the certificate against the original hostname (SNI).
4. Redirects are followed manually (max 3) and every hop goes through 1-3 again.
5. 8 s total deadline, 2 MB body cap (streamed), text/html or application/xhtml+xml only,
   no proxies from the environment.
"""
from __future__ import annotations

import asyncio
import contextvars
import ipaddress
import re
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpcore2
import httpx2

from app import config

ALLOWED_SCHEMES = {"http", "https"}
ALLOWED_PORTS = {80, 443, 8080, 8443}
MAX_BYTES = 2_000_000
TIMEOUT_S = 5.0
MAX_REDIRECTS = 3
HTML_TYPES = {"text/html", "application/xhtml+xml"}
USER_AGENT = f"StopropBot/{config.VERSION} (+https://stoprop.tech/methodology)"

_BLOCKED_V4 = [ipaddress.ip_network(n) for n in (
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16", "172.16.0.0/12",
    "192.0.0.0/24", "192.0.2.0/24", "192.88.99.0/24", "192.168.0.0/16", "198.18.0.0/15",
    "198.51.100.0/24", "203.0.113.0/24", "224.0.0.0/4", "240.0.0.0/4", "255.255.255.255/32",
)]
_BLOCKED_V6 = [ipaddress.ip_network(n) for n in (
    "::/128", "::1/128", "fc00::/7", "fe80::/10", "fec0::/10", "ff00::/8", "100::/64", "2001:db8::/32",
    "2001::/32",  # Teredo
)]
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_LOCAL_SUFFIXES = (".localhost", ".local", ".internal", ".intranet", ".lan", ".home", ".corp", ".localdomain", ".arpa")


class FetchError(Exception):
    """A URL we refuse or could not fetch. `message` is safe to show to users."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


BLOCKED_MSG = "That address points to a private, local or reserved network. Stoprop only fetches public web pages."


def ip_is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return ip_is_public(ip.ipv4_mapped)
        if ip.sixtofour is not None:
            return ip_is_public(ip.sixtofour)
        if ip in _NAT64:
            return ip_is_public(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
        if any(ip in n for n in _BLOCKED_V6):
            return False
    elif any(ip in n for n in _BLOCKED_V4):
        return False
    return ip.is_global and not ip.is_multicast and not ip.is_reserved


@dataclass
class Target:
    url: str
    scheme: str
    host: str          # IDNA/ASCII hostname, or IP literal without brackets
    port: int
    ip_literal: bool


_NUMERIC_LABEL = re.compile(r"^(0x[0-9a-f]*|[0-9]+)$")


def check_url(url: str) -> Target:
    """Static checks (no DNS). Raises FetchError."""
    url = (url or "").strip()
    if not url:
        raise FetchError("empty", "Please enter a link.")
    if len(url) > 2048:
        raise FetchError("too_long", "That link is too long.")
    if re.search(r"[\x00-\x20\\]", url):
        raise FetchError("bad_url", "That link contains characters that are not allowed.")
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise FetchError("scheme", "Only http:// and https:// links can be analyzed.")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise FetchError("credentials", "Links with a username or password are not fetched.")
    try:
        port = parts.port or (443 if scheme == "https" else 80)
    except ValueError:
        raise FetchError("bad_port", "That link has an invalid port.") from None
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        raise FetchError("no_host", "That link has no host name.")

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        if not ip_is_public(ip):
            raise FetchError("blocked", BLOCKED_MSG)
        _check_port(port)
        return Target(url, scheme, host, port, True)

    labels = host.split(".")
    if all(_NUMERIC_LABEL.match(lbl) for lbl in labels if lbl):
        # 2130706433, 0x7f000001, 0177.0.0.1, 127.1: libc would read these as IP addresses
        raise FetchError("numeric_host", "Unusual numeric addresses are not fetched. Please use the normal link.")
    if len(labels) < 2 or host == "localhost" or host.endswith(_LOCAL_SUFFIXES):
        raise FetchError("blocked", BLOCKED_MSG)
    try:
        ascii_host = host.encode("idna").decode("ascii")
    except UnicodeError:
        raise FetchError("bad_host", "That host name is not valid.") from None
    if not re.fullmatch(r"[a-z0-9.-]+", ascii_host) or any(not lbl or len(lbl) > 63 for lbl in ascii_host.split(".")):
        raise FetchError("bad_host", "That host name is not valid.")
    _check_port(port)
    return Target(url, scheme, ascii_host, port, False)


def _check_port(port: int) -> None:
    # checked after the host, so "localhost:8000" is reported as a private address, not a port problem
    if port not in ALLOWED_PORTS:
        raise FetchError("port", "Only the standard web ports (80, 443, 8080, 8443) are allowed.")


async def _getaddrinfo(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


# Tests replace this with a fake resolver.
resolver = _getaddrinfo


async def resolve_public(host: str, port: int) -> list[str]:
    """All addresses for host; raises FetchError if any of them is not public."""
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        literal = None
    if literal is not None:
        if not ip_is_public(literal):
            raise FetchError("blocked", BLOCKED_MSG)
        return [str(literal)]
    try:
        addrs = await resolver(host, port)
    except (socket.gaierror, UnicodeError, OSError):
        raise FetchError("dns", "That website could not be found (DNS lookup failed).", 400) from None
    if not addrs:
        raise FetchError("dns", "That website could not be found (DNS lookup failed).", 400)
    v4, v6 = [], []
    for a in addrs:
        ip = ipaddress.ip_address(a.split("%")[0])
        if not ip_is_public(ip):
            raise FetchError("blocked", BLOCKED_MSG)
        (v4 if isinstance(ip, ipaddress.IPv4Address) else v6).append(str(ip))
    return list(dict.fromkeys(v4 + v6))


class GuardedBackend(httpcore2.AsyncNetworkBackend):
    """Network backend that only ever connects to vetted public IP addresses."""

    def __init__(self, inner: httpcore2.AsyncNetworkBackend | None = None):
        self._inner = inner or httpcore2.AnyIOBackend()
        self.connected: list[tuple[str, int]] = []  # for tests/diagnostics

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        ips = await resolve_public(host, port)
        last: Exception | None = None
        candidates = ips[:4]
        per_ip = (min(float(timeout), 2.5) if (timeout and len(candidates) > 1) else timeout)
        for ip in candidates:
            try:
                stream = await self._inner.connect_tcp(ip, port, timeout=per_ip, local_address=local_address,
                                                       socket_options=socket_options)
                self.connected.append((ip, port))
                return stream
            except Exception as exc:  # noqa: BLE001 - try the next address
                last = exc
        raise last or FetchError("connect", "Could not connect to that website.", 502)

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise FetchError("blocked", BLOCKED_MSG)

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


def make_client(inner_backend: httpcore2.AsyncNetworkBackend | None = None) -> httpx2.AsyncClient:
    transport = httpx2.AsyncHTTPTransport(trust_env=False)
    # Swap in a pool that uses the guarded backend (httpx2 has no public knob for this).
    transport._pool = httpcore2.AsyncConnectionPool(
        ssl_context=httpx2.create_ssl_context(trust_env=False),
        network_backend=GuardedBackend(inner_backend),
        max_connections=10,
    )
    return httpx2.AsyncClient(
        transport=transport, trust_env=False, follow_redirects=False,
        timeout=httpx2.Timeout(TIMEOUT_S), headers={"User-Agent": USER_AGENT},
    )


# Tests can replace this.
client_factory = make_client


@dataclass
class Fetched:
    url: str
    final_url: str
    content_type: str
    html: str
    redirects: list[str]


def _decode(body: bytes, content_type_header: str) -> str:
    m = re.search(r"charset=([\w-]+)", content_type_header or "", re.IGNORECASE)
    charset = m.group(1) if m else None
    if not charset:
        m = re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", body[:4096], re.IGNORECASE)
        charset = m.group(1).decode("ascii", "ignore") if m else "utf-8"
    try:
        return body.decode(charset, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


async def _fetch(url: str) -> Fetched:
    redirects: list[str] = []
    current = url
    async with client_factory() as client:
        for _hop in range(MAX_REDIRECTS + 1):
            target = check_url(current)
            await resolve_public(target.host, target.port)  # early, friendly error (connect re-checks)
            request = client.build_request("GET", current, headers={"Accept": "text/html,application/xhtml+xml"})
            try:
                resp = await client.send(request, stream=True)
            except FetchError:
                raise
            except httpx2.TimeoutException:
                raise FetchError("timeout", "The website took too long to answer.", 504) from None
            except httpx2.HTTPError:
                raise FetchError("connect", "Could not connect to that website.", 502) from None
            try:
                if resp.status_code in (301, 302, 303, 307, 308):
                    location = resp.headers.get("location")
                    if not location:
                        raise FetchError("redirect", "The website sent a broken redirect.", 502)
                    current = urljoin(current, location)
                    redirects.append(current)
                    continue
                if resp.status_code >= 400:
                    raise FetchError("http_status", f"The website answered with an error (HTTP {resp.status_code}).", 502)
                ctype_header = resp.headers.get("content-type", "")
                ctype = ctype_header.split(";")[0].strip().lower()
                if ctype not in HTML_TYPES:
                    raise FetchError("content_type", "That link is not a web page (only HTML pages can be analyzed).", 415)
                length = resp.headers.get("content-length")
                if length and length.isdigit() and int(length) > MAX_BYTES:
                    raise FetchError("too_large", "That page is too large (over 2 MB).", 413)
                body = bytearray()
                async for chunk in resp.aiter_bytes():
                    body += chunk
                    if len(body) > MAX_BYTES:
                        raise FetchError("too_large", "That page is too large (over 2 MB).", 413)
                return Fetched(url, current, ctype, _decode(bytes(body), ctype_header), redirects)
            finally:
                await resp.aclose()
    raise FetchError("too_many_redirects", f"The link redirects more than {MAX_REDIRECTS} times.", 400)


async def fetch_html(url: str, *, timeout_s: float | None = None) -> Fetched:
    limit = TIMEOUT_S if timeout_s is None else timeout_s
    try:
        async with asyncio.timeout(limit):
            return await _fetch(url)
    except TimeoutError:
        raise FetchError("timeout", f"The website took too long to answer ({limit:g} s limit).", 504) from None


# ------------------------------------------------------------------ extraction

@dataclass
class Article:
    url: str
    final_url: str
    title: str | None
    date: str | None
    sitename: str | None
    text: str
    redirects: list[str]
    via: str = "direct"


MIN_WORDS = 25
_RETRYABLE_HTTP = {401, 402, 403, 406, 408, 429, 451, 500, 502, 503, 504, 520, 521, 522, 523, 524, 525, 526}
_ALLOW_FALLBACK: contextvars.ContextVar[bool] = contextvars.ContextVar("extract_allow_fallback", default=True)
_DIRECT_TIMEOUT: contextvars.ContextVar[float | None] = contextvars.ContextVar("extract_direct_timeout", default=None)
_FALLBACK_TIMEOUT: contextvars.ContextVar[float] = contextvars.ContextVar("extract_fallback_timeout", default=28.0)


def extract_main_text(html: str, url: str) -> tuple[str, dict]:
    import trafilatura  # lazy: keeps startup light

    text = trafilatura.extract(html, url=url, favor_precision=True, include_comments=False,
                               include_tables=False, deduplicate=True) or ""
    meta = trafilatura.extract_metadata(html, default_url=url)
    info = {"title": getattr(meta, "title", None), "date": getattr(meta, "date", None),
            "sitename": getattr(meta, "sitename", None)}
    return text.strip(), info


async def _try_social_post(url: str) -> Article | None:
    """Tier 0: public social post links (Reddit post, Telegram post, Bluesky, Mastodon, YouTube) via collectors."""
    from datetime import datetime, timezone
    from app.swarm import collectors
    from app.swarm.collectors import bluesky, hackernews, mastodon, reddit, telegram, youtube
    from app.swarm.collectors.http import CollectorError

    rd = reddit.parse(url)
    tg = telegram.parse(url)
    is_single_social = (
        bluesky.parse(url)
        or mastodon.parse(url)
        or hackernews.parse(url)
        or (rd and rd[0] == "thread")
        or (tg and tg[1] is not None)
        or (youtube.parse(url) and config.env("GOOGLE_API_KEY"))
    )
    if not is_single_social:
        return None
    try:
        coll, _ = await collectors.collect_urls([url], cache=True)
    except CollectorError as exc:
        raise FetchError("social", exc.message, exc.status) from None
    if not coll.posts:
        raise FetchError("no_text", "That post has no text to analyze.", 422)
    p = coll.posts[0]
    if len(p.text.split()) < 3:
        raise FetchError("no_text", "That post has too little text to analyze.", 422)
    date_s = datetime.fromtimestamp(p.ts, timezone.utc).strftime("%Y-%m-%d") if p.ts else None
    site = collectors.PLATFORM_NAMES.get(p.platform, p.platform)
    return Article(url=url, final_url=p.url or url, title=f"Post by {p.author}", date=date_s,
                   sitename=site, text=p.text, redirects=[], via="social_api")


def _closed_social_reason(url: str) -> str | None:
    from app.swarm import collectors
    host = collectors._host(url)
    for hosts, why in collectors.CLOSED.items():
        if host in hosts or any(host.endswith("." + h) for h in hosts):
            return why + " Please paste the post's text directly instead."
    return None


def _should_fallback(exc: FetchError) -> bool:
    if exc.code in ("timeout", "connect", "no_text"):
        return True
    if exc.code == "http_status":
        m = re.search(r"HTTP (\d{3})", exc.message)
        return bool(m and int(m.group(1)) in _RETRYABLE_HTTP)
    return False


async def fetch_article(url: str, *, allow_fallback: bool | None = None, direct_timeout_s: float | None = None,
                        fallback_timeout_s: float | None = None) -> Article:
    """Fetch and extract article text:
    Tier 0: native social post API (Bluesky, Mastodon, YouTube)
    Tier 1: SSRF-safe direct HTTP + trafilatura
    Tier 2: Oxylabs AI Studio `/scrape` fallback when Tier 1 hits anti-bot/JS walls (after SSRF check).
    """
    use_fallback = _ALLOW_FALLBACK.get() if allow_fallback is None else allow_fallback
    d_timeout = _DIRECT_TIMEOUT.get() if direct_timeout_s is None else direct_timeout_s
    fb_timeout = _FALLBACK_TIMEOUT.get() if fallback_timeout_s is None else fallback_timeout_s

    check_url(url)
    closed_msg = _closed_social_reason(url)
    if closed_msg:
        raise FetchError("closed_platform", closed_msg, 422)

    if use_fallback:
        social = await _try_social_post(url)
        if social is not None:
            return social

    tier1_err: FetchError | None = None
    tier1_final = url
    tier1_redirects: list[str] = []
    tier1_info: dict = {"title": None, "date": None, "sitename": None}
    try:
        fetched = await fetch_html(url, timeout_s=d_timeout)
        tier1_final = fetched.final_url
        tier1_redirects = fetched.redirects
        text, tier1_info = await asyncio.to_thread(extract_main_text, fetched.html, fetched.final_url)
        if len(text.split()) >= MIN_WORDS:
            return Article(url=url, final_url=fetched.final_url, title=tier1_info["title"], date=tier1_info["date"],
                           sitename=tier1_info["sitename"], text=text, redirects=fetched.redirects, via="direct")
        tier1_err = FetchError("no_text", "Couldn't find the main text on that page. Please paste the text instead.", 422)
    except FetchError as exc:
        tier1_err = exc

    if use_fallback and tier1_err is not None and _should_fallback(tier1_err):
        from app.pipeline import oxylabs
        if oxylabs.status()["available"]:
            # Ensure target (and any redirected target) resolves strictly to public IPs before proxying.
            ft = check_url(tier1_final)
            await resolve_public(ft.host, ft.port)
            try:
                md_text, md_title = await oxylabs.scrape_markdown(tier1_final, timeout_s=fb_timeout)
                if len(md_text.split()) >= MIN_WORDS:
                    return Article(
                        url=url, final_url=tier1_final, title=tier1_info["title"] or md_title,
                        date=tier1_info["date"], sitename=tier1_info["sitename"], text=md_text,
                        redirects=tier1_redirects, via="oxylabs",
                    )
            except oxylabs.OxylabsError:
                pass  # surface the original user-friendly FetchError

    assert tier1_err is not None
    raise tier1_err
