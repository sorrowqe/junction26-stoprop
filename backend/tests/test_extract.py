"""SSRF-safe fetching: every blocked case, redirects, DNS rebinding, limits. Network fully mocked:
a fake DNS resolver + a fake network backend that records every connection and serves canned
HTTP responses per IP address."""
import asyncio
import ipaddress

import httpcore2
import pytest

from app.pipeline import extract
from app.pipeline.extract import FetchError, check_url, ip_is_public

PUBLIC_IP = "93.184.216.34"
PUBLIC_IP2 = "151.101.1.140"
PUBLIC_V6 = "2606:4700:4700::1111"

ARTICLE_HTML = (
    "<html><head><title>Council approves meter plan</title>"
    '<meta property="article:published_time" content="2026-11-12"></head><body>'
    "<nav>Home | News | Sport</nav><article><h1>Council approves meter plan</h1>"
    + "".join(f"<p>Paragraph {i}: The Harbourton city council approved a plan on Tuesday to replace about "
              f"forty thousand electricity meters by the end of 2027, the council said in a statement.</p>"
              for i in range(4))
    + "</article><footer>Copyright</footer></body></html>"
)


def http_response(status=200, headers=None, body=b""):
    headers = {"Content-Type": "text/html; charset=utf-8", "Content-Length": str(len(body)), **(headers or {})}
    head = f"HTTP/1.1 {status} X\r\n" + "".join(f"{k}: {v}\r\n" for k, v in headers.items()) + "\r\n"
    return head.encode() + body


class FakeNet(httpcore2.AsyncNetworkBackend):
    """Inner network backend: never touches the network; records which IPs were connected to."""

    def __init__(self, routes, delay=0.0):
        self.routes = routes  # ip -> raw HTTP response bytes
        self.delay = delay
        self.connected = []

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.connected.append(host)
        if self.delay:
            await asyncio.sleep(self.delay)
        if host not in self.routes:
            raise httpcore2.ConnectError(f"no route to {host}")
        return httpcore2.AsyncMockStream([self.routes[host]])

    async def sleep(self, seconds):
        await asyncio.sleep(seconds)


@pytest.fixture
def net(monkeypatch):
    """Install fake DNS + fake network. Returns (dns dict, FakeNet)."""
    dns = {"example.com": [PUBLIC_IP], "www.example.com": [PUBLIC_IP], "other.example": [PUBLIC_IP2]}
    fake = FakeNet({})

    async def resolver(host, port):
        if host not in dns:
            import socket
            raise socket.gaierror("not found")
        value = dns[host]
        return value() if callable(value) else list(value)

    monkeypatch.setattr(extract, "resolver", resolver)
    monkeypatch.setattr(extract, "client_factory", lambda: extract.make_client(inner_backend=fake))
    return dns, fake


def fetch(url):
    return asyncio.run(extract.fetch_html(url))


def blocked(url):
    with pytest.raises(FetchError) as exc:
        fetch(url)
    return exc.value


# --------------------------------------------------------------- static checks

@pytest.mark.parametrize("url", [
    "http://127.0.0.1/", "http://127.0.0.1:8080/admin", "http://127.255.255.254/",
    "http://169.254.169.254/latest/meta-data/", "http://169.254.169.254:80/computeMetadata/v1/",
    "http://10.0.0.5/", "http://10.255.255.255/",
    "http://172.16.0.1/", "http://172.31.255.254/",
    "http://192.168.1.1/", "http://192.168.0.1:8443/",
    "http://100.64.0.1/", "http://100.127.255.254/",
    "http://0.0.0.0/", "http://224.0.0.1/", "http://255.255.255.255/",
    "http://[::1]/", "http://[::]/",
    "http://[fc00::1]/", "http://[fd12:3456::1]/",
    "http://[fe80::1]/", "http://[fe80::1%25eth0]/",
    "http://[::ffff:127.0.0.1]/", "http://[::ffff:169.254.169.254]/", "http://[::ffff:10.0.0.1]/",
    "http://[::ffff:7f00:1]/",
    "http://[64:ff9b::a9fe:a9fe]/",   # NAT64 form of 169.254.169.254
    "http://[2002:7f00:1::]/",        # 6to4 form of 127.0.0.1
    "http://[ff02::1]/",
])
def test_private_and_reserved_ip_literals_blocked(net, url):
    _, fake = net
    assert blocked(url).code == "blocked"
    assert fake.connected == []


@pytest.mark.parametrize("url", [
    "http://2130706433/",       # decimal 127.0.0.1
    "http://0x7f000001/",       # hex
    "http://0177.0.0.1/",       # octal
    "http://0x7f.0.0.1/",
    "http://127.1/",            # short form
    "http://017700000001/",
    "http://0/",
])
def test_numeric_ip_tricks_blocked(net, url):
    _, fake = net
    assert blocked(url).code in ("numeric_host", "blocked")
    assert fake.connected == []


@pytest.mark.parametrize("url", [
    "http://localhost/", "http://localhost:8000/", "http://LOCALHOST./", "http://api.localhost/",
    "http://metadata.google.internal/", "http://printer.local/", "http://router/", "http://intranet/",
])
def test_local_names_blocked(net, url):
    _, fake = net
    assert blocked(url).code == "blocked"
    assert fake.connected == []


@pytest.mark.parametrize("url, code", [
    ("ftp://example.com/file", "scheme"),
    ("file:///etc/passwd", "scheme"),
    ("gopher://example.com/", "scheme"),
    ("javascript:alert(1)", "scheme"),
    ("data:text/html,<h1>x</h1>", "scheme"),
    ("example.com", "scheme"),
    ("http://user:pass@example.com/", "credentials"),
    ("http://example.com@127.0.0.1/", "credentials"),
    ("http://example.com:22/", "port"),
    ("http://example.com:6379/", "port"),
    ("http://example.com:99999/", "bad_port"),
    ("http://example.com\\@127.0.0.1/", "bad_url"),
    ("http://exa mple.com/", "bad_url"),
    ("", "empty"),
])
def test_bad_urls_rejected(net, url, code):
    _, fake = net
    assert blocked(url).code == code
    assert fake.connected == []


# ------------------------------------------------------------ DNS-based checks

def test_public_name_resolving_to_loopback_blocked(net):
    dns, fake = net
    dns["evil.example"] = ["127.0.0.1"]
    assert blocked("http://evil.example/").code == "blocked"
    assert fake.connected == []


def test_name_with_mixed_public_and_private_answers_blocked(net):
    dns, fake = net
    dns["mixed.example"] = [PUBLIC_IP, "10.0.0.7"]
    assert blocked("http://mixed.example/").code == "blocked"
    assert fake.connected == []


def test_name_resolving_to_mapped_ipv6_private_blocked(net):
    dns, fake = net
    dns["v6.example"] = ["::ffff:10.0.0.1"]
    assert blocked("https://v6.example/").code == "blocked"


def test_name_resolving_to_metadata_blocked(net):
    dns, _ = net
    dns["meta.example"] = ["169.254.169.254"]
    assert blocked("http://meta.example/latest/meta-data/").code == "blocked"


def test_dns_rebinding_between_check_and_connect_blocked(net):
    dns, fake = net
    answers = iter([[PUBLIC_IP], ["127.0.0.1"], ["127.0.0.1"]])
    dns["rebind.example"] = lambda: next(answers)
    fake.routes[PUBLIC_IP] = http_response(body=b"<html>public</html>")
    assert blocked("http://rebind.example/").code == "blocked"
    assert "127.0.0.1" not in fake.connected


def test_unknown_host(net):
    assert blocked("http://does-not-exist.example/").code == "dns"


# ------------------------------------------------------------------ redirects

def test_redirect_from_public_to_private_ip_blocked(net):
    _, fake = net
    fake.routes[PUBLIC_IP] = http_response(302, {"Location": "http://169.254.169.254/latest/meta-data/"})
    assert blocked("http://example.com/start").code == "blocked"
    assert fake.connected == [PUBLIC_IP]  # never connected to the metadata address


def test_redirect_to_name_resolving_private_blocked(net):
    dns, fake = net
    dns["internal.example"] = ["192.168.1.10"]
    fake.routes[PUBLIC_IP] = http_response(301, {"Location": "https://internal.example/admin"})
    assert blocked("http://example.com/").code == "blocked"
    assert fake.connected == [PUBLIC_IP]


def test_redirect_to_localhost_and_file_blocked(net):
    _, fake = net
    fake.routes[PUBLIC_IP] = http_response(302, {"Location": "http://localhost:8000/api/health"})
    assert blocked("http://example.com/").code == "blocked"
    fake.routes[PUBLIC_IP] = http_response(302, {"Location": "file:///etc/passwd"})
    assert blocked("http://example.com/").code == "scheme"


def test_redirect_to_ipv6_loopback_blocked(net):
    _, fake = net
    fake.routes[PUBLIC_IP] = http_response(307, {"Location": "http://[::1]:8000/"})
    assert blocked("http://example.com/").code == "blocked"


def test_public_redirect_followed(net):
    _, fake = net
    fake.routes[PUBLIC_IP] = http_response(301, {"Location": "http://other.example/article"})
    fake.routes[PUBLIC_IP2] = http_response(body=ARTICLE_HTML.encode())
    got = fetch("http://example.com/a")
    assert got.final_url == "http://other.example/article"
    assert got.redirects == ["http://other.example/article"]
    assert fake.connected == [PUBLIC_IP, PUBLIC_IP2]


def test_too_many_redirects(net):
    dns, fake = net
    for i in range(5):
        dns[f"h{i}.example"] = [f"93.184.216.{40 + i}"]
        fake.routes[f"93.184.216.{40 + i}"] = http_response(302, {"Location": f"http://h{i + 1}.example/"})
    assert blocked("http://h0.example/").code == "too_many_redirects"
    assert len(fake.connected) == 4  # initial request + 3 redirects


# --------------------------------------------------------------- limits, types

def test_success_connects_to_vetted_ip_https(net):
    _, fake = net
    fake.routes[PUBLIC_IP] = http_response(body=ARTICLE_HTML.encode())
    got = fetch("https://example.com/news")
    assert "Council approves meter plan" in got.html
    assert fake.connected == [PUBLIC_IP]


def test_ipv6_public_literal_allowed(net):
    _, fake = net
    fake.routes[PUBLIC_V6] = http_response(body=b"<html><p>hi</p></html>")
    assert fetch(f"http://[{PUBLIC_V6}]/").html == "<html><p>hi</p></html>"


def test_non_html_rejected(net):
    _, fake = net
    fake.routes[PUBLIC_IP] = http_response(headers={"Content-Type": "application/pdf"}, body=b"%PDF")
    assert blocked("http://example.com/x.pdf").code == "content_type"


def test_too_large_by_header(net):
    _, fake = net
    fake.routes[PUBLIC_IP] = http_response(headers={"Content-Length": "5000000"}, body=b"")
    assert blocked("http://example.com/").code == "too_large"


def test_too_large_streamed_without_length(net):
    _, fake = net
    big = b"<p>" + b"a" * (extract.MAX_BYTES + 10) + b"</p>"
    raw = b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nConnection: close\r\n\r\n" + big
    fake.routes[PUBLIC_IP] = raw
    assert blocked("http://example.com/").code == "too_large"


def test_http_error_status(net):
    _, fake = net
    fake.routes[PUBLIC_IP] = http_response(404, body=b"nope")
    err = blocked("http://example.com/missing")
    assert err.code == "http_status" and "404" in err.message


def test_timeout(net, monkeypatch):
    _, fake = net
    monkeypatch.setattr(extract, "TIMEOUT_S", 0.2)
    fake.delay = 1.0
    fake.routes[PUBLIC_IP] = http_response(body=b"<html></html>")
    assert blocked("http://example.com/").code == "timeout"


def test_charset_from_meta():
    html = '<html><head><meta charset="windows-1251"></head><body>Привет</body></html>'.encode("cp1251")
    assert "Привет" in extract._decode(html, "text/html")


# ----------------------------------------------------------------- extraction

def test_fetch_article_extracts_main_text(net):
    _, fake = net
    fake.routes[PUBLIC_IP] = http_response(body=ARTICLE_HTML.encode())
    art = asyncio.run(extract.fetch_article("https://example.com/news"))
    assert art.title == "Council approves meter plan"
    assert art.date == "2026-11-12"
    assert "forty thousand electricity meters" in art.text
    assert "Home | News | Sport" not in art.text and "Copyright" not in art.text


def test_fetch_article_without_text(net):
    _, fake = net
    fake.routes[PUBLIC_IP] = http_response(body=b"<html><body><nav>menu</nav></body></html>")
    with pytest.raises(FetchError) as exc:
        asyncio.run(extract.fetch_article("https://example.com/"))
    assert exc.value.code == "no_text"


# ----------------------------------------------------------------- unit checks

@pytest.mark.parametrize("ip, public", [
    ("1.1.1.1", True), ("8.8.8.8", True), (PUBLIC_IP, True), (PUBLIC_V6, True),
    ("127.0.0.1", False), ("10.1.2.3", False), ("172.20.0.1", False), ("192.168.5.5", False),
    ("100.64.1.1", False), ("169.254.169.254", False), ("0.0.0.0", False), ("239.1.1.1", False),
    ("::1", False), ("fd00::1", False), ("fe80::1", False), ("::ffff:192.168.0.1", False),
    ("::ffff:8.8.8.8", True), ("64:ff9b::808:808", True), ("64:ff9b::a00:1", False),
    ("2001:0::1", False),  # Teredo
])
def test_ip_is_public(ip, public):
    assert ip_is_public(ipaddress.ip_address(ip)) is public


def test_check_url_normalizes_idn():
    t = check_url("https://bücher.example/path")
    assert t.host == "xn--bcher-kva.example" and t.port == 443
