"""Env vars and settings. Read at call time (docker compose env_file; tests monkeypatch)."""
import os
from pathlib import Path

VERSION = "0.3.0"

REPO_ROOT = Path(__file__).resolve().parents[2]
WEB_DIR = REPO_ROOT / "web"
DATA_DIR = REPO_ROOT / "data"
DB_PATH = DATA_DIR / "stoprop.db"
PROMPTS_DIR = REPO_ROOT / "backend" / "prompts"


def env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def git_commit() -> str:
    """Short commit hash baked into the image at build time (Dockerfile ARG/ENV GIT_COMMIT)."""
    return env("GIT_COMMIT", "dev")


def router_base_url() -> str:
    return env("ROUTER_BASE_URL", "https://router.hostinger.com/v1")


def router_api_key() -> str:
    return env("ROUTER_API_KEY")


def model_tagger() -> str:
    return env("MODEL_TAGGER", "gemini-3.5-flash-lite-eu")


def model_summary() -> str:
    return env("MODEL_SUMMARY", "gemini-3.5-flash-lite-eu")


def search_provider() -> str:
    return env("SEARCH_PROVIDER", "none").lower()


def search_api_key() -> str:
    return env("SEARCH_API_KEY")


def model_embed() -> str:
    return env("MODEL_EMBED", "text-embedding-3-small-eu")


def llm_configured() -> bool:
    return bool(router_api_key() and router_base_url() and model_tagger())


def daily_token_cap() -> int:
    try:
        return max(0, int(env("DAILY_TOKEN_CAP", "2000000")))
    except ValueError:
        return 2_000_000
