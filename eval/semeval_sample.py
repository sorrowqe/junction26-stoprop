"""Convert a seeded 100-paragraph sample of the SemEval-2020 Task 11 corpus (PTC) to our gold CSV.

Source: Zenodo record 3952415 (open, CC BY 4.0): Da San Martino et al., "SemEval-2020 Task 11:
Detection of Propaganda Techniques in News Articles", 2020. Only the TRAINING split has gold labels.

  python eval/semeval_sample.py                     # download (checksum-verified) + convert
  python eval/semeval_sample.py --archive path.tgz  # use a local copy

Label mapping (SemEval -> ours). Paragraphs containing an unmapped or merged/lossy class are skipped,
so the sample never contains a span we would have to mislabel:
  mapped:   Loaded_Language, Name_Calling,Labeling, Exaggeration,Minimisation, Doubt,
            Appeal_to_fear-prejudice, Causal_Oversimplification, Appeal_to_Authority, Black-and-White_Fallacy
  skipped:  Repetition, Flag-Waving, Slogans, Thought-terminating_Cliches (no equivalent in our taxonomy),
            Whataboutism,Straw_Men,Red_Herring and Bandwagon,Reductio_ad_hitlerum (merged classes).
Note: SemEval also annotates techniques inside quotations; our reported-speech rule does not count those
against the text, so reported_speech is left empty (unknown) here.
"""
from __future__ import annotations

import argparse
import hashlib
import random
import re
import sys
import tarfile
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import goldcsv  # noqa: E402

URL = "https://zenodo.org/records/3952415/files/datasets-v2.tgz?download=1"
MD5 = "a67b730067e275da627ed6753586b98e"
CACHE = ROOT / "eval" / "data" / "cache" / "semeval"
OUT = ROOT / "eval" / "data" / "raw" / "semeval_sample.csv"

LABEL_MAP = {
    "Loaded_Language": "loaded_language",
    "Name_Calling,Labeling": "name_calling",
    "Exaggeration,Minimisation": "exaggeration_minimisation",
    "Doubt": "doubt_casting",
    "Appeal_to_fear-prejudice": "fear_appeal",
    "Causal_Oversimplification": "causal_oversimplification",
    "Appeal_to_Authority": "questionable_authority",
    "Black-and-White_Fallacy": "false_dilemma",
}
MIN_WORDS, MAX_WORDS = 25, 150


def download(dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / "datasets-v2.tgz"
    if not path.exists():
        print(f"Downloading {URL} ...")
        with urllib.request.urlopen(URL, timeout=120) as r:
            path.write_bytes(r.read())
    if hashlib.md5(path.read_bytes()).hexdigest() != MD5:
        path.unlink()
        raise SystemExit("Checksum mismatch - download removed. Try again.")
    return path


def extract(archive: Path, dest: Path) -> Path:
    out = dest / "x"
    if not (out / "datasets").exists():
        with tarfile.open(archive) as t:
            t.extractall(out, filter="data")  # safe extraction: no absolute paths, links or devices
    return out / "datasets"


def load_articles(base: Path) -> dict[str, tuple[str, list[tuple[str, int, int]]]]:
    """article id -> (text, [(semeval_label, start, end)])"""
    out = {}
    for txt in sorted((base / "train-articles").glob("article*.txt")):
        aid = re.sub(r"\D", "", txt.stem)
        text = txt.read_text(encoding="utf-8")
        spans = []
        lab = base / "train-labels-task2-technique-classification" / f"article{aid}.task2-TC.labels"
        if lab.exists():
            for line in lab.read_text(encoding="utf-8").splitlines():
                parts = line.split("\t")
                if len(parts) == 4:
                    spans.append((parts[1], int(parts[2]), int(parts[3])))
        out[aid] = (text, spans)
    return out


def paragraphs(text: str) -> list[tuple[int, int]]:
    out, pos = [], 0
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped:
            a = pos + (len(line) - len(line.lstrip()))
            out.append((a, a + len(stripped)))
        pos += len(line) + 1
    return out


def candidates(articles: dict) -> tuple[list[dict], list[dict]]:
    """(paragraphs with mapped spans, paragraphs without any span)."""
    with_spans, without = [], []
    for aid, (text, spans) in articles.items():
        for pi, (a, b) in enumerate(paragraphs(text)):
            if pi == 0:
                continue  # headline
            ptext = text[a:b]
            if not MIN_WORDS <= len(ptext.split()) <= MAX_WORDS:
                continue
            inside, touching = [], False
            for label, s, e in spans:
                if s < b and e > a:  # overlaps this paragraph
                    if not (a <= s and e <= b) or label not in LABEL_MAP:
                        touching = True
                        break
                    inside.append((LABEL_MAP[label], label, s - a, e - a))
            if touching:
                continue
            item = {"id": f"sem-{aid}-p{pi + 1}", "text": ptext, "spans": inside}
            (with_spans if inside else without).append(item)
    return with_spans, without


def sample(articles: dict, n: int = 100, share_with_spans: float = 0.7, seed: int = 2026) -> list[dict]:
    with_spans, without = candidates(articles)
    rng = random.Random(seed)
    k = min(len(with_spans), round(n * share_with_spans))
    picked = rng.sample(with_spans, k) + rng.sample(without, min(len(without), n - k))
    return sorted(picked, key=lambda x: x["id"])


def to_rows(items: list[dict]) -> list[dict]:
    rows = []
    for it in items:
        base = {"id": it["id"], "source": "semeval", "lang": "en", "genre": "news", "kind": "semeval",
                "topic": "", "direction": "", "text": it["text"], "reported_speech": "", "ok": ""}
        if not it["spans"]:
            rows.append({**base, "quote": "", "label": "none", "start": "", "end": "", "notes": ""})
        for ours, theirs, s, e in sorted(it["spans"], key=lambda x: (x[2], x[3])):
            assert it["text"][s:e], it["id"]
            rows.append({**base, "quote": it["text"][s:e], "label": ours, "start": s, "end": e,
                         "notes": f"semeval:{theirs}"})
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--archive", type=Path, help="local datasets-v2.tgz (else downloaded from Zenodo)")
    ap.add_argument("-n", type=int, default=100)
    ap.add_argument("--seed", type=int, default=2026)
    args = ap.parse_args(argv)
    archive = args.archive or download(CACHE)
    base = extract(archive, CACHE)
    articles = load_articles(base)
    items = sample(articles, args.n, seed=args.seed)
    rows = to_rows(items)
    goldcsv.write_csv(OUT, goldcsv.GOLD_COLUMNS, rows)
    n_spans = sum(1 for r in rows if r["label"] != "none")
    print(f"{len(articles)} training articles -> {len(items)} paragraphs "
          f"({sum(1 for i in items if i['spans'])} with spans, {n_spans} spans) -> {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
