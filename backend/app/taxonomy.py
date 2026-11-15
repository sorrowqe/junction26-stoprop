"""Load config/techniques.yaml and apply gating (CLAUDE.md §4 step 5)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

TECHNIQUES_PATH = Path(__file__).resolve().parents[1] / "config" / "techniques.yaml"
SENSITIVITIES = ("strict", "balanced", "broad")


VALIDATED_PRECISION = 0.70
VALIDATED_MIN_PREDICTIONS = 15


def validated(entry: dict) -> bool:
    """Validated = measured dev precision >= 0.70 on >= 15 predictions. Everything else is "not yet validated"."""
    p, n = entry.get("dev_precision"), entry.get("dev_predictions") or 0
    return p is not None and p >= VALIDATED_PRECISION and n >= VALIDATED_MIN_PREDICTIONS


@lru_cache(maxsize=1)
def load() -> dict:
    data = yaml.safe_load(TECHNIQUES_PATH.read_text(encoding="utf-8"))
    for t in data["techniques"]:
        t["experimental"] = not validated(t)   # computed, never trusted from the file
    data["by_label"] = {t["label"]: t for t in data["techniques"]}
    return data


def version() -> int:
    return int(load()["version"])


def names() -> dict[str, str]:
    return {t["label"]: t.get("display_name") or t["name"] for t in load()["techniques"]}


def public() -> dict:
    """Public taxonomy for API callers, UI legend, and tooltips."""
    keys = ("label", "name", "kind", "color", "definition", "example")
    return {
        "version": version(),
        "labels": [
            {
                **{k: t.get(k) for k in keys},
                "name": t.get("display_name") or t["name"],
                "example": t.get("public_example") or t.get("example"),
                "thresholds": t.get("min_confidence"),
            }
            for t in load()["techniques"]
        ],
    }


def gate(spans: list[dict], sensitivity: str) -> list[dict]:
    """Drop disabled / unknown / below-threshold spans; set color and experimental.

    - colour comes only from the label type (techniques red, unsupported claims amber, supported claims green,
      balance blue); "experimental" means not yet validated (shown dashed in the UI, never hidden by default)
    - heuristic spans: always experimental; dropped at sensitivities in heuristics.drop_at
    - llm spans: dropped below min_confidence[sensitivity]
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
        s["color"] = entry["color"]
        if s.get("source") == "heuristic":
            if sensitivity in tax.get("heuristics", {}).get("drop_at", []):
                continue
            s["experimental"] = True
        else:
            if s.get("confidence", 0.0) < entry["min_confidence"][sensitivity]:
                continue
            s["experimental"] = bool(entry.get("experimental"))
        out.append(s)
    return out
