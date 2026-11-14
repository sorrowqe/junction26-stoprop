from app.pipeline.heuristics import extract_hints, spans_from_hints
from app.pipeline.segment import segment


def hint_types(text, lang=None):
    return [(h["type"], h["quote"]) for h in extract_hints(segment(text, lang=lang))]


def labels(text, lang=None):
    seg = segment(text, lang=lang)
    return [(c["label"], c["quote"]) for c in spans_from_hints(extract_hints(seg))]


def test_hint_offsets_match_text():
    text = "Intro.\n\nEveryone knows they always lie to us! Bills jumped 300% overnight."
    seg = segment(text)
    hints = extract_hints(seg)
    assert hints
    for h in hints:
        assert text[h["start"]:h["end"]] == h["quote"]
        assert seg.sentence(h["sentence_id"]) is not None


def test_lexicon_features_en():
    types = dict(hint_types("This is a shocking and extremely bad plan. They never listen to us. However, it may change."))
    assert types["emotive"] == "shocking"
    assert types["intensifier"] == "extremely"
    assert types["absolutist"] == "never"
    assert types["us_them"] == "They"
    assert types["balance_marker"] == "However"


def test_all_caps_shouting_but_not_acronyms():
    assert ("all_caps", "WAKE UP") in hint_types("WAKE UP people, it is happening.")
    assert not [t for t, _ in hint_types("NATO and the EU met in the US today.") if t == "all_caps"]
    assert not [t for t, _ in hint_types("HARBOURTON, Nov 12 – The council met.") if t == "all_caps"]
    assert ("all_caps", "DISGRACE") in hint_types("This is a DISGRACE!")


def test_exclamation_and_link_hints():
    types = [t for t, _ in hint_types("Read this now!! See https://example.org/report for details.")]
    assert "exclamation" in types and "link" in types


def test_numbers_attributed_vs_unattributed():
    types = hint_types("Bills jumped 300% last year. Unemployment rose to 7.2 percent, the agency said.")
    assert ("number_unattributed", "300%") in types
    assert ("number_attributed", "7.2 percent") in types


def test_years_and_small_numbers_are_not_statistics():
    types = [t for t, _ in hint_types("In 2024 the 15 members met for 3 hours.")]
    assert "number_unattributed" not in types


def test_large_number_formats():
    types = hint_types("About 40,000 meters and 18 million euros and 125000 people.")
    quotes = [q for t, q in types if t.startswith("number_")]
    assert quotes == ["40,000", "18 million", "125000"]


def test_quotation_hint():
    assert ("quotation", "“too tight”") in hint_types("She said the plan was “too tight”.")


def test_ru_and_et_lexicons():
    ru = dict(hint_types("Все знают, что они всегда врут нам. Эксперты считают иначе."))
    assert ru["bandwagon_phrase"] == "Все знают"
    assert ru["vague_authority"] == "Эксперты считают"
    assert ru["absolutist"] == "всегда"
    assert ru["us_them"] == "они"
    et = dict(hint_types("Kõik teavad, et nad valetavad meile alati. Uuringud näitavad muud."))
    assert et["bandwagon_phrase"] == "Kõik teavad"
    assert et["vague_authority"] == "Uuringud näitavad"
    assert et["absolutist"] == "alati"
    assert et["us_them"] == "nad"


def test_ru_yo_folding():
    assert ("vague_authority", "Учёные доказали") in hint_types("Учёные доказали, что это вредно.")


def test_other_language_gets_only_language_independent_hints():
    types = [t for t, _ in hint_types("Die Regierung LÜGT UNS AN! Seht https://example.org", lang="other")]
    assert set(types) <= {"all_caps", "exclamation", "link", "number_unattributed", "quotation"}
    assert "all_caps" in types


# ----------------------------------------------------- conservative span rules

def test_spans_only_from_precise_patterns():
    found = labels("Everyone knows this. Experts say it is bad. Bills rose 300%. WAKE UP now!")
    assert found == [
        ("bandwagon", "Everyone knows"),
        ("questionable_authority", "Experts say"),
        ("unsupported_claim", "300%"),
        ("loaded_language", "WAKE UP"),
    ]


def test_spans_are_low_confidence_heuristic():
    seg = segment("Everyone knows this is true.")
    for c in spans_from_hints(extract_hints(seg)):
        assert c["source"] == "heuristic"
        assert c["confidence"] < 0.5
        assert "Rule-based" in c["explanation"]


def test_lexicon_only_hints_do_not_become_spans():
    # emotive / intensifier / absolutist / us-vs-them are hints for the LLM, never spans alone
    assert labels("This shocking plan is extremely bad and they never listen to us.") == []


def test_vague_authority_with_link_is_not_flagged():
    assert labels("Experts say so, see https://example.org/study for the report.") == []


def test_one_unsupported_claim_per_sentence():
    assert labels("Prices rose 40% and rents rose 25% this year.") == [("unsupported_claim", "40%")]


def test_neutral_wire_sentences_produce_no_spans():
    text = (
        "The council approved a plan to replace about 40,000 meters, the council said in a statement. "
        "The project will cost 18 million euros, according to the budget document."
    )
    assert labels(text) == []


def test_mixed_language_paragraphs_use_their_own_lexicon():
    text = ("Everyone knows that the plan has failed and the council is to blame for it.\n\n"
            "Все знают, что эта реформа провалилась, и министр заявил об этом в понедельник.")
    found = labels(text)
    assert ("bandwagon", "Everyone knows") in found
    assert ("bandwagon", "Все знают") in found
