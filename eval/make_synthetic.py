"""Synthetic gold-set generator (planted spans, offsets known by construction).

Generator model: a NON-Claude model (default gpt-5.5-eu) so the gold set does not share the tagger's
biases. Uses the DEV key from .env.local.

  python eval/make_synthetic.py            # dry run: prints the plan, number of calls, token estimate
  python eval/make_synthetic.py --yes      # really generate (after the OK)

Outputs (both with an empty "ok" review column; rows marked "n" are dropped later by sheet_to_jsonl.py):
  eval/data/raw/synthetic_gold.csv   ~90 paragraphs: 45 manipulative, 30 hard negatives, 15 claim items
  eval/data/raw/symmetry.csv         20 pairs: same text with sides/entities swapped
Raw model replies are cached in eval/data/cache/synthetic/ (gitignored), so re-runs cost nothing.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "backend"))

import goldcsv  # noqa: E402
from app import taxonomy  # noqa: E402

DEFAULT_MODEL = "gpt-5.5-eu"
CACHE_DIR = ROOT / "eval" / "data" / "cache" / "synthetic"
OUT_GOLD = ROOT / "eval" / "data" / "raw" / "synthetic_gold.csv"
OUT_SYM = ROOT / "eval" / "data" / "raw" / "symmetry.csv"
BATCH = 5
MAX_OUTPUT_TOKENS = 8000

TOPICS = ["energy", "elections", "migration", "health", "war", "economy"]
# Two opposing stances per topic; side "a" and side "b" are used equally often.
STANCES = {
    "energy": ("pushes fast renewables and attacks the fossil-fuel lobby",
               "defends fossil fuels / nuclear and attacks green-energy policies"),
    "elections": ("supports the liberal governing party and attacks the conservative opposition",
                  "supports the conservative opposition and attacks the liberal governing party"),
    "migration": ("defends refugees and attacks anti-immigration groups",
                  "opposes immigration and attacks open-border advocates"),
    "health": ("defends public-health measures and attacks vaccine sceptics",
               "opposes health mandates and attacks the health authorities"),
    "war": ("supports military aid to the invaded country and attacks those who want to stop it",
            "opposes military aid and attacks those who want to send more weapons"),
    "economy": ("wants higher taxes on the rich and attacks big business",
                "wants tax cuts and attacks welfare spending"),
}
LANG_PLAN = {  # kind -> {lang: count}; totals EN 50, ET 20, RU 20
    "manipulative": {"en": 25, "et": 10, "ru": 10},
    "hard_negative": {"en": 17, "et": 7, "ru": 6},
    "claim": {"en": 8, "et": 3, "ru": 4},
}
HARD_NEG_SUBTYPES = ["emotional_factual", "quoted_loaded", "fair_opinion"]
CLAIM_LABELS = ["unsupported_claim", "supported_claim", "balance"]
SYM_LANGS = ["en"] * 12 + ["et"] * 4 + ["ru"] * 4
LANG_NAMES = {"en": "English", "et": "Estonian", "ru": "Russian"}


# ------------------------------------------------------------------ specs

def build_specs(seed: int = 2026) -> tuple[list[dict], list[dict]]:
    rng = random.Random(seed)
    techniques = sorted(goldcsv.technique_labels())
    gold: list[dict] = []

    # manipulative: 45 items, 30 with two techniques + 15 with one = 75 slots: every technique exactly 7 or 8 times
    order = techniques[:]
    rng.shuffle(order)
    pool = order * 7 + order[:5]
    rng.shuffle(pool)
    assignments: list[list[str]] = []
    for i in range(45):
        first = pool.pop(0)
        chosen = [first]
        if i < 30:
            j = next(k for k, t in enumerate(pool) if t != first)
            chosen.append(pool.pop(j))
        assignments.append(chosen)
    manip_langs = [lg for lg, n in LANG_PLAN["manipulative"].items() for _ in range(n)]
    rng.shuffle(manip_langs)
    for i, lang in enumerate(manip_langs):
        chosen = assignments[i]
        topic = TOPICS[i % len(TOPICS)]
        side = "a" if i % 2 == 0 else "b"
        gold.append({"kind": "manipulative", "lang": lang, "topic": topic, "direction": side,
                     "stance": STANCES[topic][0 if side == "a" else 1], "techniques": chosen,
                     "genre": rng.choice(["social", "opinion", "social", "news"])})

    neg_langs = [lg for lg, n in LANG_PLAN["hard_negative"].items() for _ in range(n)]
    rng.shuffle(neg_langs)
    for i, lang in enumerate(neg_langs):
        sub = HARD_NEG_SUBTYPES[i % 3]
        topic = TOPICS[(i * 5) % len(TOPICS)]
        side = "a" if (i // 3) % 2 == 0 else "b"
        item = {"kind": "hard_negative", "subtype": sub, "lang": lang, "topic": topic,
                "genre": "opinion" if sub == "fair_opinion" else "news"}
        if sub == "emotional_factual":
            item["direction"] = "neutral"
        else:
            item["direction"] = side
            item["stance"] = STANCES[topic][0 if side == "a" else 1]
        if sub == "quoted_loaded":
            item["techniques"] = [rng.choice(["loaded_language", "name_calling", "fear_appeal"])]
        gold.append(item)

    claim_langs = [lg for lg, n in LANG_PLAN["claim"].items() for _ in range(n)]
    rng.shuffle(claim_langs)
    for i, lang in enumerate(claim_langs):
        topic = TOPICS[(i * 7) % len(TOPICS)]
        gold.append({"kind": "claim", "target_label": CLAIM_LABELS[i % 3], "lang": lang, "topic": topic,
                     "direction": "neutral", "genre": "news"})

    for n, item in enumerate(gold, start=1):
        item["id"] = f"syn-{n:03d}"
        item["source"] = "synthetic"

    sym: list[dict] = []
    for i, lang in enumerate(SYM_LANGS):
        topic = TOPICS[i % len(TOPICS)]
        manipulative = i % 10 < 7  # 14 manipulative pairs, 6 neutral
        item = {"pair_id": f"sym-{i + 1:02d}", "lang": lang, "topic": topic, "source": "synthetic",
                "manipulative": manipulative}
        if manipulative:
            item["techniques"] = rng.sample(techniques, 1 if i % 3 == 0 else 2)
        sym.append(item)
    return gold, sym


# ----------------------------------------------------------------- prompts

def system_prompt() -> str:
    lines = [f"- {t['label']}: {t['definition']} Example: {t['example']}" for t in taxonomy.load()["techniques"]]
    return f"""You write short synthetic test paragraphs for evaluating a manipulation-technique detector.
The detector labels HOW something is said, never WHICH side is right, so the test set must be balanced:
both political sides use the same techniques equally, and neutral texts must stay neutral.

Rules for every paragraph:
- Use only FICTIONAL people, parties, towns, companies and countries (invent names that fit the language).
  Do not name real living people. Real institutions in generic form (the EU, the WHO) are fine.
- Write natural, idiomatic text in the requested language (Estonian or Russian must read as written by a native
  speaker, not as a translation). 50-120 words, one paragraph, no title, no hashtags unless the genre is social.
- Mark every passage that fits a label with [[label|exact phrase]]. The phrase is the shortest passage that shows
  the label (usually 2-12 words), inside one sentence, copied exactly as it appears in the text.
  The text WITHOUT the markers must read naturally. Never nest markers. Do not put markers around whole sentences
  unless the whole sentence is the technique.
- For words quoted from a third party (reported speech), use [[label|exact phrase|rs]]; the phrase must be inside
  quotation marks in the text and the quote must be attributed (e.g. "..., the minister said").
- Do not use any technique that is not requested. Plain, neutral sentences must not contain hidden techniques.

Labels:
{chr(10).join(lines)}

Reply with JSON only (no code fences): {{"items": [{{"spec_id": "...", "text": "..."}}]}}
For symmetry specs reply {{"pairs": [{{"spec_id": "...", "text_a": "...", "text_b": "...", "swap": "what was swapped"}}]}}"""


def spec_instruction(item: dict) -> dict:
    lang = LANG_NAMES[item["lang"]]
    kind = item["kind"]
    out = {"spec_id": item["id"], "language": lang, "topic": item["topic"], "genre": item["genre"]}
    if kind == "manipulative":
        out["task"] = (f"A {item['genre']} text that {item['stance']}. Plant exactly these techniques "
                       f"(each at least once, marked): {', '.join(item['techniques'])}. Everything else plain.")
    elif kind == "hard_negative" and item["subtype"] == "emotional_factual":
        out["task"] = ("Neutral factual news report about an emotional or alarming event (disaster, crime statistics, "
                       "casualties) with named sources and dates. Sad or alarming facts, but NO manipulation technique "
                       "and no unsupported claim. Mark claims that come with a named source as supported_claim.")
    elif kind == "hard_negative" and item["subtype"] == "quoted_loaded":
        out["task"] = (f"Neutral news report quoting a fictional politician who {item['stance']}. The politician's quoted "
                       f"words use {item['techniques'][0]}; mark them with the |rs suffix. The reporter's own words "
                       "stay neutral and attribute the quote (said / told reporters).")
    elif kind == "hard_negative":
        out["task"] = (f"A fair, calm opinion piece that {item['stance']}, but argues with evidence, states the other "
                       "side's best argument fairly and admits uncertainty. Mark the fair counter-argument or admitted "
                       "uncertainty as balance. No manipulation technique and no unsupported claim.")
    else:
        label = item["target_label"]
        what = {"unsupported_claim": "1-2 specific, check-worthy factual claims (numbers, events) with NO source given",
                "supported_claim": "1-2 specific factual claims each WITH a named source, report or date in the text",
                "balance": "a fair counter-argument and/or an explicitly acknowledged uncertainty"}[label]
        out["task"] = f"A short neutral news text containing {what}. Mark them as {label}. No manipulation technique."
    return out


def symmetry_instruction(item: dict) -> dict:
    lang = LANG_NAMES[item["lang"]]
    if item["manipulative"]:
        task = (f"text_a: a short social-media style text about {item['topic']} that attacks one side, planting "
                f"{', '.join(item['techniques'])} (marked). text_b: the SAME text with the two sides swapped "
                "(parties, groups, names, and which side is attacked), keeping every other word and the markers on the "
                "corresponding phrases.")
    else:
        task = (f"text_a: a short neutral news text about {item['topic']} mentioning two opposing sides, no markers. "
                "text_b: the SAME text with the two sides swapped, otherwise identical.")
    return {"spec_id": item["pair_id"], "language": lang, "task": task}


def batches(items: list, size: int = BATCH) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def gold_messages(batch: list[dict], problems: dict | None = None) -> list[dict]:
    specs = [spec_instruction(i) for i in batch]
    msg = "Write one paragraph per spec:\n" + json.dumps(specs, ensure_ascii=False, indent=1)
    if problems:
        msg += "\n\nA previous attempt had these problems; avoid them:\n" + json.dumps(problems, ensure_ascii=False)
    return [{"role": "system", "content": system_prompt()}, {"role": "user", "content": msg}]


def symmetry_messages(batch: list[dict], problems: dict | None = None) -> list[dict]:
    specs = [symmetry_instruction(i) for i in batch]
    msg = "Write one pair per spec:\n" + json.dumps(specs, ensure_ascii=False, indent=1)
    if problems:
        msg += "\n\nA previous attempt had these problems; avoid them:\n" + json.dumps(problems, ensure_ascii=False)
    return [{"role": "system", "content": system_prompt()}, {"role": "user", "content": msg}]


# -------------------------------------------------------------- validation

def check_gold_item(item: dict, marked: str) -> tuple[str | None, list[goldcsv.Span], list[str]]:
    """(plain text, spans, problems)."""
    try:
        text, spans = goldcsv.strip_markers(marked)
    except goldcsv.MarkerError as exc:
        return None, [], [f"marker error: {exc}"]
    problems = goldcsv.validate_spans(text, spans, lang=item["lang"])
    words = len(text.split())
    if not 35 <= words <= 170:
        problems.append(f"length {words} words (want 50-120)")
    techniques = goldcsv.technique_labels()
    plain = [s for s in spans if not s.rs]
    kind = item["kind"]
    if kind == "manipulative":
        for t in item["techniques"]:
            if not any(s.label == t for s in plain):
                problems.append(f"planted technique {t} missing")
        extra = {s.label for s in plain if s.label in techniques} - set(item["techniques"])
        if extra:
            problems.append(f"unrequested techniques {sorted(extra)}")
    elif kind == "hard_negative":
        if any(s.label in techniques for s in plain):
            problems.append("hard negative contains a (non-quoted) technique")
        if any(s.label == "unsupported_claim" for s in spans):
            problems.append("hard negative contains an unsupported claim")
        if item["subtype"] == "quoted_loaded" and not any(s.rs for s in spans):
            problems.append("no quoted (rs) span")
    else:
        if not any(s.label == item["target_label"] for s in spans):
            problems.append(f"target label {item['target_label']} missing")
        if any(s.label in techniques for s in plain):
            problems.append("claim item contains a technique")
    return text, spans, problems


def check_pair(item: dict, a_marked: str, b_marked: str) -> tuple[tuple | None, list[str]]:
    try:
        a_text, a_spans = goldcsv.strip_markers(a_marked)
        b_text, b_spans = goldcsv.strip_markers(b_marked)
    except goldcsv.MarkerError as exc:
        return None, [f"marker error: {exc}"]
    problems = goldcsv.validate_spans(a_text, a_spans, lang=item["lang"])
    problems += goldcsv.validate_spans(b_text, b_spans, lang=item["lang"])
    if a_text == b_text:
        problems.append("texts are identical")
    if not 0.75 <= len(a_text) / max(1, len(b_text)) <= 1.33:
        problems.append("texts differ too much in length")
    if Counter(s.label for s in a_spans) != Counter(s.label for s in b_spans):
        problems.append("label sets of a and b differ")
    if item["manipulative"]:
        for t in item["techniques"]:
            if not any(s.label == t for s in a_spans):
                problems.append(f"planted technique {t} missing")
    elif a_spans or b_spans:
        problems.append("neutral pair must have no markers")
    return (a_text, a_spans, b_text, b_spans), problems


# ------------------------------------------------------------------ model

def _load_env_local() -> None:
    """Load .env.local into os.environ without printing anything."""
    path = ROOT / ".env.local"
    if not path.exists() or os.environ.get("ROUTER_API_KEY"):
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.split(" #", 1)[0].strip().strip("'\"")
        os.environ.setdefault(k.strip(), v)


class Generator:
    """Calls the model with on-disk caching; records usage."""

    def __init__(self, model: str, call=None):
        self.model = model
        self.calls = 0
        self.cached = 0
        self.tokens_in = 0
        self.tokens_out = 0
        self._call = call or self._openai_call

    def _openai_call(self, messages: list[dict]) -> tuple[str, int, int]:
        from openai import OpenAI
        client = OpenAI(base_url=os.environ["ROUTER_BASE_URL"], api_key=os.environ["ROUTER_API_KEY"],
                        timeout=180, max_retries=1)
        kwargs = {"model": self.model, "messages": messages, "max_completion_tokens": MAX_OUTPUT_TOKENS,
                  "response_format": {"type": "json_object"}}
        try:
            resp = client.chat.completions.create(**kwargs, reasoning_effort="low")
        except Exception as exc:  # router may not accept reasoning_effort: retry once without it
            if "reasoning" not in str(exc).lower():
                raise
            resp = client.chat.completions.create(**kwargs)
        u = resp.usage
        return resp.choices[0].message.content or "", getattr(u, "prompt_tokens", 0), getattr(u, "completion_tokens", 0)

    def __call__(self, messages: list[dict]) -> str:
        key = hashlib.sha256(json.dumps([self.model, messages], ensure_ascii=False).encode("utf-8")).hexdigest()[:24]
        path = CACHE_DIR / f"{key}.json"
        if path.exists():
            self.cached += 1
            return json.loads(path.read_text(encoding="utf-8"))["text"]
        t0 = time.monotonic()
        text, tin, tout = self._call(messages)
        self.calls += 1
        self.tokens_in += tin
        self.tokens_out += tout
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"model": self.model, "text": text, "input_tokens": tin, "output_tokens": tout,
                                    "seconds": round(time.monotonic() - t0, 1)}, ensure_ascii=False), encoding="utf-8")
        print(f"  call {self.calls}: {tin} in / {tout} out tokens, {time.monotonic() - t0:.1f}s", flush=True)
        return text


def _parse(reply: str, key: str) -> dict[str, dict]:
    try:
        data = json.loads(reply)
    except ValueError:
        start, end = reply.find("{"), reply.rfind("}")
        try:
            data = json.loads(reply[start:end + 1])
        except ValueError:
            return {}
    return {str(x.get("spec_id")): x for x in data.get(key, []) if isinstance(x, dict)}


def generate(gold_specs: list[dict], sym_specs: list[dict], gen: Generator) -> tuple[list[dict], list[dict], dict]:
    report = {"gold_ok": 0, "gold_dropped": [], "pairs_ok": 0, "pairs_dropped": []}
    gold_rows: list[dict] = []
    by_id = {s["id"]: s for s in gold_specs}
    accepted: dict[str, tuple] = {}
    pending, problems = list(gold_specs), {}
    for _round in range(2):  # first attempt + one repair round
        retry = []
        for batch in batches(pending):
            got = _parse(gen(gold_messages(batch, {k: v for k, v in problems.items() if k in {b['id'] for b in batch}})), "items")
            for spec in batch:
                marked = (got.get(spec["id"]) or {}).get("text")
                if not isinstance(marked, str):
                    problems[spec["id"]] = ["missing in reply"]
                    retry.append(spec)
                    continue
                text, spans, probs = check_gold_item(spec, marked)
                if probs:
                    problems[spec["id"]] = probs
                    retry.append(spec)
                else:
                    accepted[spec["id"]] = (text, spans)
        pending = retry
        if not pending:
            break
    for sid, spec in by_id.items():
        if sid in accepted:
            text, spans = accepted[sid]
            notes = spec.get("subtype", "") or spec.get("target_label", "")
            if spec["kind"] == "manipulative":
                notes = "planted: " + ",".join(spec["techniques"])
            gold_rows += goldcsv.gold_rows({**spec, "notes": notes}, text, spans)
            report["gold_ok"] += 1
        else:
            report["gold_dropped"].append({"id": sid, "problems": problems.get(sid)})

    sym_rows: list[dict] = []
    accepted_p: dict[str, tuple] = {}
    pending, sproblems = list(sym_specs), {}
    for _round in range(2):
        retry = []
        for batch in batches(pending):
            got = _parse(gen(symmetry_messages(batch, {k: v for k, v in sproblems.items() if k in {b['pair_id'] for b in batch}})), "pairs")
            for spec in batch:
                g = got.get(spec["pair_id"]) or {}
                if not (isinstance(g.get("text_a"), str) and isinstance(g.get("text_b"), str)):
                    sproblems[spec["pair_id"]] = ["missing in reply"]
                    retry.append(spec)
                    continue
                parsed, probs = check_pair(spec, g["text_a"], g["text_b"])
                if probs:
                    sproblems[spec["pair_id"]] = probs
                    retry.append(spec)
                else:
                    accepted_p[spec["pair_id"]] = (*parsed, str(g.get("swap", ""))[:200])
        pending = retry
        if not pending:
            break
    for spec in sym_specs:
        pid = spec["pair_id"]
        if pid not in accepted_p:
            report["pairs_dropped"].append({"id": pid, "problems": sproblems.get(pid)})
            continue
        a_text, a_spans, b_text, b_spans, swap = accepted_p[pid]
        for variant, text, spans in (("a", a_text, a_spans), ("b", b_text, b_spans)):
            base = {"pair_id": pid, "variant": variant, "source": "synthetic", "lang": spec["lang"],
                    "topic": spec["topic"], "swap": swap, "text": text, "ok": "",
                    "notes": "planted: " + ",".join(spec.get("techniques", [])) if spec["manipulative"] else "neutral"}
            if not spans:
                sym_rows.append({**base, "quote": "", "label": "none", "start": "", "end": ""})
            for s in spans:
                sym_rows.append({**base, "quote": s.quote, "label": s.label, "start": s.start, "end": s.end})
        report["pairs_ok"] += 1
    return gold_rows, sym_rows, report


# ------------------------------------------------------------------ plan

def estimate(gold_specs: list[dict], sym_specs: list[dict]) -> dict:
    """Rough token estimate (chars/3.5 for input; per-item output + reasoning allowance per call)."""
    g_batches, s_batches = batches(gold_specs), batches(sym_specs)
    tin = sum(len(json.dumps(gold_messages(b), ensure_ascii=False)) for b in g_batches)
    tin += sum(len(json.dumps(symmetry_messages(b), ensure_ascii=False)) for b in s_batches)
    tin = int(tin / 3.5)
    per_item = {"en": 260, "et": 420, "ru": 420}
    tout = sum(per_item[s["lang"]] for s in gold_specs) + sum(2 * per_item[s["lang"]] for s in sym_specs)
    calls = len(g_batches) + len(s_batches)
    reasoning = 1500 * calls
    repair_calls = max(2, round(calls * 0.3))
    return {"calls": calls, "repair_calls_max": repair_calls, "input_tokens": tin,
            "output_tokens": tout + reasoning, "upper_bound_tokens": int((tin + tout + reasoning) * (1 + repair_calls / calls))}


def print_plan(gold_specs, sym_specs, model) -> None:
    est = estimate(gold_specs, sym_specs)
    kinds = Counter(s["kind"] for s in gold_specs)
    langs = Counter(s["lang"] for s in gold_specs)
    tech = Counter(t for s in gold_specs if s["kind"] == "manipulative" for t in s["techniques"])
    dirs = Counter(s["direction"] for s in gold_specs if s["kind"] == "manipulative")
    print(f"Model: {model} (router, DEV key from .env.local)")
    print(f"Gold specs: {len(gold_specs)} {dict(kinds)}  languages {dict(langs)}")
    print(f"  manipulative sides: {dict(dirs)}  techniques planted: {dict(sorted(tech.items()))}")
    print(f"  hard negatives: {dict(Counter(s['subtype'] for s in gold_specs if s['kind'] == 'hard_negative'))}")
    print(f"  claim items: {dict(Counter(s['target_label'] for s in gold_specs if s['kind'] == 'claim'))}")
    print(f"Symmetry pairs: {len(sym_specs)} languages {dict(Counter(s['lang'] for s in sym_specs))}, "
          f"{sum(s['manipulative'] for s in sym_specs)} manipulative / {sum(not s['manipulative'] for s in sym_specs)} neutral")
    print(f"LLM calls: {est['calls']} ({BATCH} items per call) + at most ~{est['repair_calls_max']} repair calls")
    print(f"Estimated tokens: ~{est['input_tokens']:,} in + ~{est['output_tokens']:,} out "
          f"(incl. reasoning allowance); upper bound ~{est['upper_bound_tokens']:,}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--yes", action="store_true", help="really call the model (default: dry run)")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--seed", type=int, default=2026)
    args = ap.parse_args(argv)
    if args.model.startswith("claude"):
        print("Refusing: the generator must not be a Claude model (the tagger is Claude).")
        return 2
    gold_specs, sym_specs = build_specs(args.seed)
    print_plan(gold_specs, sym_specs, args.model)
    if not args.yes:
        print("\nDry run only. Re-run with --yes to generate.")
        return 0
    _load_env_local()
    if not os.environ.get("ROUTER_API_KEY"):
        print("ROUTER_API_KEY not set (.env.local missing?)")
        return 2
    os.environ.setdefault("ROUTER_BASE_URL", "https://router.hostinger.com/v1")
    gen = Generator(args.model)
    gold_rows, sym_rows, report = generate(gold_specs, sym_specs, gen)
    goldcsv.write_csv(OUT_GOLD, goldcsv.GOLD_COLUMNS, gold_rows)
    goldcsv.write_csv(OUT_SYM, goldcsv.SYMMETRY_COLUMNS, sym_rows)
    print(f"\nCalls made: {gen.calls} (cached: {gen.cached}); tokens: {gen.tokens_in:,} in / {gen.tokens_out:,} out")
    print(f"Gold items accepted: {report['gold_ok']}/{len(gold_specs)} -> {OUT_GOLD.relative_to(ROOT)} ({len(gold_rows)} rows)")
    print(f"Symmetry pairs accepted: {report['pairs_ok']}/{len(sym_specs)} -> {OUT_SYM.relative_to(ROOT)} ({len(sym_rows)} rows)")
    for d in report["gold_dropped"] + report["pairs_dropped"]:
        print(f"  dropped {d['id']}: {d['problems']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
