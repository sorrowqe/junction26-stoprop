"""Paragraphs -> sentences with stable ids (p{n}s{m}), original char offsets, language detect.

Offsets are Python str indices (Unicode code points) into the ORIGINAL text, end exclusive:
text[s.start:s.end] == s.text. Ids are 1-based: the 2nd sentence of the 3rd paragraph is p3s2.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Sentence:
    id: str
    paragraph_id: str
    start: int
    end: int
    text: str


@dataclass
class Paragraph:
    id: str
    start: int
    end: int
    text: str
    sentences: list[Sentence] = field(default_factory=list)


@dataclass
class Segmented:
    text: str
    lang: str
    paragraphs: list[Paragraph]

    def __post_init__(self) -> None:
        self._sent = {s.id: s for p in self.paragraphs for s in p.sentences}
        self._para = {p.id: p for p in self.paragraphs}

    @property
    def sentences(self) -> list[Sentence]:
        return [s for p in self.paragraphs for s in p.sentences]

    def sentence(self, sentence_id: str) -> Sentence | None:
        return self._sent.get(sentence_id)

    def paragraph(self, paragraph_id: str) -> Paragraph | None:
        return self._para.get(paragraph_id)


# ---------------------------------------------------------------- paragraphs

def _trim(text: str, start: int, end: int) -> tuple[int, int]:
    chunk = text[start:end]
    left = len(chunk) - len(chunk.lstrip())
    right = len(chunk) - len(chunk.rstrip())
    return start + left, end - right


def split_paragraphs(text: str) -> list[tuple[int, int]]:
    """Blank-line separated blocks; if the text has no blank lines, every line is a paragraph."""
    lines = []
    pos = 0
    for line in text.split("\n"):
        lines.append((pos, pos + len(line)))
        pos += len(line) + 1
    blank = [not text[a:b].strip() for a, b in lines]
    has_blank_separator = re.search(r"\S[^\S\n]*\n[^\S\n]*\n\s*\S", text) is not None

    blocks: list[tuple[int, int]] = []
    if has_blank_separator:
        cur = None
        for (a, b), is_blank in zip(lines, blank):
            if is_blank:
                if cur:
                    blocks.append(cur)
                cur = None
            else:
                cur = (cur[0], b) if cur else (a, b)
        if cur:
            blocks.append(cur)
    else:
        blocks = [(a, b) for (a, b), is_blank in zip(lines, blank) if not is_blank]
    return [_trim(text, a, b) for a, b in blocks]


# ----------------------------------------------------------------- sentences

ABBREVIATIONS = {
    # en
    "mr", "mrs", "ms", "dr", "prof", "st", "jr", "sr", "vs", "etc", "e.g", "i.e", "inc", "ltd",
    "co", "corp", "gen", "gov", "sen", "rep", "jan", "feb", "mar", "apr", "jun", "jul", "aug",
    "sep", "sept", "oct", "nov", "dec", "fig", "approx", "est", "dept",
    # et
    "nt", "jne", "lk", "hr", "pr", "prl", "sh", "vt", "nn", "jm", "ca", "kl", "nö", "u", "tn", "mnt",
    # ru
    "т.е", "т.д", "т.п", "т.к", "г", "гг", "ул", "им", "проф", "др", "пр", "см", "стр", "руб",
    "тыс", "млн", "млрд", "т", "д", "с",
}
_END = re.compile(r"[.!?…]+[\"'”’»“)\]]*")
_OPENERS = set("\"'“„«‘([–—-")


def _is_abbreviation(before: str) -> bool:
    m = re.search(r"(\S+)$", before)
    if not m:
        return False
    token = m.group(1).lstrip("(\"'“„«‘[")
    if not token:
        return False
    low = token.lower()
    if low in ABBREVIATIONS:
        return True
    if len(token) == 1 and token.isalpha():  # initials: J. K. Rowling; RU "т. е."
        return True
    return re.fullmatch(r"(?:\w\.)+\w", token) is not None  # U.S / e.g / т.е


def split_sentences(text: str, start: int, end: int) -> list[tuple[int, int]]:
    """Sentence boundaries inside text[start:end] (global offsets, trimmed)."""
    chunk = text[start:end]
    out: list[tuple[int, int]] = []
    last = 0
    for m in _END.finditer(chunk):
        e = m.end()
        if e >= len(chunk) or not chunk[e].isspace():
            continue
        k = e
        while k < len(chunk) and chunk[k].isspace():
            k += 1
        if k >= len(chunk):
            continue
        nxt = chunk[k]
        if not (nxt.isupper() or nxt.isdigit() or nxt in _OPENERS):
            continue
        punct = m.group(0)
        if set(punct.rstrip("\"'”’»“)]")) == {"."} and _is_abbreviation(chunk[: m.start()]):
            continue
        out.append(_trim(text, start + last, start + e))
        last = k
    out.append(_trim(text, start + last, end))
    return [(a, b) for a, b in out if b > a]


# ------------------------------------------------------------------ language

_STOP_EN = set(
    "the and of to is in that it was for on are with as this be by have not from at but "
    "they his her an which you were has been would their will there what about".split()
)
_STOP_ET = set(
    "ja on ei et see ka oli mis kui aga või ning nagu seda tema kes siis veel oma ta "
    "ole olid oleks mida sest pärast kuid selle mitte nad meie need üle juba".split()
)
_UKRAINIAN_ONLY = set("іїєґІЇЄҐ")


def detect_language(text: str) -> str:
    """'en' | 'et' | 'ru' | 'other' - tiny rule-based detector, no dependencies."""
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 8:
        return "other"
    cyr = sum(1 for c in letters if "Ѐ" <= c <= "ӿ")
    if cyr / len(letters) >= 0.5:
        return "other" if any(c in _UKRAINIAN_ONLY for c in letters) else "ru"
    tokens = re.findall(r"[a-zõäöüšž]+", text.lower())
    if not tokens:
        return "other"
    en = sum(1 for t in tokens if t in _STOP_EN)
    et = sum(1 for t in tokens if t in _STOP_ET)
    et += 2 * text.lower().count("õ") + 0.5 * sum(text.lower().count(c) for c in "äöü")
    best = max(en, et)
    if best < max(2, 0.08 * len(tokens)):
        return "other"
    return "et" if et > en else "en"


# --------------------------------------------------------------------- entry

def segment(text: str, lang: str | None = None) -> Segmented:
    paragraphs: list[Paragraph] = []
    for pi, (pa, pb) in enumerate(split_paragraphs(text), start=1):
        pid = f"p{pi}"
        para = Paragraph(id=pid, start=pa, end=pb, text=text[pa:pb])
        for si, (sa, sb) in enumerate(split_sentences(text, pa, pb), start=1):
            para.sentences.append(Sentence(id=f"{pid}s{si}", paragraph_id=pid, start=sa, end=sb, text=text[sa:sb]))
        paragraphs.append(para)
    return Segmented(text=text, lang=lang or detect_language(text), paragraphs=paragraphs)
