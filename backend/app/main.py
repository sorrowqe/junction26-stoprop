"""FastAPI app: routes and static files."""
import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import json

from app import config, languages, ratelimit, taxonomy
from app.analyze import analyze_text, prewarm_samples
from app.llm import budget
from app.pipeline import extract, oxylabs
from app.receipts import service as receipts_service
from app.receipts.domain import DomainError, normalize_host
from app.store import get_store
from app.swarm import clusters as swarm_clusters, ingest as swarm_ingest
from app.trace import fingerprint as trace_fp

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

MAX_CHARS = 100_000  # hard cap on request size; word truncation (8000) happens later


@asynccontextmanager
async def lifespan(_app: FastAPI):
    get_store()  # open SQLite early (falls back to memory if data/ is not writable)
    task = asyncio.create_task(prewarm_samples())
    yield
    task.cancel()


app = FastAPI(title="Stoprop", version=config.VERSION, docs_url="/docs", redoc_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=config.WEB_DIR / "static"), name="static")


class AnalyzeRequest(BaseModel):
    text: str | None = None
    url: str | None = None
    lang: str = "auto"
    # None = site default (strict for every language; calibrated in eval/calibrate.py)
    sensitivity: Literal["strict", "balanced", "broad"] | None = None
    private: bool = False  # analyze, but do not write to the shared cache


class ReceiptsRequest(BaseModel):
    url: str | None = None
    domain: str | None = None
    title: str | None = None   # optional: improves the fact-check keyword search
    refresh: bool = False      # re-check items older than 15 minutes


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "version": config.VERSION,
        "commit": config.git_commit(),
        "budget": budget.status(),
        "llm": {"configured": config.llm_configured(), "model": config.model_tagger()},
        "search": {"provider": config.active_search_provider(), "configured": config.active_search_provider() != "none"},
        "scraper": oxylabs.status(),
        "collectors": {
            "reddit": True,
            "telegram": True,
            "bluesky": True,
            "mastodon": True,
            "hackernews": True,
            "youtube": bool(config.env("GOOGLE_API_KEY")),
        },
        "storage": get_store().kind,
    }


RESULTS_DIR = config.REPO_ROOT / "eval" / "results"


def _read_result(name: str):
    path = RESULTS_DIR / name
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except ValueError:
        return None


@app.get("/api/metrics")
def metrics() -> dict:
    """Latest evaluation results (eval/results/*.json, committed) + language coverage."""
    return {"latest": _read_result("latest.json"), "bakeoff": _read_result("bakeoff.json"),
            "calibration": _read_result("calibration.json"), "self_consistency": _read_result("self_consistency.json"),
            "ai_review": _read_result("ai_review.json"), "swarm": _read_result("swarm_latest.json"),
            "gold_report": _read_result("gold_report.json"), "languages": languages.coverage()}


@app.get("/api/techniques")
def techniques() -> dict:
    return taxonomy.public()


async def _safe_receipts(target: str, title: str | None, quote: str | None = None) -> tuple[list, str | None]:
    try:
        result = await receipts_service.get_receipts(target, title=title, quote=quote)
        return result["receipts"], None
    except DomainError as exc:
        return [], f"No source receipts: {exc}"
    except Exception:  # noqa: BLE001 - receipts must never break an analysis
        logging.getLogger("stoprop.receipts").exception("receipts failed")
        return [], "Source receipts could not be checked right now."


@app.post("/api/analyze")
async def analyze(req: AnalyzeRequest, request: Request) -> dict:
    text = req.text or ""
    url = (req.url or "").strip()
    if not text.strip() and not url:
        raise HTTPException(400, "Please paste some text or a link to analyze.")
    if len(text) > MAX_CHARS:
        raise HTTPException(413, f"Text is too long (max {MAX_CHARS:,} characters).")
    ratelimit.enforce("analyze", request)

    source = None
    if not text.strip():
        try:
            article = await extract.fetch_article(url)
        except extract.FetchError as exc:
            raise HTTPException(exc.status, exc.message) from None
        text = article.text[:MAX_CHARS]
        source = {"url": article.url, "final_url": article.final_url, "title": article.title,
                  "date": article.date, "sitename": article.sitename, "redirects": article.redirects,
                  "via": article.via}

    receipts_target = source["final_url"] if source else url
    domain = None
    if receipts_target:
        try:
            domain = normalize_host(receipts_target)
        except DomainError:
            domain = None
    sample_quote = None
    if source:
        picks = trace_fp.heuristic(text)
        sample_quote = picks[0] if picks else None
    analysis, (receipts, receipts_note) = await asyncio.gather(
        analyze_text(text, sensitivity=req.sensitivity, lang=req.lang, private=req.private, domain=domain),
        _safe_receipts(receipts_target, source["title"] if source else None, quote=sample_quote) if receipts_target
        else asyncio.sleep(0, result=([], None)),
    )
    analysis["receipts"] = receipts
    if receipts_note:
        analysis["notes"].append(receipts_note)
    if source:
        source["domain"] = domain
        analysis["source"] = source
        analysis["text"] = text  # offsets refer to this extracted text
        if source["redirects"]:
            analysis["notes"].append(f"The link redirected to {source['final_url']}.")
    return analysis


@app.post("/api/receipts")
async def receipts(req: ReceiptsRequest, request: Request) -> dict:
    from app.receipts import pagecheck
    target = (req.url or req.domain or "").strip()
    if not target:
        raise HTTPException(400, "Please enter a link or a domain name.")
    if len(target) > 2048:
        raise HTTPException(400, "That link is too long.")
    try:
        normalize_host(target)
    except DomainError as exc:
        raise HTTPException(400, str(exc)) from None
    ratelimit.enforce("receipts", request)
    page = await pagecheck.inspect_target(req.url.strip() if (req.url and req.url.strip()) else target)
    eff_title = (req.title or "").strip() or ((page or {}).get("title") if (page or {}).get("is_article") else None)
    eff_quote = (page or {}).get("sample_quote") if page else None
    eff_target = (page or {}).get("final_url") if (page and page.get("reachable") and req.url) else target
    try:
        out = await receipts_service.get_receipts(eff_target, title=eff_title, quote=eff_quote, refresh=req.refresh)
    except DomainError as exc:
        raise HTTPException(400, str(exc)) from None
    if page is not None:
        out["page"] = {k: v for k, v in page.items() if k != "sample_quote"}
    return out


_SWARM_BODY = {"requestBody": {"content": {
    "application/json": {"schema": {"type": "object", "properties": {
        "data": {"type": "string", "description": "CSV or JSON text (id, author, timestamp, text; optional url, "
                                                  "author_created_at, platform)"},
        "posts": {"type": "array", "items": {"type": "object"}},
        "url": {"type": "string", "description": "a public post link (Bluesky, Mastodon, YouTube): the post + its replies"},
        "urls": {"type": "array", "items": {"type": "string"}, "description": "up to 50 post links, one post each"},
        "stream": {"type": "boolean", "default": False, "description": "link mode: NDJSON progress lines, then the result"},
        "deep": {"type": "boolean", "default": False, "description": "embedding paraphrase detection (costs credits)"},
        "tag_clusters": {"type": "boolean", "default": False, "description": "technique tags, max 10 LLM calls"}}}},
    "text/csv": {"schema": {"type": "string"}}}}}


async def _tag_text(text: str) -> tuple[list[str], dict]:
    r = await analyze_text(text, sensitivity="strict", private=True, count_seen=False)
    labels = sorted({s["label"] for s in r["spans"] if s.get("color") in ("red", "amber") and not s.get("reported_speech")})
    return labels, r["cost"]


@app.post("/api/swarm", openapi_extra=_SWARM_BODY)
async def swarm(request: Request, deep: bool = False, tag_clusters: bool = False) -> dict:
    """Coordinated-behaviour analysis of a batch of posts (max 2000 posts / 2 MB), in memory only.
    JSON body {"data": "<csv or json text>"} or {"posts": [...]}, or a raw CSV body (Content-Type: text/csv;
    options as query parameters). Link mode: {"url": "<post link>"} (the post and its replies) or
    {"urls": [...]} (up to 50 links, one post each); add "stream": true for NDJSON progress lines."""
    body = await request.body()
    if len(body) > swarm_ingest.MAX_BYTES + 65536:
        raise HTTPException(413, "The upload is larger than 2 MB.")
    try:
        if "json" in request.headers.get("content-type", ""):
            try:
                payload = json.loads(body or b"null")
            except json.JSONDecodeError:
                raise HTTPException(400, "The request body is not valid JSON.") from None
            if isinstance(payload, dict) and ("url" in payload or "urls" in payload):
                return await _swarm_links(payload, request, bool(payload.get("deep", deep)),
                                          bool(payload.get("tag_clusters", tag_clusters)))
            if isinstance(payload, dict) and ("data" in payload or "posts" in payload):
                deep = bool(payload.get("deep", deep))
                tag_clusters = bool(payload.get("tag_clusters", tag_clusters))
                batch = (swarm_ingest.parse_text(payload["data"]) if isinstance(payload.get("data"), str)
                         else swarm_ingest.from_json(payload.get("posts")))
            else:
                batch = swarm_ingest.from_json(payload)
        else:
            batch = swarm_ingest.parse_text(body.decode("utf-8", errors="replace"))
    except swarm_ingest.IngestError as exc:
        raise HTTPException(413 if "limit" in str(exc) or "2 MB" in str(exc) else 400, str(exc)) from None
    if not batch.posts:
        first = "; ".join(f"row {r['row']}: {r['reason']}" for r in batch.rejected[:3])
        raise HTTPException(400, f"No valid posts found ({batch.rejected_total} rows rejected{': ' + first if first else ''}).")
    ratelimit.enforce("swarm", request)
    return await _run_swarm(batch, deep, tag_clusters)


async def _run_swarm(batch, deep: bool, tag_clusters: bool) -> dict:
    llm_ok = budget.llm_available()[0]
    embed = None
    if deep and llm_ok:
        from app.llm import client as llm
        embed = lambda texts: llm.embed(texts, model=config.model_embed())  # noqa: E731
    return await swarm_clusters.analyze_batch(batch, deep=deep, tag_clusters=tag_clusters, embed=embed,
                                              tagger=_tag_text if (tag_clusters and llm_ok) else None)


COLLECT_TIMEOUT_S = 30


async def _swarm_links(payload: dict, request: Request, deep: bool, tag_clusters: bool):
    """Link mode: collect public posts (in memory, cached 10 min per link), then the same analysis."""
    from app.swarm import collectors
    from app.swarm.collectors.http import CollectorError
    multi = payload.get("urls") is not None
    try:
        if multi:
            urls = payload["urls"]
            if not isinstance(urls, list) or not all(isinstance(u, str) for u in urls):
                raise CollectorError("“urls” must be a list of links.", 400)
            collectors.validate_urls(urls)
        else:
            url = payload.get("url")
            if not isinstance(url, str):
                raise CollectorError("“url” must be a link.", 400)
            collectors.classify(url)
    except CollectorError as exc:
        raise HTTPException(exc.status, exc.message) from None
    ratelimit.enforce("swarm", request)

    async def run(progress) -> dict:
        async with asyncio.timeout(COLLECT_TIMEOUT_S):
            coll, cached = await (collectors.collect_urls(urls, progress) if multi else collectors.collect_url(url, progress))
        if progress:
            progress(f"Collected {len(coll.posts)} posts from {len({p.author.lower() for p in coll.posts})} accounts. "
                     "Looking for coordination…")
        batch = swarm_ingest.Batch(posts=list(coll.posts), rows_total=len(coll.posts))
        result = await _run_swarm(batch, deep, tag_clusters)
        result["source"] = {**coll.summary(), "cached": cached, "mode": "links" if multi else "thread"}
        result["notes"] = list(coll.notes) + result["notes"]
        if cached:
            result["notes"].append("These posts were collected less than 10 minutes ago and reused from memory.")
        return result

    def error(exc: Exception) -> tuple[int, str]:
        if isinstance(exc, CollectorError):
            return exc.status, exc.message
        if isinstance(exc, TimeoutError):
            return 504, "Collecting took too long. Please try again."
        logging.getLogger("stoprop.swarm").exception("link collection failed")
        return 500, "Something went wrong while collecting posts."

    return await _respond(run, error, bool(payload.get("stream")))


async def _respond(run, error, stream: bool):
    """Plain JSON, or NDJSON: {"progress": ...} lines while working, then {"result": ...} or {"error": ...}."""
    if not stream:
        try:
            return await run(None)
        except Exception as exc:  # noqa: BLE001 - mapped to a plain message
            status, msg = error(exc)
            raise HTTPException(status, msg) from None

    async def lines():
        queue: asyncio.Queue = asyncio.Queue()
        task = asyncio.create_task(run(queue.put_nowait))
        while True:
            getter = asyncio.ensure_future(queue.get())
            done, _ = await asyncio.wait({task, getter}, return_when=asyncio.FIRST_COMPLETED)
            if getter in done:
                yield json.dumps({"progress": getter.result()}) + "\n"
                continue
            getter.cancel()
            while not queue.empty():
                yield json.dumps({"progress": queue.get_nowait()}) + "\n"
            break
        try:
            yield json.dumps({"result": task.result()}, ensure_ascii=False) + "\n"
        except Exception as exc:  # noqa: BLE001
            status, msg = error(exc)
            yield json.dumps({"error": {"status": status, "detail": msg}}) + "\n"

    return StreamingResponse(lines(), media_type="application/x-ndjson")


class VerifyRequest(BaseModel):
    text: str
    stream: bool = False


@app.post("/api/verify")
async def verify_claims(req: VerifyRequest, request: Request):
    """Verify up to 3 check-worthy claims in a text: was the cited source really saying it, do independent outlets
    report it, do reports contradict it. Verdicts: cited_source_not_found | contradicted | corroborated |
    no_reports_found | not_checked (never true/false). <= 30 s, 10 checks/hour/IP, results cached 1 h."""
    from app.verify.run import VerifyError, verify
    text = (req.text or "").strip()
    if len(text.split()) < 5:
        raise HTTPException(400, "Please give a text of at least a few words to check.")
    if len(text) > 20_000:
        raise HTTPException(413, "That text is too long to check (max 20,000 characters).")
    ratelimit.enforce("verify", request)

    async def run(progress):
        async with asyncio.timeout(45):
            return await verify(text, progress)

    def error(exc: Exception) -> tuple[int, str]:
        if isinstance(exc, VerifyError):
            return exc.status, exc.message
        if isinstance(exc, TimeoutError):
            return 504, "Checking took too long. Please try again."
        logging.getLogger("stoprop.verify").exception("verify failed")
        return 500, "Something went wrong while checking the claims."

    return await _respond(run, error, req.stream)


class TraceRequest(BaseModel):
    text: str | None = None
    url: str | None = None
    include_forums: bool = False
    stream: bool = False


@app.post("/api/trace")
async def trace_post(req: TraceRequest, request: Request):
    """Trace where a claim, post, or article spread across the open web and public platforms, then run the swarm
    detector on the copies. Public data only; no identity research; nothing stored."""
    from app.swarm import collectors
    from app.swarm.collectors.http import CollectorError
    from app.trace import fingerprint
    from app.trace.run import MIN_QUERY_WORDS, TraceError, trace
    text, url = (req.text or "").strip(), (req.url or "").strip()
    if not text and not url:
        raise HTTPException(400, "Please paste text, a claim, or a link.")
    if url:
        host = collectors._host(url)
        for hosts, why in collectors.CLOSED.items():
            if host in hosts or any(host.endswith("." + h) for h in hosts):
                raise HTTPException(400, why + collectors.UPLOAD_HINT)
        try:
            extract.check_url(url)
        except extract.FetchError as exc:
            raise HTTPException(exc.status, exc.message) from None
    elif len(fingerprint.words(text)) < MIN_QUERY_WORDS:
        raise HTTPException(400, "Please enter at least 3 words (a claim, headline, or post text) or a public link.")
    elif len(text) > 20_000:
        raise HTTPException(413, "That text is too long to trace (max 20,000 characters).")
    ratelimit.enforce("trace", request)

    async def run(progress):
        async with asyncio.timeout(45):
            return await trace(text=None if url else text, url=url or None,
                               include_forums=req.include_forums, progress=progress)

    def error(exc: Exception) -> tuple[int, str]:
        if isinstance(exc, (TraceError, CollectorError)):
            return exc.status, exc.message
        if isinstance(exc, extract.FetchError):
            return exc.status, exc.message
        if isinstance(exc, TimeoutError):
            return 504, "Tracing took too long. Please try again."
        logging.getLogger("stoprop.trace").exception("trace failed")
        return 500, "Something went wrong while tracing."

    return await _respond(run, error, req.stream)


@app.middleware("http")
async def _no_stale_static(request: Request, call_next):
    resp = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/") or request.url.path in {
        "/source", "/swarm", "/metrics", "/methodology", "/api",
    }:
        resp.headers.setdefault("Cache-Control", "no-cache")
    return resp


def _page(name: str) -> FileResponse:
    return FileResponse(config.WEB_DIR / f"{name}.html", media_type="text/html", headers={"Cache-Control": "no-cache"})


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return _page("index")


@app.get("/trace", include_in_schema=False)
def trace_redirect() -> RedirectResponse:
    return RedirectResponse("/swarm", status_code=302)


for _name in ("source", "swarm", "metrics", "methodology", "api"):
    app.add_api_route(f"/{_name}", (lambda n=_name: _page(n)), methods=["GET"], include_in_schema=False)
