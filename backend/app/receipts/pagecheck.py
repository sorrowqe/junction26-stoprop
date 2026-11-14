"""Live page & accountability inspection for `/source` (`POST /api/receipts`).

Performs one fast, SSRF-safe HTTP fetch (`4.0 s` cap, 0 LLM/Oxylabs credits) to inspect redirects,
HTTPS, accountability links (About/Contact/Policy), article metadata (author, date, sample quote),
and HTML brand impersonation against `outlets.yaml`.
"""
from __future__ import annotations

import asyncio
import html as html_mod
import re
from urllib.parse import urlsplit

from app.pipeline import extract
from app.receipts import domain as dom
from app.receipts import lookalike
from app.trace import fingerprint

PAGE_TIMEOUT_S = 4.0

_TITLE_RE = re.compile(r"<title[^>]*>([\s\S]{1,400}?)</title>", re.I)
_META_RE = re.compile(r"<meta\s+([^>]{1,500})>", re.I)
_ATTR_RE = re.compile(r'([a-zA-Z0-9_:-]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\')')
_ANCHOR_RE = re.compile(r'<a\s+[^>]*href\s*=\s*(?:"([^"]*)"|\'([^\']*)\')[^>]*>([\s\S]{0,160}?)</a>', re.I)
_JSONLD_AUTHOR = re.compile(r'"author"\s*:\s*(?:\[\s*)?\{\s*"@type"\s*:\s*"(?:Person|Organization)"[^}]{0,300}?"name"\s*:\s*"([^"]{2,80})"', re.I)
_TAG_STRIP = re.compile(r"<[^>]+>")

_ABOUT_HREF = re.compile(r"(?:://info\.|/(?:about(?:-us)?|masthead|impressum|editorial(?:-team|-policy)?|who-we-are|meist|err-ist|organisatsioon|par-mums|apie-mus|uber-uns|ueber-uns))(?:[/?#.]|$)", re.I)
_ABOUT_TEXT = re.compile(r"\b(?:about us|about|masthead|impressum|editorial team|who we are|meist|err-ist|toimetus|о нас|редакция)\b", re.I)
_CONTACT_HREF = re.compile(r"^(?:mailto:|tel:)|/(?:contact(?:-us|s)?|kontakt(?:id|ai|i)?|feedback|redaktion|tagasiside)(?:[/?#.]|$)", re.I)
_CONTACT_TEXT = re.compile(r"\b(?:contact us|contacts?|kontakt(?:id)?|võta ühendust|tagasiside|обратная связь|контакты)\b", re.I)
_POLICY_HREF = re.compile(r"/(?:privacy|terms|ethics|corrections|standards|cookie|datenschutz|mentions-legales|privaatsus|andmekaitse|kupsised|tingimused)(?:[/?#.]|$)", re.I)
_POLICY_TEXT = re.compile(r"\b(?:privacy policy|privacy|editorial policy|corrections|code of ethics|terms of use|datenschutz|privaatsus|andmekaitse|küpsised)\b", re.I)


def _meta_map(raw_html: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in _META_RE.finditer(raw_html[:120_000]):
        attrs = {k.lower(): (v1 if v1 is not None else v2) for k, v1, v2 in _ATTR_RE.findall(m.group(1))}
        key = (attrs.get("property") or attrs.get("name") or "").strip().lower()
        val = html_mod.unescape(attrs.get("content") or "").strip()
        if key and val and key not in out:
            out[key] = val[:300]
    return out


def _detect_brand_impersonation(reg_domain: str, site_name: str | None, page_title: str | None) -> dict | None:
    official = lookalike.official_domains()
    if reg_domain in official:
        return None
    candidates = [s.strip() for s in (
        site_name,
        page_title.split("|")[-1].strip() if page_title and "|" in page_title else None,
        page_title.split("—")[-1].strip() if page_title and "—" in page_title else None,
    ) if s and 3 <= len(s.strip()) <= 60]
    if not candidates:
        return None
    for o in lookalike.outlets():
        oname = o["name"].split("(")[0].strip()
        if len(oname) < 3:
            continue
        for cand in candidates:
            if cand.lower() == oname.lower() or (len(oname) >= 6 and cand.lower() == o["domains"][0].lower()):
                return {
                    "outlet": o["name"],
                    "official_domain": o["domains"][0],
                    "claimed_name": cand,
                    "text": f"Page header identifies itself as “{cand}”, matching {o['name']} ({o['domains'][0]}), "
                            f"but {reg_domain} is not an official domain of that outlet.",
                }
    return None


def analyze_html(raw_html: str, requested_url: str, final_url: str, redirects: list[str]) -> dict:
    parts = urlsplit(final_url)
    path = parts.path or "/"
    is_article = path not in ("", "/") and len(path.strip("/")) >= 4
    reg = dom.registrable(dom.normalize_host(final_url))
    req_reg = dom.registrable(dom.normalize_host(requested_url))

    metas = _meta_map(raw_html)
    tm = _TITLE_RE.search(raw_html[:80_000])
    raw_title = html_mod.unescape(_TAG_STRIP.sub(" ", tm.group(1))).strip() if tm else ""
    raw_title = re.sub(r"\s+", " ", raw_title)[:300] or None
    title = metas.get("og:title") or metas.get("twitter:title") or raw_title
    site_name = metas.get("og:site_name") or metas.get("application-name")

    has_about = has_contact = has_policy = False
    scan_html = raw_html if len(raw_html) <= 300_000 else (raw_html[:150_000] + "\n" + raw_html[-150_000:])
    for m in _ANCHOR_RE.finditer(scan_html):
        href = (m.group(1) or m.group(2) or "").strip()
        txt = html_mod.unescape(_TAG_STRIP.sub(" ", m.group(3) or "")).strip()
        if not has_about and (_ABOUT_HREF.search(href) or _ABOUT_TEXT.search(txt)):
            has_about = True
        if not has_contact and (_CONTACT_HREF.search(href) or _CONTACT_TEXT.search(txt)):
            has_contact = True
        if not has_policy and (_POLICY_HREF.search(href) or _POLICY_TEXT.search(txt)):
            has_policy = True
        if has_about and has_contact and has_policy:
            break

    author = metas.get("author") or metas.get("article:author") or metas.get("parsely-author")
    if not author:
        jm = _JSONLD_AUTHOR.search(raw_html[:150_000])
        if jm:
            author = html_mod.unescape(jm.group(1)).strip()
    if author and (author.startswith("http") or len(author) > 90):
        author = None

    pub_date = (metas.get("article:published_time") or metas.get("datepublished")
                or metas.get("pubdate") or metas.get("date"))
    if pub_date:
        pub_date = pub_date[:10]

    sample_quote = None
    word_count = 0
    if is_article:
        body_text, info = extract.extract_main_text(raw_html, final_url)
        word_count = len(body_text.split())
        if not title and info.get("title"):
            title = info["title"]
        if not pub_date and info.get("date"):
            pub_date = str(info["date"])[:10]
        if word_count >= 15:
            picks = fingerprint.heuristic(body_text)
            sample_quote = picks[0] if picks else None

    brand_warning = _detect_brand_impersonation(reg, site_name, raw_title)
    return {
        "reachable": True,
        "requested_url": requested_url,
        "final_url": final_url,
        "final_domain": reg,
        "cross_domain_redirect": req_reg != reg,
        "redirects": redirects,
        "https": final_url.lower().startswith("https://"),
        "is_article": is_article,
        "title": title,
        "site_name": site_name,
        "author": author,
        "published_date": pub_date,
        "word_count": word_count,
        "sample_quote": sample_quote,
        "signals": {
            "https": final_url.lower().startswith("https://"),
            "about_link": has_about,
            "contact_link": has_contact,
            "policy_link": has_policy,
        },
        "brand_warning": brand_warning,
    }


async def _default_inspect(target: str) -> dict | None:
    raw = (target or "").strip()
    if not raw:
        return None
    url = raw if "://" in raw else f"https://{raw}/"
    try:
        fetched = await extract.fetch_html(url, timeout_s=PAGE_TIMEOUT_S)
    except extract.FetchError as exc:
        if "://" not in raw and exc.code in ("connect", "timeout"):
            try:
                fetched = await extract.fetch_html(f"http://{raw}/", timeout_s=2.5)
            except extract.FetchError:
                return {"reachable": False, "requested_url": url, "reason": exc.message}
        else:
            return {"reachable": False, "requested_url": url, "reason": exc.message}
    return await asyncio.to_thread(analyze_html, fetched.html, url, fetched.final_url, fetched.redirects)


inspect_target = _default_inspect
