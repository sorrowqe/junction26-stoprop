"""Load config/techniques.yaml and apply gating (CLAUDE.md §4 step 5)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

TECHNIQUES_PATH = Path(__file__).resolve().parents[1] / "config" / "techniques.yaml"
SENSITIVITIES = ("strict", "balanced", "broad")


@lru_cache(maxsize=1)
def load() -> dict:
    data = yaml.safe_load(TECHNIQUES_PATH.read_text(encoding="utf-8"))
    data["by_label"] = {t["label"]: t for t in data["techniques"]}
    return data


def version() -> int:
    return int(load()["version"])


def names() -> dict[str, str]:
    return {t["label"]: t["name"] for t in load()["techniques"]}


def public() -> dict:
    """Taxonomy for the UI legend / tooltips."""
    keys = ("label", "name", "kind", "color", "definition", "example", "experimental", "dev_precision")
    return {"version": version(), "labels": [{k: t.get(k) for k in keys} for t in load()["techniques"]]}


def gate(spans: list[dict], sensitivity: str) -> list[dict]:
    """Drop disabled / unknown / below-threshold spans; set color and experimental.

    - heuristic spans: always amber + experimental; dropped at sensitivities in heuristics.drop_at
    - llm spans: dropped below min_confidence[sensitivity]; red techniques below amber_below -> amber
    """
    tax = load()
    if sensitivity not in SENSITIVITIES:
        sensitivity = "balanced"
    out = []
    for s in spans:
        entry = tax["by_label"].get(s.get("label"))
        if entry is None or not entry.get("enabled", True):
            continue
        s = dict(s)
        if s.get("source") == "heuristic":
            if sensitivity in tax.get("heuristics", {}).get("drop_at", []):
                continue
            s["color"] = "amber"
            s["experimental"] = True
        else:
            if s.get("confidence", 0.0) < entry["min_confidence"][sensitivity]:
                continue
            color = entry["color"]
            if color == "red" and (entry.get("experimental") or s["confidence"] < tax.get("amber_below", 0.7)):
                color = "amber"
            s["color"] = color
            s["experimental"] = bool(entry.get("experimental"))
        out.append(s)
    return out
