"""Parse Nginx/Apache Combined & Common access logs, Caddy/Traefik JSON logs, and CSV exports in memory."""
from __future__ import annotations

import csv
import io
import ipaddress
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone

MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 10_000
MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
          "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}

_COMBINED_RE = re.compile(
    r'^\s*(?P<ip>[0-9a-fA-F:.]+)\s+\S+\s+\S+\s+\[(?P<ts>[^\]]+)\]\s+'
    r'"(?P<req>[^"]*)"\s+(?P<status>\d{3})\s+(?P<bytes>\d+|-)'
    r'(?:\s+"(?P<ref>[^"]*)"\s+"(?P<ua>[^"]*)")?'
)
_CLF_TS_RE = re.compile(
    r"^(?P<d>\d{1,2})/(?P<m>[A-Za-z]{3})/(?P<y>\d{4}):(?P<H>\d{2}):(?P<M>\d{2}):(?P<S>\d{2})(?:\s*(?P<tz>[+-]\d{4}))?"
)


class LogParseError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message, self.status = message, status


@dataclass(frozen=True)
class LogEntry:
    ip: str
    ts: float
    method: str
    path: str
    status: int
    size: int
    ua: str
    referer: str = ""


def _clean_ip(raw: str) -> str | None:
    s = (raw or "").strip().split(",")[0].strip()
    if s.startswith("[") and "]" in s:
        s = s[1:s.index("]")]
    elif s.count(":") == 1 and "." in s:
        s = s.split(":", 1)[0]
    try:
        return str(ipaddress.ip_address(s))
    except ValueError:
        return None


def is_public_ip(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
        return a.is_global and not (a.is_private or a.is_loopback or a.is_link_local or a.is_multicast or a.is_reserved)
    except ValueError:
        return False


def _parse_ts(raw: str | int | float | None) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        v = float(raw)
        return v / 1000.0 if v > 1e12 else v
    s = str(raw).strip()
    if not s:
        return None
    try:
        v = float(s)
        return v / 1000.0 if v > 1e12 else v
    except ValueError:
        pass
    m = _CLF_TS_RE.match(s)
    if m and m.group("m").lower() in MONTHS:
        try:
            dt = datetime(int(m.group("y")), MONTHS[m.group("m").lower()], int(m.group("d")),
                          int(m.group("H")), int(m.group("M")), int(m.group("S")), tzinfo=timezone.utc)
            tz = m.group("tz")
            off = (1 if tz[0] == "+" else -1) * (int(tz[1:3]) * 3600 + int(tz[3:5]) * 60) if tz else 0
            return dt.timestamp() - off
        except ValueError:
            return None
    iso = s[:-1] + "+00:00" if s.endswith("Z") else s
    try:
        dt = datetime.fromisoformat(iso)
        return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()
    except ValueError:
        return None
def _split_req(req: str) -> tuple[str, str]:
    parts = (req or "").strip().split()
    if len(parts) >= 2:
        return parts[0].upper(), parts[1]
    if len(parts) == 1:
        return "GET", parts[0]
    return "GET", "/"


def parse_logs(raw: str) -> tuple[list[LogEntry], dict]:
    """Return (entries, stats) from Combined/Common log lines, JSON lines (Caddy/Traefik), or CSV."""
    if not raw or not raw.strip():
        raise LogParseError("Please paste web-server log lines or upload an access.log / JSON / CSV file.")
    if len(raw.encode("utf-8", errors="ignore")) > MAX_BYTES:
        raise LogParseError("Log input exceeds the 2 MB limit. Please upload a recent slice (up to 10,000 lines).", 413)

    lines = [ln for ln in raw.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    if not lines:
        raise LogParseError("No log entries found in the input.")

    entries: list[LogEntry] = []
    skipped = 0
    fmt = "combined"
    first_low = lines[0].lower()

    if not first_low.lstrip().startswith("{") and "," in first_low and any(k in first_low for k in ("ip", "remote_addr", "client_ip")) and any(k in first_low for k in ("path", "uri", "url", "status")):
        fmt = "csv"
        reader = csv.DictReader(io.StringIO("\n".join(lines[:MAX_ROWS + 1])))
        for row in reader:
            norm = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            ip = _clean_ip(norm.get("ip") or norm.get("client_ip") or norm.get("remote_addr") or "")
            ts = _parse_ts(norm.get("timestamp") or norm.get("ts") or norm.get("time"))
            path = norm.get("path") or norm.get("uri") or norm.get("url") or "/"
            method = (norm.get("method") or "GET").upper()
            st = int(norm["status"]) if (norm.get("status") or "").isdigit() else 200
            sz = int(norm["bytes"]) if (norm.get("bytes") or "").isdigit() else 0
            ua = norm.get("user_agent") or norm.get("ua") or ""
            if ip and ts is not None:
                entries.append(LogEntry(ip=ip, ts=ts, method=method, path=path, status=st, size=sz, ua=ua))
            else:
                skipped += 1
    else:
        for ln in lines[:MAX_ROWS]:
            s = ln.strip()
            if s.startswith("{"):
                fmt = "json"
                try:
                    obj = json.loads(s)
                except ValueError:
                    skipped += 1
                    continue
                req_o = obj.get("request") if isinstance(obj.get("request"), dict) else {}
                hdrs = req_o.get("headers") if isinstance(req_o.get("headers"), dict) else {}
                ua_v = hdrs.get("User-Agent") or hdrs.get("user-agent") or obj.get("user_agent") or obj.get("ClientRequestUserAgent") or ""
                if isinstance(ua_v, list):
                    ua_v = ua_v[0] if ua_v else ""
                ip = _clean_ip(str(req_o.get("client_ip") or req_o.get("remote_ip") or obj.get("client_ip") or obj.get("ip") or ""))
                ts = _parse_ts(obj.get("ts") or obj.get("time"))
                method = str(req_o.get("method") or obj.get("method") or "GET").upper()
                path = str(req_o.get("uri") or obj.get("uri") or obj.get("path") or "/")
                st_raw = obj.get("status") or 200
                st = int(st_raw) if str(st_raw).isdigit() else 200
                sz_raw = obj.get("size") or obj.get("bytes") or 0
                sz = int(sz_raw) if str(sz_raw).isdigit() else 0
                if ip and ts is not None:
                    entries.append(LogEntry(ip=ip, ts=ts, method=method, path=path, status=st, size=sz, ua=str(ua_v)))
                else:
                    skipped += 1
                continue

            m = _COMBINED_RE.match(s)
            if not m:
                skipped += 1
                continue
            ip = _clean_ip(m.group("ip"))
            ts = _parse_ts(m.group("ts"))
            method, path = _split_req(m.group("req"))
            st = int(m.group("status"))
            sz = 0 if m.group("bytes") == "-" else int(m.group("bytes"))
            ua = m.group("ua") or ""
            ref = "" if (m.group("ref") or "-") == "-" else (m.group("ref") or "")
            if ip and ts is not None:
                entries.append(LogEntry(ip=ip, ts=ts, method=method, path=path, status=st, size=sz, ua=ua, referer=ref))
            else:
                skipped += 1

    if not entries:
        raise LogParseError(
            "Could not parse any log lines. Supported formats: Nginx/Apache Combined or Common access.log, "
            "Caddy/Traefik JSON lines, or CSV (ip, timestamp, method, path, status, user_agent)."
        )
    return entries, {"format": fmt, "parsed": len(entries), "skipped": skipped, "capped": len(lines) > MAX_ROWS}