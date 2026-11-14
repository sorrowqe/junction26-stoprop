"""Language coverage (config/languages.yaml): which languages have measured accuracy."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

PATH = Path(__file__).resolve().parents[1] / "config" / "languages.yaml"


@lru_cache(maxsize=1)
def load() -> dict:
    return yaml.safe_load(PATH.read_text(encoding="utf-8"))["languages"]


def is_evaluated(lang: str) -> bool:
    return bool(load().get(lang, load()["other"]).get("evaluated"))


def name(lang: str) -> str:
    return load().get(lang, load()["other"])["name"]


def default_sensitivity(lang: str) -> str:
    return "balanced" if is_evaluated(lang) else "strict"


def note(lang: str) -> str | None:
    if is_evaluated(lang):
        return None
    what = "this language" if lang == "other" else name(lang)
    return (f"Accuracy has only been measured for English; results for {what} are not measured, "
            "so strict sensitivity is used by default.")


def coverage() -> list[dict]:
    return [{"lang": k, "name": v["name"], "evaluated": bool(v.get("evaluated"))} for k, v in load().items()]
