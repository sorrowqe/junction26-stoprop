"""Swarm evaluation -> `eval/results/swarm_latest.json` (0 LLM calls)."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(HERE), str(ROOT / "backend")]

import make_benchmark as mb  # noqa: E402
from app.swarm import clusters  # noqa: E402
from app.swarm.ingest import Batch  # noqa: E402

OUT = ROOT / "eval" / "results" / "swarm_latest.json"


async def evaluate() -> dict:
    scenarios = mb.build_scenarios()
    tp = fp = fn = tn = 0
    rows = []
    for sc in scenarios:
        batch = Batch(posts=sc["posts"], rows_total=len(sc["posts"]), rejected_total=0, rejected=[])
        res = await clusters.analyze_batch(batch)
        flagged = [c for c in res["clusters"] if c["verdict"] == "likely_coordinated"]
        has_flag = len(flagged) > 0
        if sc["kind"] == "coordinated":
            if has_flag:
                tp += 1
            else:
                fn += 1
        else:
            if has_flag:
                fp += 1
            else:
                tn += 1
        top_verdict = res["clusters"][0]["verdict"] if res["clusters"] else "none"
        rows.append({
            "id": sc["id"],
            "kind": sc["kind"],
            "label": sc["label"],
            "posts": len(sc["posts"]),
            "top_verdict": top_verdict,
            "likely_coordinated_clusters": len(flagged),
            "passed": (has_flag if sc["kind"] == "coordinated" else not has_flag),
        })

    precision = round(tp / (tp + fp), 3) if (tp + fp) else None
    recall = round(tp / (tp + fn), 3) if (tp + fn) else None
    f1 = round(2 * precision * recall / (precision + recall), 3) if (precision and recall) else 0.0
    innocent_total = tn + fp
    fpr = round(fp / innocent_total, 3) if innocent_total else 0.0
    return {
        "scenarios": len(scenarios),
        "coordinated_scenarios": tp + fn,
        "innocent_scenarios": innocent_total,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "innocent_fpr": fpr,
        "details": rows,
    }


def main() -> int:
    report = asyncio.run(evaluate())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Swarm benchmark ({report['scenarios']} scenarios, 0 LLM calls): "
          f"P={report['precision']} R={report['recall']} F1={report['f1']} | "
          f"Innocent FPR={report['fp']}/{report['innocent_scenarios']} ({report['innocent_fpr']}) -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
