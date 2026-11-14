"""Domain normalization and registrable domain (offline Public Suffix List via `tld`)."""
from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit

from tld import get_tld

_HOST_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9-]{2,63}$")


class DomainError(ValueError):
    """Not a usable public domain name. str(exc) is safe to show to users."""


def normalize_host(value: str) -> str:
    """URL or domain -> lowercase ASCII (IDNA) hostname. Raises DomainError."""
    v = (value or "").strip()
    if not v:
        raise DomainError("Please enter a link or a domain name.")
    if "://" not in v:
        v = "http://" + v
    try:
        host = (urlsplit(v).hostname or "").rstrip(".").lower()
    except ValueError:
        raise DomainError("That does not look like a link or a domain name.") from None
    if not host:
        raise DomainError("That does not look like a link or a domain name.")
    try:
        ipaddress.ip_address(host)
        raise DomainError("Receipts work for domain names, not bare IP addresses.")
    except ValueError as exc:
        if isinstance(exc, DomainError):
            raise
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        raise DomainError("That domain name is not valid.") from None
    if not _HOST_RE.match(host):
        raise DomainError("That domain name is not valid.")
    return host


def split_registrable(host: str) -> tuple[str, str, str]:
    """(registrable domain, its label without suffix, public suffix). Falls back to the last two labels."""
    res = get_tld(host, fix_protocol=True, fail_silently=True, as_object=True)
    if res is not None and res.fld:
        return res.fld, res.domain, res.tld
    parts = host.split(".")
    return ".".join(parts[-2:]), parts[-2], parts[-1]


def registrable(host: str) -> str:
    return split_registrable(host)[0]


def to_unicode(host: str) -> str:
    try:
        return host.encode("ascii").decode("idna")
    except UnicodeError:
        return host
