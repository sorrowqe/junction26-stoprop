"""FastAPI app: routes and static files."""
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import config, taxonomy
from app.analyze import analyze_text

MAX_CHARS = 100_000  # hard cap on request size; word truncation (8000) happens later

app = FastAPI(title="Stoprop", version=config.VERSION, docs_url="/docs", redoc_url=None)
app.mount("/static", StaticFiles(directory=config.WEB_DIR / "static"), name="static")


class AnalyzeRequest(BaseModel):
    text: str | None = None
    url: str | None = None
    lang: str = "auto"
    sensitivity: Literal["strict", "balanced", "broad"] = "balanced"
    private: bool = False  # honoured by the shared cache in M2


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "version": config.VERSION,
        "commit": config.git_commit(),
        "budget": {"mode": "ok"},  # real budget guard arrives in M2
    }


@app.get("/api/techniques")
def techniques() -> dict:
    return taxonomy.public()


@app.post("/api/analyze")
def analyze(req: AnalyzeRequest) -> dict:
    text = req.text or ""
    if not text.strip():
        if req.url:
            raise HTTPException(400, "Analyzing a link is not available yet. Please paste the text instead.")
        raise HTTPException(400, "Please paste some text to analyze.")
    if len(text) > MAX_CHARS:
        raise HTTPException(413, f"Text is too long (max {MAX_CHARS:,} characters).")
    return analyze_text(text, sensitivity=req.sensitivity, lang=req.lang)


def _page(name: str) -> FileResponse:
    return FileResponse(config.WEB_DIR / f"{name}.html", media_type="text/html")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return _page("index")


for _name in ("source", "swarm", "metrics", "methodology", "api"):
    app.add_api_route(f"/{_name}", (lambda n=_name: _page(n)), methods=["GET"], include_in_schema=False)
