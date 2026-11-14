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


@app.post("/api/analyze")
async def analyze(req: AnalyzeRequest, request: Request) -> dict:
    text = req.text or ""
    if not text.strip():
        if req.url:
            raise HTTPException(400, "Analyzing a link is not available yet. Please paste the text instead.")
        raise HTTPException(400, "Please paste some text to analyze.")
    if len(text) > MAX_CHARS:
        raise HTTPException(413, f"Text is too long (max {MAX_CHARS:,} characters).")
    ratelimit.enforce("analyze", request)
    return await analyze_text(text, sensitivity=req.sensitivity, lang=req.lang, private=req.private)


def _page(name: str) -> FileResponse:
    return FileResponse(config.WEB_DIR / f"{name}.html", media_type="text/html")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return _page("index")


for _name in ("source", "swarm", "metrics", "methodology", "api"):
    app.add_api_route(f"/{_name}", (lambda n=_name: _page(n)), methods=["GET"], include_in_schema=False)
