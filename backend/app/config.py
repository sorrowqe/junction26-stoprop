"""Env vars and settings. Values are read from the environment (docker compose env_file)."""
import os
from pathlib import Path

VERSION = "0.1.0"

REPO_ROOT = Path(__file__).resolve().parents[2]
WEB_DIR = REPO_ROOT / "web"
DATA_DIR = REPO_ROOT / "data"


def git_commit() -> str:
    """Short commit hash baked into the image at build time (Dockerfile ARG/ENV GIT_COMMIT)."""
    return os.environ.get("GIT_COMMIT") or "dev"
