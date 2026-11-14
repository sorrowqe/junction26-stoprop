"""Two-model consensus review of the ENGLISH synthetic gold rows (no human review is planned).

Reviewers: two different model families, neither GPT (the generator) nor Claude (the tagger):
gemini-3.5-flash-eu and mistral-medium-3.5-eu. Each reviewer judges every gold row y/n, checks the
reported-speech rule (direct quotes AND attributed indirect speech count) and, per item, looks for
manipulation techniques the gold rows missed. Only agreements are written:

  ai_ok = y   both reviewers accept the row          ai_ok = n   both reject it
  ai_ok = ""  reviewers disagree -> sheet_to_jsonl.py EXCLUDES the item and counts it
  reported_speech is corrected only if both reviewers propose the same correction.
  hard negatives / claim items / neutral symmetry pairs: if BOTH reviewers find an unmarked author technique,
  all rows of the item become n; if only one does, the item is excluded (blank).
  symmetry pairs: if both reviewers say the two variants are not the same text with sides swapped -> n.

  python eval/ai_review.py                 # dry run: items, calls, token estimate
  python eval/ai_review.py --first-batch   # first batch for both reviewers (2 calls), prints credits + projection
  python eval/ai_review.py --yes           # everything (first batch comes from the cache), writes CSVs + report

Writes ai_ok / ai_reasons into eval/data/raw/synthetic_gold.csv and symmetry.csv (ET/RU rows untouched)
and eval/results/ai_review.json (Cohen's kappa, accepted/dropped/excluded per label).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "backend"))

import goldcsv  # noqa: E402
from app import taxonomy  # noqa: E402

REVIEWERS = ["gemini-3.5-flash-eu", "mistral-medium-3.5-eu"]
LANG = "en"
BATCH = 6
MAX_TOKENS = 8000
# Gemini 3.5 Flash "thinks" before answering; with the default budget the thinking used the whole output limit
# and the JSON was cut off (first batch, 2026-11-14). Keep its reasoning low.
MODEL_OPTIONS = {"gemini-3.5-flash-eu": {"reasoning_effort": "low"}}
CACHE_DIR = ROOT / "eval" / "data" / "cache" / "ai_review"
GOLD = ROOT / "eval" / "data" / "raw" / "synthetic_gold.csv"
SYM = ROOT / "eval" / "data" / "raw" / "symmetry.csv"
REPORT = ROOT / "eval" / "results" / "ai_review.json"
EST_IN_PER_CALL = 2600   # system prompt (~1.3k) + 6 items
EST_OUT_PER_CALL = 900


# ------------------------------------------------------------------- items

def load_items(gold_rows: list[dict], sym_rows: list[dict], lang: str = LANG) -> list[dict]:
    """Group English rows into review items. Row references are (file, index) into the CSV row lists."""
    items: list[dict] = []
    by_id: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(gold_rows):
        if r.get("lang") == lang and r.get("source") == "synthetic":
            by_id[r["id"]].append(i)
    for item_id, idxs in by_id.items():
        first = gold_rows[idxs[0]]
        items.append({
            "item_id": item_id, "file": "gold", "kind": first["kind"], "subtype": first.get("notes", ""),
            "variants": [{"variant": "", "text": first["text"],
                          "rows": [{"ref": ("gold", i), "quote": gold_rows[i]["quote"], "label": gold_rows[i]["label"],
                                    "reported_speech": gold_rows[i].get("reported_speech", "")} for i in idxs]}],
        })
    by_pair: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    for i, r in enumerate(sym_rows):
        if r.get("lang") == lang:
            by_pair[r["pair_id"]][r["variant"]].append(i)
    for pair_id, variants in by_pair.items():
        neutral = all(sym_rows[i]["label"] == "none" for idxs in variants.values() for i in idxs)
        items.append({
            "item_id": pair_id, "file": "sym", "kind": "symmetry_neutral" if neutral else "symmetry",
            "subtype": "", "variants": [
                {"variant": v, "text": sym_rows[idxs[0]]["text"],
                 "rows": [{"ref": ("sym", i), "quote": sym_rows[i]["quote"], "label": sym_rows[i]["label"],
                           "reported_speech": ""} for i in idxs]}
                for v, idxs in sorted(variants.items())],
        })
    return items


def number_rows(item: dict) -> list[dict]:
    """Flat list of the item's rows with 1-based numbers (as shown to the reviewers)."""
    out, n = [], 0
    for v in item["variants"]:
        for r in v["rows"]:
            n += 1
            out.append({**r, "n": n, "variant": v["variant"]})
    return out


# ------------------------------------------------------------------ prompt

def system_prompt() -> str:
    defs = "\n".join(f"- {t['label']}: {t['definition']}" for t in taxonomy.load()["techniques"])
    return f"""You are an independent reviewer of a gold-standard test set for a manipulation-technique detector.
The detector labels HOW something is said, never which political side is right. Judge every row strictly and
neutrally; the same wording must be judged the same way whichever side it favours.

Labels:
{defs}
- none: the row claims the text contains no manipulation technique used by its author.

Reported speech rule: wording attributed to someone other than the author is reported speech - direct quotes AND
attributed indirect speech ("X said that ...", "X called it ...", "according to X, ...", "..., the minister said").
First-person attribution ("I said") and vague attributions ("experts say", "sources claim") are NOT reported speech.

For each item you get the text (or two variants a/b of a symmetry pair) and numbered gold rows (quote, label,
reported_speech). For EACH row answer:
- "verdict": "y" if the label correctly describes this exact phrase and the phrase is a sensible span, else "n".
  For label "none": "y" only if the text really contains no manipulation technique used by its author.
- "reported_speech": "y" or "n" according to the rule above (for label "none" use "n").
- "reason": at most 15 words.
For each ITEM also answer:
- "unmarked": list of {{"quote": exact words, "label": technique}} for manipulation techniques used by the AUTHOR
  (not reported speech) that no row covers. Claims and balance markers do not count here. Empty list if none.
- for symmetry pairs only, "symmetric": "y" if variant b is the same text as a with the sides/entities swapped and
  the same techniques, else "n".

Reply with JSON only: {{"items": [{{"item_id": "...", "rows": [{{"n": 1, "verdict": "y", "reported_speech": "n",
"reason": "..."}}], "unmarked": [], "symmetric": "y"}}]}}"""


def item_payload(item: dict) -> dict:
    rows = number_rows(item)
    payload = {"item_id": item["item_id"]}
    if item["file"] == "sym":
        payload["variants"] = {v["variant"]: v["text"] for v in item["variants"]}
        payload["rows"] = [{"n": r["n"], "variant": r["variant"], "quote": r["quote"], "label": r["label"]} for r in rows]
    else:
        payload["text"] = item["variants"][0]["text"]
        payload["rows"] = [{"n": r["n"], "quote": r["quote"], "label": r["label"],
                            "reported_speech": r["reported_speech"] or "n"} for r in rows]
    return payload


def messages(batch: list[dict]) -> list[dict]:
    body = json.dumps([item_payload(i) for i in batch], ensure_ascii=False, indent=1)
    return [{"role": "system", "content": system_prompt()},
            {"role": "user", "content": f"Review these items:\n{body}"}]


def batches(items: list, size: int = BATCH) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)]


# ------------------------------------------------------------------- model

def _load_env_local() -> None:
    path = ROOT / ".env.local"
    if not path.exists() or os.environ.get("ROUTER_API_KEY"):
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.split(" #", 1)[0].strip().strip("'\""))


def _find_credits(headers: dict, body: dict) -> dict:
    """Any billing info the router returns (header or body field mentioning credit/cost/charge)."""
    found = {}
    for k, v in headers.items():
        if any(w in k.lower() for w in ("credit", "cost", "charge", "billing")):
            found[f"header:{k}"] = v

    def walk(obj, path=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                p = f"{path}.{k}" if path else k
                if any(w in k.lower() for w in ("credit", "cost", "charge")) and not isinstance(v, (dict, list)):
                    found[f"body:{p}"] = v
                walk(v, p)
    walk(body)
    return found


def credits_value(found: dict) -> float | None:
    for key in sorted(found, key=lambda k: ("credits_charged" not in k, k)):
        try:
            return float(found[key])
        except (TypeError, ValueError):
            continue
    return None


class Reviewer:
    def __init__(self, model: str, call=None):
        self.model = model
        self.calls = self.cached = self.tokens_in = self.tokens_out = self.truncated = 0
        self.credits: list[float] = []
        self.credit_fields: dict = {}
        self._call = call or self._router_call

    def _router_call(self, msgs: list[dict]) -> dict:
        from openai import OpenAI
        client = OpenAI(base_url=os.environ["ROUTER_BASE_URL"], api_key=os.environ["ROUTER_API_KEY"],
                        timeout=180, max_retries=1)
        kwargs = {"model": self.model, "messages": msgs, "max_tokens": MAX_TOKENS, "temperature": 0,
                  **MODEL_OPTIONS.get(self.model, {})}
        try:
            raw = client.chat.completions.with_raw_response.create(**kwargs, response_format={"type": "json_object"})
        except Exception as exc:  # optional parameter rejected (400 costs nothing): retry once without it
            if "response_format" not in str(exc).lower() and "unsupported" not in str(exc).lower():
                raise
            raw = client.chat.completions.with_raw_response.create(**kwargs)
        resp = raw.parse()
        body = json.loads(raw.text) if raw.text else {}
        found = _find_credits(dict(raw.headers), body)
        u = resp.usage
        return {"text": resp.choices[0].message.content or "", "input_tokens": getattr(u, "prompt_tokens", 0) or 0,
                "output_tokens": getattr(u, "completion_tokens", 0) or 0, "credit_fields": found,
                "credits": credits_value(found), "finish_reason": resp.choices[0].finish_reason}

    def __call__(self, msgs: list[dict]) -> dict:
        key = hashlib.sha256(json.dumps([self.model, msgs], ensure_ascii=False).encode("utf-8")).hexdigest()[:24]
        path = CACHE_DIR / f"{key}.json"
        if path.exists():
            self.cached += 1
            return json.loads(path.read_text(encoding="utf-8"))
        t0 = time.monotonic()
        out = self._call(msgs)
        out["seconds"] = round(time.monotonic() - t0, 1)
        self.calls += 1
        self.tokens_in += out["input_tokens"]
        self.tokens_out += out["output_tokens"]
        if out.get("credits") is not None:
            self.credits.append(out["credits"])
        self.credit_fields.update(out.get("credit_fields") or {})
        if out.get("finish_reason") == "length":
            self.truncated += 1
            print(f"  WARNING: {self.model} reply was cut off by the output limit; not cached, items stay unreviewed",
                  flush=True)
        else:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"model": self.model, **out}, ensure_ascii=False), encoding="utf-8")
        print(f"  {self.model} call {self.calls}: {out['input_tokens']} in / {out['output_tokens']} out tokens, "
              f"credits_charged={out.get('credits')}, finish={out.get('finish_reason')}, {out['seconds']}s", flush=True)
        return out


def parse_reply(text: str) -> dict[str, dict]:
    try:
        data = json.loads(text)
    except ValueError:
        a, b = text.find("{"), text.rfind("}")
        try:
            data = json.loads(text[a:b + 1])
        except ValueError:
            return {}
    return {str(x.get("item_id")): x for x in data.get("items", []) if isinstance(x, dict)} if isinstance(data, dict) else {}


# --------------------------------------------------------------- consensus

def _yn(v) -> str | None:
    v = str(v or "").strip().lower()
    return v[0] if v[:1] in ("y", "n") else None


def consensus(item: dict, a: dict | None, b: dict | None) -> dict:
    """Decide ai_ok per row from two reviews of one item. Returns {"rows": {ref: {...}}, "item": {...}}."""
    rows = number_rows(item)
    ra = {int(r.get("n", 0)): r for r in (a or {}).get("rows", []) if isinstance(r, dict)}
    rb = {int(r.get("n", 0)): r for r in (b or {}).get("rows", []) if isinstance(r, dict)}
    unmarked_a, unmarked_b = bool((a or {}).get("unmarked")), bool((b or {}).get("unmarked"))
    item_flags = {"unmarked_both": unmarked_a and unmarked_b, "unmarked_one": unmarked_a != unmarked_b,
                  "missing_review": a is None or b is None}
    needs_clean = item["kind"] in ("hard_negative", "claim", "symmetry_neutral")
    asym_both = asym_one = False
    if item["file"] == "sym":
        sa, sb = _yn((a or {}).get("symmetric")), _yn((b or {}).get("symmetric"))
        asym_both, asym_one = sa == "n" and sb == "n", (sa == "n") != (sb == "n")
    item_flags.update({"asymmetric_both": asym_both, "asymmetric_one": asym_one})

    out = {}
    for r in rows:
        va, vb = ra.get(r["n"], {}), rb.get(r["n"], {})
        ya, yb = _yn(va.get("verdict")), _yn(vb.get("verdict"))
        decision = "y" if ya == yb == "y" else ("n" if ya == yb == "n" else "")
        why = ""
        if a is None or b is None:
            decision, why = "", "missing review"
        elif needs_clean and item_flags["unmarked_both"]:
            decision, why = "n", "both reviewers found an unmarked technique"
        elif needs_clean and item_flags["unmarked_one"] and decision == "y":
            decision, why = "", "one reviewer found an unmarked technique"
        if asym_both:
            decision, why = "n", "both reviewers: variants are not symmetric"
        elif asym_one and decision == "y":
            decision, why = "", "one reviewer: variants are not symmetric"
        rs_a, rs_b = _yn(va.get("reported_speech")), _yn(vb.get("reported_speech"))
        rs_fix = None
        gold_rs = r["reported_speech"] or "n"
        if r["label"] != "none" and item["file"] == "gold" and rs_a and rs_a == rs_b and rs_a != gold_rs:
            rs_fix = rs_a
        reasons = f"{REVIEWERS[0]}: {ya or '?'} {str(va.get('reason', ''))[:90]} | {REVIEWERS[1]}: {yb or '?'} {str(vb.get('reason', ''))[:90]}"
        if why:
            reasons += f" | {why}"
        if rs_fix:
            reasons += f" | reported_speech corrected {gold_rs}->{rs_fix} (both reviewers)"
        out[r["ref"]] = {"ai_ok": decision, "ai_reasons": reasons, "rs_fix": rs_fix, "label": r["label"],
                         "va": ya, "vb": yb}
    return {"rows": out, "item": item_flags}


def cohen_kappa(pairs: list[tuple[str, str]]) -> float | None:
    pairs = [(x, y) for x, y in pairs if x in ("y", "n") and y in ("y", "n")]
    if not pairs:
        return None
    n = len(pairs)
    po = sum(x == y for x, y in pairs) / n
    pa = sum(x == "y" for x, _ in pairs) / n
    pb = sum(y == "y" for _, y in pairs) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    if pe == 1:
        return 1.0 if po == 1 else 0.0
    return round((po - pe) / (1 - pe), 3)


# -------------------------------------------------------------------- run

def review(items: list[dict], reviewers: list[Reviewer], only_first_batch: bool = False) -> dict[str, list[dict]]:
    """item_id -> [review by reviewer 0, review by reviewer 1] (None if missing)."""
    results: dict[str, list] = {i["item_id"]: [None, None] for i in items}
    todo = batches(items)[:1] if only_first_batch else batches(items)
    for batch in todo:
        msgs = messages(batch)
        for k, rev in enumerate(reviewers):
            got = parse_reply(rev(msgs)["text"])
            for it in batch:
                results[it["item_id"]][k] = got.get(it["item_id"])
    return results


def apply(items, results, gold_rows, sym_rows) -> dict:
    """Write consensus into the row dicts; return the report."""
    per_label = defaultdict(Counter)
    pairs_all, pairs_by_label = [], defaultdict(list)
    rs_fixes, unmarked_items, item_counter = 0, Counter(), Counter()
    for item in items:
        a, b = results[item["item_id"]]
        c = consensus(item, a, b)
        if c["item"]["unmarked_both"]:
            unmarked_items["both"] += 1
        elif c["item"]["unmarked_one"]:
            unmarked_items["one"] += 1
        decisions = set()
        for (file, idx), d in c["rows"].items():
            row = (gold_rows if file == "gold" else sym_rows)[idx]
            row["ai_ok"], row["ai_reasons"] = d["ai_ok"], d["ai_reasons"]
            if d["rs_fix"]:
                row["reported_speech"] = d["rs_fix"]
                rs_fixes += 1
            per_label[d["label"]][{"y": "accepted", "n": "dropped", "": "excluded"}[d["ai_ok"]]] += 1
            pairs_all.append((d["va"], d["vb"]))
            pairs_by_label[d["label"]].append((d["va"], d["vb"]))
            decisions.add(d["ai_ok"])
        status = "dropped" if "n" in decisions else ("excluded" if "" in decisions else "accepted")
        item_counter[f"{item['file']}:{status}"] += 1
    return {
        "per_label": {lbl: {**dict(cnt), "kappa": cohen_kappa(pairs_by_label[lbl])} for lbl, cnt in sorted(per_label.items())},
        "kappa": cohen_kappa(pairs_all),
        "rows": sum(sum(c.values()) for c in per_label.values()),
        "items": dict(item_counter),
        "reported_speech_corrections": rs_fixes,
        "unmarked_technique_items": dict(unmarked_items),
    }


def git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short=8", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    except OSError:
        return "unknown"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--first-batch", action="store_true", help="run only the first batch (both reviewers) and stop")
    ap.add_argument("--yes", action="store_true", help="run everything and write the CSVs + report")
    args = ap.parse_args(argv)

    gold_rows, sym_rows = goldcsv.read_csv(GOLD), goldcsv.read_csv(SYM)
    items = load_items(gold_rows, sym_rows)
    n_batches = len(batches(items))
    n_rows = sum(len(number_rows(i)) for i in items)
    print(f"Reviewers: {', '.join(REVIEWERS)} (consensus only; language: {LANG})")
    print(f"Items: {len(items)} ({sum(i['file'] == 'gold' for i in items)} gold paragraphs + "
          f"{sum(i['file'] == 'sym' for i in items)} symmetry pairs), {n_rows} rows; ET/RU rows are not touched")
    print(f"Calls: {n_batches} batches x {len(REVIEWERS)} reviewers = {n_batches * len(REVIEWERS)} "
          f"(~{EST_IN_PER_CALL:,} in / ~{EST_OUT_PER_CALL:,} out tokens per call, "
          f"~{n_batches * len(REVIEWERS) * (EST_IN_PER_CALL + EST_OUT_PER_CALL):,} tokens total)")
    if not (args.first_batch or args.yes):
        print("\nDry run only. --first-batch runs 2 calls and prints credits; --yes runs everything.")
        return 0
    if args.yes:
        goldcsv.check_not_open(GOLD)
        goldcsv.check_not_open(SYM)
    _load_env_local()
    if not os.environ.get("ROUTER_API_KEY"):
        print("ROUTER_API_KEY not set (.env.local missing?)")
        return 2
    os.environ.setdefault("ROUTER_BASE_URL", "https://router.hostinger.com/v1")
    reviewers = [Reviewer(m) for m in REVIEWERS]
    results = review(items, reviewers, only_first_batch=args.first_batch)

    if args.first_batch:
        print("\nFirst batch done.")
        total = 0.0
        for rev in reviewers:
            per = (sum(rev.credits) / len(rev.credits)) if rev.credits else None
            proj = per * n_batches if per is not None else None
            total += proj or 0
            print(f"  {rev.model}: credits_charged={rev.credits or 'not reported by the router'}; "
                  f"tokens {rev.tokens_in} in / {rev.tokens_out} out; projected for {n_batches} calls: "
                  f"{proj if proj is not None else 'n/a'}")
            if rev.credit_fields:
                print(f"    billing fields seen: {rev.credit_fields}")
        print(f"  projected total credits: {total if total else 'n/a (router did not report credits)'}")
        print("Stop here and ask before running --yes.")
        return 0

    report = apply(items, results, gold_rows, sym_rows)
    goldcsv.write_csv(GOLD, goldcsv.GOLD_COLUMNS, gold_rows)
    goldcsv.write_csv(SYM, goldcsv.SYMMETRY_COLUMNS, sym_rows)
    report.update({
        "reviewers": REVIEWERS, "language": LANG, "method": "two model families, consensus only, not human-verified",
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"), "git_commit": git_commit(),
        "calls": {r.model: r.calls for r in reviewers},
        "tokens": {r.model: {"in": r.tokens_in, "out": r.tokens_out} for r in reviewers},
        "credits": {r.model: round(sum(r.credits), 4) if r.credits else None for r in reviewers},
    })
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("rows", "items", "kappa", "reported_speech_corrections",
                                              "unmarked_technique_items", "calls", "credits")}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
