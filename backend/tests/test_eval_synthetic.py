"""Synthetic gold generator + SemEval converter: markers, validation, specs, generation flow (fake model)."""
import json
from collections import Counter

import pytest

import goldcsv
import make_synthetic as ms
import semeval_sample as sem
from goldcsv import MarkerError, strip_markers, validate_spans

# ------------------------------------------------------------------ markers


def test_strip_markers_offsets():
    text, spans = strip_markers("They call it [[loaded_language|a vile scheme]] again, [[name_calling|clowns]].")
    assert text == "They call it a vile scheme again, clowns."
    assert [(s.label, s.quote, text[s.start:s.end], s.rs) for s in spans] == [
        ("loaded_language", "a vile scheme", "a vile scheme", False), ("name_calling", "clowns", "clowns", False)]


def test_strip_markers_rs_and_unicode():
    text, spans = strip_markers('Мэр заявил: «Это [[loaded_language|полный позор|rs]] для нас».')
    assert text == "Мэр заявил: «Это полный позор для нас»." and spans[0].rs and text[spans[0].start:spans[0].end] == "полный позор"


@pytest.mark.parametrize("bad", [
    "unclosed [[loaded_language|vile scheme",
    "stray ]] here",
    "nested [[loaded_language|a [[name_calling|b]] c]]",
    "spaces [[loaded_language| vile ]]",
    "empty [[loaded_language|]]",
])
def test_strip_markers_malformed(bad):
    with pytest.raises(MarkerError):
        strip_markers(bad)


def test_validate_spans_problems():
    text, spans = strip_markers("Calm start here. It is [[made_up_label|very bad]] now. And [[fear_appeal|they come. For you]] soon.")
    probs = validate_spans(text, spans, lang="en")
    assert any("unknown label made_up_label" in p for p in probs)
    assert any("crosses a sentence boundary" in p for p in probs)


def test_validate_rs_must_be_quoted_and_language():
    text, spans = strip_markers("The mayor said it was [[loaded_language|a disgrace|rs]] for the town and the people.")
    assert any("not inside quotation marks" in p for p in validate_spans(text, spans))
    text, spans = strip_markers('The mayor said: "It is [[loaded_language|a disgrace|rs]] for the town."')
    assert validate_spans(text, spans, lang="en") == []
    assert any("language detected" in p for p in validate_spans(text, spans, lang="ru"))


# -------------------------------------------------------------------- specs

def test_specs_distribution():
    gold, sym = ms.build_specs(2026)
    assert len(gold) == 90 and len(sym) == 20
    assert Counter(s["kind"] for s in gold) == {"manipulative": 45, "hard_negative": 30, "claim": 15}
    assert Counter(s["lang"] for s in gold) == {"en": 50, "et": 20, "ru": 20}
    manip = [s for s in gold if s["kind"] == "manipulative"]
    assert abs(sum(s["direction"] == "a" for s in manip) - sum(s["direction"] == "b" for s in manip)) <= 1
    tech = Counter(t for s in manip for t in s["techniques"])
    assert set(tech) == goldcsv.technique_labels() and set(tech.values()) == {7, 8}
    assert all(1 <= len(s["techniques"]) <= 2 and len(set(s["techniques"])) == len(s["techniques"]) for s in manip)
    assert Counter(s["subtype"] for s in gold if s["kind"] == "hard_negative") == {
        "emotional_factual": 10, "quoted_loaded": 10, "fair_opinion": 10}
    assert Counter(s["target_label"] for s in gold if s["kind"] == "claim") == {
        "unsupported_claim": 5, "supported_claim": 5, "balance": 5}
    assert set(s["topic"] for s in gold) == set(ms.TOPICS)
    assert all(s["source"] == "synthetic" for s in gold)
    assert Counter(s["lang"] for s in sym) == {"en": 12, "et": 4, "ru": 4}
    assert ms.build_specs(2026) == ms.build_specs(2026)  # reproducible


def test_prompt_is_not_claude_and_has_taxonomy():
    p = ms.system_prompt()
    assert "[[label|exact phrase]]" in p and "FICTIONAL" in p and "loaded_language" in p
    assert ms.main(["--model", "claude-haiku-4.5-eu"]) == 2


def test_dry_run_makes_no_calls(monkeypatch, capsys):
    monkeypatch.setattr(ms.Generator, "__call__", lambda *a, **k: (_ for _ in ()).throw(AssertionError("called")))
    assert ms.main([]) == 0
    out = capsys.readouterr().out
    assert "LLM calls: 22" in out and "Dry run only" in out and "Estimated tokens" in out


# ------------------------------------------------------- generation (fake)

FILLER = {
    "en": "The council met on Tuesday to discuss the plan for the town. Residents asked many questions about the cost "
          "and the timeline of the project, and the answers will be published next week. The mayor thanked "
          "everyone who came and promised that the next meeting will also be open to the public.",
    "et": "Vallavolikogu arutas teisipäeval uut plaani ja elanikud esitasid palju küsimusi. See on oluline teema, mida on "
          "vaja hoolikalt kaaluda, ning otsus tehakse järgmisel kuul pärast avalikku arutelu. Vallavanem tänas "
          "kõiki, kes kohale tulid, ja lubas, et ka järgmine koosolek on avalik.",
    "ru": "Городской совет обсудил во вторник новый план, и жители задали много вопросов. Это важная тема, которую нужно "
          "тщательно рассмотреть, и решение будет принято в следующем месяце после обсуждения. Мэр поблагодарил "
          "всех, кто пришёл, и пообещал, что следующее заседание тоже будет открытым для всех жителей города.",
}
MARK = {"en": "They said it was [[{l}|a truly remarkable phrase]] for everyone.",
        "et": "See oli [[{l}|väga eriline väljend]] kõigile.",
        "ru": "Это было [[{l}|очень особое выражение]] для всех."}
RS = {"en": 'The mayor said: "This is [[loaded_language|a total disgrace|rs]] for us."',
      "et": "Linnapea ütles: „See on [[loaded_language|täielik häbi|rs]] meile.“",
      "ru": "Мэр заявил: «Это [[loaded_language|полный позор|rs]] для нас»."}


def good_text(spec):
    lang = spec["lang"]
    if spec["kind"] == "manipulative":
        marks = [MARK[lang].format(l=t) for t in spec["techniques"]]
    elif spec["kind"] == "claim":
        marks = [MARK[lang].format(l=spec["target_label"])]
    elif spec["subtype"] == "quoted_loaded":
        marks = [RS[lang]]
    else:
        marks = []
    return " ".join([FILLER[lang], *marks])


class FakeModel:
    def __init__(self, gold, sym, break_first=()):
        self.specs = {s["id"]: s for s in gold}
        self.sym = {s["pair_id"]: s for s in sym}
        self.break_first = set(break_first)
        self.seen = Counter()
        self.calls = 0

    def __call__(self, messages):
        self.calls += 1
        user = messages[-1]["content"]
        specs = json.loads(user.split("\n", 1)[1].split("\n\nA previous attempt")[0])
        if "pair per spec" in user:
            pairs = []
            for sp in specs:
                s = self.sym[sp["spec_id"]]
                a = good_text({"kind": "manipulative", "lang": s["lang"], "techniques": s.get("techniques", [])}) \
                    if s["manipulative"] else FILLER[s["lang"]]
                pairs.append({"spec_id": sp["spec_id"], "text_a": a, "text_b": a.replace("2", "3") + " ",
                              "swap": "sides"})
                pairs[-1]["text_b"] = a.replace("council", "board").replace("совет", "комитет").replace("Vallavolikogu", "Linnavolikogu")
            return json.dumps({"pairs": pairs}), 100, 200
        items = []
        for sp in specs:
            sid = sp["spec_id"]
            self.seen[sid] += 1
            if sid in self.break_first and self.seen[sid] == 1:
                items.append({"spec_id": sid, "text": "broken [[loaded_language|oops"})
            else:
                items.append({"spec_id": sid, "text": good_text(self.specs[sid])})
        return json.dumps({"items": items}), 100, 200


def test_generate_end_to_end_with_repair(monkeypatch, tmp_path):
    monkeypatch.setattr(ms, "CACHE_DIR", tmp_path / "cache")
    gold, sym = ms.build_specs(2026)
    fake = FakeModel(gold, sym, break_first={"syn-001", "syn-050"})
    gen = ms.Generator("gpt-test", call=fake)
    gold_rows, sym_rows, report = ms.generate(gold, sym, gen)
    assert report["gold_ok"] == 90 and report["gold_dropped"] == [] and report["pairs_ok"] == 20
    assert fake.calls == 22 + 1  # 18 gold + 4 symmetry batches + ONE repair batch holding both broken items
    assert gen.tokens_in == 2300 and gen.tokens_out == 4600
    ids = {r["id"] for r in gold_rows}
    assert len(ids) == 90 and all(r["source"] == "synthetic" and r["ok"] == "" for r in gold_rows)
    for r in gold_rows:
        if r["label"] != "none":
            assert r["text"][int(r["start"]):int(r["end"])] == r["quote"]
    rs_rows = [r for r in gold_rows if r["reported_speech"] == "y"]
    assert len({r["id"] for r in rs_rows}) == 10
    assert sum(1 for r in gold_rows if r["label"] == "none") == 20  # emotional_factual + fair_opinion here
    # CSV round trip
    path = tmp_path / "g.csv"
    goldcsv.write_csv(path, goldcsv.GOLD_COLUMNS, gold_rows)
    back = goldcsv.read_csv(path)
    assert back[0].keys() == set(goldcsv.GOLD_COLUMNS) and back[0]["text"] == gold_rows[0]["text"]
    assert {r["variant"] for r in sym_rows} == {"a", "b"} and len({r["pair_id"] for r in sym_rows}) == 20
    # second run hits the on-disk cache: no new calls
    gen2 = ms.Generator("gpt-test", call=FakeModel(gold, sym))
    ms.generate(gold, sym, gen2)
    assert gen2.calls == 0 and gen2.cached > 0


def test_item_dropped_after_failed_repair(monkeypatch, tmp_path):
    monkeypatch.setattr(ms, "CACHE_DIR", tmp_path / "cache")
    gold, sym = ms.build_specs(2026)

    class AlwaysBroken(FakeModel):
        def __call__(self, messages):
            text, a, b = super().__call__(messages)
            data = json.loads(text)
            for it in data.get("items", []):
                if it["spec_id"] == "syn-002":
                    it["text"] = FILLER["en"]  # planted techniques missing every time
            return json.dumps(data), a, b

    _, _, report = ms.generate(gold[:5], sym[:0], ms.Generator("gpt-test", call=AlwaysBroken(gold, sym)))
    assert report["gold_ok"] == 4
    assert report["gold_dropped"][0]["id"] == "syn-002"
    assert any("missing" in p for p in report["gold_dropped"][0]["problems"])


@pytest.mark.parametrize("spec, text, needle", [
    ({"kind": "manipulative", "lang": "en", "techniques": ["fear_appeal"]},
     FILLER["en"] + " " + MARK["en"].format(l="bandwagon"), "missing"),
    ({"kind": "manipulative", "lang": "en", "techniques": ["fear_appeal"]},
     FILLER["en"] + " " + MARK["en"].format(l="fear_appeal") + " " + MARK["en"].format(l="bandwagon"), "unrequested"),
    ({"kind": "hard_negative", "subtype": "fair_opinion", "lang": "en"},
     FILLER["en"] + " " + MARK["en"].format(l="loaded_language"), "contains a (non-quoted) technique"),
    ({"kind": "hard_negative", "subtype": "emotional_factual", "lang": "en"},
     FILLER["en"] + " " + MARK["en"].format(l="unsupported_claim"), "unsupported claim"),
    ({"kind": "hard_negative", "subtype": "quoted_loaded", "lang": "en"}, FILLER["en"], "no quoted (rs) span"),
    ({"kind": "claim", "target_label": "balance", "lang": "en"},
     FILLER["en"] + " " + MARK["en"].format(l="supported_claim"), "target label balance missing"),
    ({"kind": "claim", "target_label": "balance", "lang": "et"}, FILLER["en"], "language detected"),
])
def test_check_gold_item_rejects(spec, text, needle):
    _, _, probs = ms.check_gold_item(spec, text)
    assert any(needle in p for p in probs), probs


def test_check_pair():
    a = FILLER["en"] + " " + MARK["en"].format(l="name_calling")
    b = a.replace("council", "board")
    spec = {"lang": "en", "manipulative": True, "techniques": ["name_calling"]}
    assert ms.check_pair(spec, a, b)[1] == []
    assert any("label sets" in p for p in ms.check_pair(spec, a, FILLER["en"])[1])
    assert any("identical" in p for p in ms.check_pair(spec, a, a)[1])


# ----------------------------------------------------------------- semeval

def make_corpus(tmp_path):
    base = tmp_path / "datasets"
    (base / "train-articles").mkdir(parents=True)
    (base / "train-labels-task2-technique-classification").mkdir()
    words = " ".join(["word"] * 30)
    text = (f"Headline here\n\nFirst paragraph is a disgrace and {words}.\n\nSecond calm paragraph {words}.\n\n"
            f"Third has a slogan {words}.\n\nFourth spans two\nparagraphs {words}.\n")
    (base / "train-articles" / "article1.txt").write_text(text, encoding="utf-8")
    s1 = text.index("a disgrace")
    s3 = text.index("a slogan")
    s4 = text.index("spans two")
    labels = [f"1\tLoaded_Language\t{s1}\t{s1 + 10}", f"1\tSlogans\t{s3}\t{s3 + 8}",
              f"1\tDoubt\t{s4}\t{text.index('paragraphs') + 10}"]
    (base / "train-labels-task2-technique-classification" / "article1.task2-TC.labels").write_text("\n".join(labels), encoding="utf-8")
    return base


def test_semeval_candidates_and_rows(tmp_path):
    arts = sem.load_articles(make_corpus(tmp_path))
    with_spans, without = sem.candidates(arts)
    assert [c["id"] for c in with_spans] == ["sem-1-p2"]
    assert [c["id"] for c in without] == ["sem-1-p3"]  # p4 (unmapped Slogans) and p5/p6 (cross-paragraph) skipped
    rows = sem.to_rows(with_spans + without)
    r = rows[0]
    assert r["label"] == "loaded_language" and r["quote"] == "a disgrace" and r["notes"] == "semeval:Loaded_Language"
    assert r["source"] == "semeval" and r["text"][r["start"]:r["end"]] == "a disgrace"
    assert rows[1]["label"] == "none"


def test_semeval_label_map_targets_exist():
    assert set(sem.LABEL_MAP.values()) <= goldcsv.labels()
