"""FastAPI app: routes and static files."""
import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import config, ratelimit, taxonomy
from app.analyze import analyze_text, prewarm_samples
from app.llm import budget
from app.pipeline import extract
from app.receipts import service as receipts_service
from app.receipts.domain import DomainError, normalize_host
from app.store import get_store

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
    sensitivity: Literal["strict", "balanced", "broad"] = "balanced"
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


def _page(name: str) -> FileResponse:
    return FileResponse(config.WEB_DIR / f"{name}.html", media_type="text/html")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return _page("index")


for _name in ("source", "swarm", "metrics", "methodology", "api"):
    app.add_api_route(f"/{_name}", (lambda n=_name: _page(n)), methods=["GET"], include_in_schema=False)
