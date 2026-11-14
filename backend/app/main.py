"""FastAPI app: routes and static files."""
from fastapi import FastAPI
from fastapi.responses import FileResponse

from app import config

app = FastAPI(title="Stoprop", version=config.VERSION, docs_url=None, redoc_url=None)


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "version": config.VERSION,
        "commit": config.git_commit(),
        "budget": {"mode": "ok"},  # real budget guard arrives in M2
    }


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(config.WEB_DIR / "index.html", media_type="text/html")
