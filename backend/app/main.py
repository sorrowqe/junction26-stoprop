"""FastAPI app: routes and static files."""
import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import json

from app import config, languages, ratelimit, taxonomy
from app.analyze import analyze_text, prewarm_samples
from app.llm import budget
from app.pipeline import extract
from app.receipts import service as receipts_service
from app.receipts.domain import DomainError, normalize_host
from app.store import get_store
from app.swarm import clusters as swarm_clusters, ingest as swarm_ingest

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
            "ai_review": _read_result("ai_review.json"),
            "gold_report": _read_result("gold_report.json"), "languages": languages.coverage()}


@app.get("/api/techniques")
def techniques() -> dict:
    return taxonomy.public()


async def _safe_receipts(target: str, title: str | None) -> tuple[list, str | None]:
    try:
        result = await receipts_service.get_receipts(target, title=title)
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
                  "date": article.date, "sitename": article.sitename, "redirects": article.redirects}

    receipts_target = source["final_url"] if source else url
    domain = None
    if receipts_target:
        try:
            domain = normalize_host(receipts_target)
        except DomainError:
            domain = None
    analysis, (receipts, receipts_note) = await asyncio.gather(
        analyze_text(text, sensitivity=req.sensitivity, lang=req.lang, private=req.private, domain=domain),
        _safe_receipts(receipts_target, source["title"] if source else None) if receipts_target
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
    target = (req.url or req.domain or "").strip()
    if not target:
        raise HTTPException(400, "Please enter a link or a domain name.")
    ratelimit.enforce("receipts", request)
    try:
        return await receipts_service.get_receipts(target, title=req.title, refresh=req.refresh)
    except DomainError as exc:
        raise HTTPException(400, str(exc)) from None


_SWARM_BODY = {"requestBody": {"content": {
    "application/json": {"schema": {"type": "object", "properties": {
        "data": {"type": "string", "description": "CSV or JSON text (id, author, timestamp, text; optional url, "
                                                  "author_created_at, platform)"},
        "posts": {"type": "array", "items": {"type": "object"}},
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
    options as query parameters)."""
    body = await request.body()
    if len(body) > swarm_ingest.MAX_BYTES + 65536:
        raise HTTPException(413, "The upload is larger than 2 MB.")
    try:
        if "json" in request.headers.get("content-type", ""):
            try:
                payload = json.loads(body or b"null")
            except json.JSONDecodeError:
                raise HTTPException(400, "The request body is not valid JSON.") from None
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
    llm_ok = budget.llm_available()[0]
    embed = None
    if deep and llm_ok:
        from app.llm import client as llm
        embed = lambda texts: llm.embed(texts, model=config.model_embed())  # noqa: E731
    return await swarm_clusters.analyze_batch(batch, deep=deep, tag_clusters=tag_clusters, embed=embed,
                                              tagger=_tag_text if (tag_clusters and llm_ok) else None)


def _page(name: str) -> FileResponse:
    return FileResponse(config.WEB_DIR / f"{name}.html", media_type="text/html")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return _page("index")


for _name in ("source", "swarm", "metrics", "methodology", "api"):
    app.add_api_route(f"/{_name}", (lambda n=_name: _page(n)), methods=["GET"], include_in_schema=False)
