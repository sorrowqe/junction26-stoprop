from app.pipeline.segment import segment
from app.pipeline.validate import is_reported_speech, locate, quote_regions, validate_candidates


def span_text(seg, loc):
    assert loc is not None
    return seg.text[loc[0]:loc[1]]


# ------------------------------------------------------------- verbatim match

def test_exact_match_offsets():
    text = "Intro sentence here. The plan is a total disaster for everyone."
    seg = segment(text)
    loc = locate(seg, "p1s2", "a total disaster")
    assert loc == (text.index("a total disaster"), text.index("a total disaster") + len("a total disaster"))


def test_offsets_are_global_not_sentence_relative():
    text = "First paragraph.\n\nSecond one has the phrase in it."
    seg = segment(text)
    loc = locate(seg, "p2s1", "the phrase")
    assert loc[0] == text.index("the phrase")


def test_whitespace_differences():
    # blank-line separated text, so the single newline stays inside the sentence
    text = "The  plan is\na total disaster.\n\nSecond paragraph."
    seg = segment(text)
    loc = locate(seg, "p1s1", "plan is a total disaster")
    assert span_text(seg, loc) == "plan is\na total disaster"


def test_quote_with_extra_outer_whitespace_and_newline():
    seg = segment("They ruined everything again.")
    assert span_text(seg, locate(seg, "p1s1", "  ruined \n everything ")) == "ruined everything"


def test_smart_quotes_in_source_straight_in_quote():
    text = "He called it “the worst deal” and left."
    seg = segment(text)
    assert span_text(seg, locate(seg, "p1s1", '"the worst deal"')) == "“the worst deal”"


def test_straight_quotes_in_source_smart_in_quote_and_apostrophes():
    text = "It's \"the worst deal\" ever, isn't it?"
    seg = segment(text)
    assert span_text(seg, locate(seg, "p1s1", "It’s “the worst deal”")) == 'It\'s "the worst deal"'


def test_quote_wrapped_in_quote_marks_by_model():
    seg = segment("This is a reckless scheme by the council.")
    assert span_text(seg, locate(seg, "p1s1", "“reckless scheme”")) == "reckless scheme"


def test_dashes_and_ellipsis_normalized():
    text = "The result — a disaster… was clear."
    seg = segment(text)
    assert span_text(seg, locate(seg, "p1s1", "result - a disaster...")) == "result — a disaster…"


def test_zero_width_characters_ignored():
    text = "A total​ disaster happened."
    seg = segment(text)
    assert span_text(seg, locate(seg, "p1s1", "total disaster")) == "total​ disaster"


def test_case_insensitive_fallback():
    seg = segment("Everyone knows this is wrong.")
    assert span_text(seg, locate(seg, "p1s1", "everyone knows")) == "Everyone knows"


def test_quote_not_present_rejected():
    seg = segment("The council approved the budget on Tuesday.")
    assert locate(seg, "p1s1", "a shocking betrayal") is None


def test_paraphrase_rejected():
    seg = segment("The council approved the budget on Tuesday.")
    assert locate(seg, "p1s1", "the council passed the budget") is None


def test_quote_in_other_sentence_rejected():
    seg = segment("First sentence is calm. Second has the scandal.")
    assert locate(seg, "p1s1", "the scandal") is None
    assert locate(seg, "p1s2", "the scandal") is not None


def test_unknown_sentence_id_and_empty_quote_rejected():
    seg = segment("Some text here.")
    assert locate(seg, "p9s9", "text") is None
    assert locate(seg, "p1s1", "") is None
    assert locate(seg, "p1s1", "   ") is None
    assert locate(seg, "p1s1", "x") is None  # too short to be meaningful


def test_repeated_phrase_in_one_sentence():
    text = "They lie and lie and lie again."
    seg = segment(text)
    first = locate(seg, "p1s1", "lie")
    second = locate(seg, "p1s1", "lie", occurrence=2)
    third = locate(seg, "p1s1", "lie", occurrence=3)
    assert first == (5, 8)
    assert second == (13, 16)
    assert third == (21, 24)
    assert locate(seg, "p1s1", "lie", occurrence=4) is None


def test_russian_text_with_yo_and_guillemets():
    text = "Он сказал, что всё пропало. Это «полный провал» властей."
    seg = segment(text)
    assert span_text(seg, locate(seg, "p1s1", "все пропало")) == "всё пропало"
    assert span_text(seg, locate(seg, "p1s2", '"полный провал"')) == "«полный провал»"


def test_estonian_text_with_low_high_quotes():
    text = "Kõik teavad, et see plaan on „täielik läbikukkumine“."
    seg = segment(text)
    assert span_text(seg, locate(seg, "p1s1", "Kõik teavad")) == "Kõik teavad"
    assert span_text(seg, locate(seg, "p1s1", '"täielik läbikukkumine"')) == "„täielik läbikukkumine“"


def test_decomposed_unicode_matches_composed_quote():
    text = "Ta õnnestus hästi."  # õ and ä written as base + combining mark
    seg = segment(text)
    assert span_text(seg, locate(seg, "p1s1", "õnnestus hästi")) == "õnnestus hästi"


# ------------------------------------------------------------ reported speech

def _reported(text, sentence_id, quote):
    seg = segment(text)
    loc = locate(seg, sentence_id, quote)
    assert loc is not None
    return is_reported_speech(seg, *loc)


def test_quote_regions_pairs():
    text = 'A “b c” d „e f“ g «h «i» j» k "l m" n'
    regions = [text[a:b] for a, b in quote_regions(text, 0, len(text))]
    assert regions == ["“b c”", "„e f“", "«h «i» j»", '"l m"']


def test_reported_speech_en_said_after():
    assert _reported("“This is a disaster for all of us,” the mayor said.", "p1s1", "a disaster")


def test_reported_speech_en_said_before_straight_quotes():
    assert _reported('The minister said: "Everyone knows the plan failed."', "p1s1", "Everyone knows")


def test_reported_speech_ru():
    assert _reported("Министр заявил: «Это полный провал, и все знают правду».", "p1s1", "все знают")


def test_reported_speech_et():
    assert _reported("Minister ütles: „Kõik teavad, et see on vale.“", "p1s1", "Kõik teavad")


def test_not_reported_speech_outside_quotes():
    assert not _reported('Everyone knows it failed, the minister said "no comment".', "p1s1", "Everyone knows")


def test_not_reported_speech_without_attribution():
    assert not _reported("This “total disaster” must end now.", "p1s1", "total disaster")


def test_scare_quotes_are_not_reported_speech():
    assert not _reported('The so-called "experts" said nothing useful.', "p1s1", "experts")


def test_unclosed_quote_runs_to_paragraph_end():
    text = '“We will never give up, the fight goes on\n\nNext paragraph.'
    seg = segment(text)
    assert quote_regions(text, seg.paragraphs[0].start, seg.paragraphs[0].end) == [(0, len("“We will never give up, the fight goes on"))]


# --------------------------------------------------------------- candidates

def test_validate_candidates_counts_rejections_and_marks_reported():
    text = 'The plan is a disaster. "It is a scam," the mayor said.'
    seg = segment(text)
    cands = [
        {"sentence_id": "p1s1", "quote": "a disaster", "label": "loaded_language", "confidence": 0.9},
        {"sentence_id": "p1s2", "quote": "a scam", "label": "loaded_language", "confidence": 0.8},
        {"sentence_id": "p1s1", "quote": "invented words", "label": "fear_appeal", "confidence": 0.9},
        {"sentence_id": "p1s1", "quote": "a disaster", "label": "loaded_language", "confidence": 0.9},  # duplicate
    ]
    spans, rejections = validate_candidates(seg, cands)
    assert rejections == 1
    assert len(spans) == 2
    assert spans[0]["quote"] == "a disaster" and spans[0]["reported_speech"] is False
    assert spans[1]["quote"] == "a scam" and spans[1]["reported_speech"] is True
    for s in spans:
        assert text[s["start"]:s["end"]] == s["quote"]


def test_validate_candidates_tolerates_garbage_fields():
    seg = segment("Some calm sentence.")
    spans, rejections = validate_candidates(seg, [{"quote": None}, {}, {"sentence_id": 5, "quote": 7}])
    assert spans == [] and rejections == 3
