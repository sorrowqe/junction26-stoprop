"""Gold sheets (CSV) -> JSONL for run_eval.py, with a seeded 70/30 dev/test split (test = LOCKED).

  python eval/sheet_to_jsonl.py            # English only (current evaluation scope)
  python eval/sheet_to_jsonl.py --lang all

Inputs: every eval/data/raw/*.csv except symmetry.csv (gold format, see goldcsv.py) + symmetry.csv.
Row decision: `ok` if filled (y/n); otherwise semeval/human rows are kept as labelled; otherwise `ai_ok`
(two-model consensus). Blank = reviewers disagreed or not reviewed -> EXCLUDED and counted.
An item (paragraph) is used only if ALL its rows are accepted; one rejected row drops the item.
Symmetry: a pair is kept only if both variants are fully accepted.
Rows in other languages are left untouched in the CSVs and reported as "not evaluated (language)".

Outputs: eval/data/dev/gold.jsonl, eval/data/test/gold.jsonl (gitignored, LOCKED: only run_eval.py --final),
eval/data/symmetry/symmetry.jsonl, eval/results/gold_report.json.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import goldcsv  # noqa: E402

RAW = ROOT / "eval" / "data" / "raw"
DEV = ROOT / "eval" / "data" / "dev" / "gold.jsonl"
TEST = ROOT / "eval" / "data" / "test" / "gold.jsonl"
SYM_OUT = ROOT / "eval" / "data" / "symmetry" / "symmetry.jsonl"
REPORT = ROOT / "eval" / "results" / "gold_report.json"
SPLIT_SEED = "stoprop-2026"
DEV_SHARE = 70
EXPERT_SOURCES = {"semeval", "human"}


def row_decision(r: dict) -> str:
    """'y' accepted, 'n' rejected, '' excluded (no consensus / not reviewed)."""
    ok = (r.get("ok") or "").strip().lower()[:1]
    if ok in ("y", "n"):
        return ok
    if r.get("source") in EXPERT_SOURCES:
        return "y"
    ai = (r.get("ai_ok") or "").strip().lower()[:1]
    return ai if ai in ("y", "n") else ""


def is_dev(item_id: str) -> bool:
    return int(hashlib.sha256(f"{SPLIT_SEED}:{item_id}".encode()).hexdigest(), 16) % 100 < DEV_SHARE


def _span(r: dict, problems: list) -> dict | None:
    if r["label"] == "none":
        return None
    text, quote = r["text"], r["quote"]
    if str(r.get("start", "")).strip() != "":
        s, e = int(r["start"]), int(r["end"])
        if text[s:e] != quote:
            problems.append(f"{r['id']}: offsets do not match quote {quote[:40]!r}")
            return {"invalid": True}
    else:  # human sheets without offsets: find the quote
        s = text.find(quote)
        if not quote or s < 0:
            problems.append(f"{r['id']}: quote not found {quote[:40]!r}")
            return {"invalid": True}
        e = s + len(quote)
    rs = (r.get("reported_speech") or "").strip().lower()[:1]
    return {"start": s, "end": e, "label": r["label"], "quote": quote,
            "reported_speech": True if rs == "y" else (False if rs == "n" else None)}


def build_items(rows: list[dict], lang: str) -> tuple[list[dict], dict]:
    report = {"per_label": defaultdict(Counter), "per_source": defaultdict(Counter), "problems": [],
              "not_evaluated_language_rows": 0}
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if lang != "all" and r.get("lang") != lang:
            report["not_evaluated_language_rows"] += 1
            continue
        groups[r["id"]].append(r)
    items = []
    for item_id, rs in groups.items():
        decisions = [row_decision(r) for r in rs]
        spans = [_span(r, report["problems"]) for r in rs]
        if any(s and s.get("invalid") for s in spans):
            status = "invalid"
        elif "n" in decisions:
            status = "dropped"
        elif "" in decisions:
            status = "excluded"
        else:
            status = "accepted"
        for r, d in zip(rs, decisions):
            report["per_label"][r["label"]][{"y": "accepted", "n": "dropped", "": "excluded"}[d]] += 1
        first = rs[0]
        report["per_source"][first["source"]][status] += 1
        if status != "accepted":
            continue
        good = [s for s in spans if s]
        items.append({
            "id": item_id, "source": first["source"], "lang": first["lang"], "genre": first.get("genre", ""),
            "kind": first.get("kind", ""), "subtype": first.get("notes", "") if first.get("kind") == "hard_negative" else "",
            "topic": first.get("topic", ""), "direction": first.get("direction", ""), "text": first["text"],
            "spans": sorted(good, key=lambda s: (s["start"], s["end"])),
            "clean_set": clean_set(first, good),
        })
    return items, report


def clean_set(first: dict, spans: list[dict]) -> str | None:
    """Which clean-text FPR set an item belongs to (None = not a clean item)."""
    if first["source"] == "semeval" and not spans:
        return "semeval_none"
    if first.get("kind") == "hard_negative":
        return f"{first['source']}_hard_negative"
    return None


def build_symmetry(rows: list[dict], lang: str) -> tuple[list[dict], Counter]:
    pairs: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if lang == "all" or r.get("lang") == lang:
            pairs[r["pair_id"]][r["variant"]].append(r)
    out, counts = [], Counter()
    for pair_id, variants in sorted(pairs.items()):
        if set(variants) != {"a", "b"}:
            counts["incomplete"] += 1
            continue
        decisions = [row_decision({**r, "source": r.get("source", "synthetic")}) for v in variants.values() for r in v]
        if not all(d == "y" for d in decisions):
            counts["dropped" if "n" in decisions else "excluded"] += 1
            continue
        rec = {"pair_id": pair_id, "lang": variants["a"][0]["lang"], "topic": variants["a"][0].get("topic", "")}
        for v in ("a", "b"):
            rec[v] = {"text": variants[v][0]["text"],
                      "labels": sorted({r["label"] for r in variants[v] if r["label"] != "none"})}
        out.append(rec)
        counts["accepted"] += 1
    return out, counts


def write_jsonl(path: Path, items: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lang", default="en", help="language to evaluate (default en; 'all' for every language)")
    args = ap.parse_args(argv)

    rows: list[dict] = []
    for path in sorted(RAW.glob("*.csv")):
        if path.name != "symmetry.csv":
            rows += goldcsv.read_csv(path)
    items, report = build_items(rows, args.lang)
    dev = [i for i in items if is_dev(i["id"])]
    test = [i for i in items if not is_dev(i["id"])]
    write_jsonl(DEV, dev)
    write_jsonl(TEST, test)
    sym_path = RAW / "symmetry.csv"
    pairs, sym_counts = build_symmetry(goldcsv.read_csv(sym_path), args.lang) if sym_path.exists() else ([], Counter())
    write_jsonl(SYM_OUT, pairs)

    out = {
        "language": args.lang,
        "items": {"dev": len(dev), "test": len(test)},
        "per_source": {k: dict(v) for k, v in report["per_source"].items()},
        "per_label": {k: dict(v) for k, v in sorted(report["per_label"].items())},
        "clean_sets": dict(Counter(i["clean_set"] for i in items if i["clean_set"])),
        "symmetry_pairs": dict(sym_counts),
        "not_evaluated_language_rows": report["not_evaluated_language_rows"],
        "problems": report["problems"],
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
