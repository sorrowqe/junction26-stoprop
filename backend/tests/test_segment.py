import pytest

from app.pipeline.segment import detect_language, segment


def texts(seg):
    return [s.text for s in seg.sentences]


def assert_offsets(seg):
    for p in seg.paragraphs:
        assert seg.text[p.start:p.end] == p.text
        for s in p.sentences:
            assert seg.text[s.start:s.end] == s.text
            assert p.start <= s.start < s.end <= p.end


def test_ids_and_offsets_blank_line_paragraphs():
    text = "First one. Second one!\n\n  Third para here? Yes.\n\n\nFourth."
    seg = segment(text)
    assert [p.id for p in seg.paragraphs] == ["p1", "p2", "p3"]
    assert [s.id for s in seg.sentences] == ["p1s1", "p1s2", "p2s1", "p2s2", "p3s1"]
    assert texts(seg) == ["First one.", "Second one!", "Third para here?", "Yes.", "Fourth."]
    assert_offsets(seg)


def test_single_newlines_become_paragraphs_when_no_blank_lines():
    seg = segment("Line one is here.\nLine two is here.\r\nLine three.")
    assert len(seg.paragraphs) == 3
    assert seg.paragraphs[1].text == "Line two is here."
    assert_offsets(seg)


def test_hard_wrapped_lines_stay_one_paragraph_when_blank_lines_exist():
    seg = segment("This sentence is\nwrapped over two lines.\n\nSecond paragraph.")
    assert len(seg.paragraphs) == 2
    assert seg.sentences[0].text == "This sentence is\nwrapped over two lines."


def test_ids_are_stable_for_same_input():
    text = "A b c. D e f.\n\nG h i."
    assert [s.id for s in segment(text).sentences] == [s.id for s in segment(text).sentences]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Mr. Smith met Dr. Jones. They talked.", ["Mr. Smith met Dr. Jones.", "They talked."]),
        ("The U.S. economy grew. Prices fell.", ["The U.S. economy grew.", "Prices fell."]),
        ("J. K. Rowling wrote it. Fans cheered.", ["J. K. Rowling wrote it.", "Fans cheered."]),
        ("Growth was 3.5 percent. It slowed.", ["Growth was 3.5 percent.", "It slowed."]),
        ("Use tools, e.g. hammers. Then rest.", ["Use tools, e.g. hammers.", "Then rest."]),
        ('He said "Stop." Then he left.', ['He said "Stop."', "Then he left."]),
        ('"Stop!" he shouted. Nobody moved.', ['"Stop!" he shouted.', "Nobody moved."]),
        ("Really?! Yes. Wait… What?", ["Really?!", "Yes.", "Wait…", "What?"]),
        ("lowercase after period. so no split here.", ["lowercase after period. so no split here."]),
    ],
)
def test_sentence_splitting_en(text, expected):
    seg = segment(text)
    assert texts(seg) == expected
    assert_offsets(seg)


def test_sentence_splitting_ru():
    text = "Министр заявил об этом в понедельник. Т. е. спор продолжается, см. стр. 5. «Это ложь», — ответил он."
    seg = segment(text)
    assert texts(seg) == [
        "Министр заявил об этом в понедельник.",
        "Т. е. спор продолжается, см. стр. 5.",
        "«Это ложь», — ответил он.",
    ]
    assert_offsets(seg)


def test_sentence_splitting_et():
    text = "Ta sündis 1990. aastal Tartus. Kohtumine on 14. novembril, vt lk. 3. Minister ütles: „Jah.“ Siis lahkus."
    seg = segment(text)
    assert texts(seg) == [
        "Ta sündis 1990. aastal Tartus.",
        "Kohtumine on 14. novembril, vt lk. 3.",
        "Minister ütles: „Jah.“",
        "Siis lahkus.",
    ]
    assert_offsets(seg)


def test_empty_and_whitespace():
    assert segment("").paragraphs == []
    assert segment("   \n\n  ").paragraphs == []


def test_offsets_with_emoji_and_crlf():
    text = "Wow 🎉 this is big.\r\n\r\nNext 👍 part. Done."
    seg = segment(text)
    assert texts(seg) == ["Wow 🎉 this is big.", "Next 👍 part.", "Done."]
    assert_offsets(seg)


@pytest.mark.parametrize(
    "text, lang",
    [
        ("The government announced on Monday that it would raise taxes for the second time this year.", "en"),
        ("Valitsus teatas esmaspäeval, et tõstab sel aastal teist korda makse ja see on otsustatud.", "et"),
        ("Правительство объявило в понедельник, что повысит налоги второй раз в этом году.", "ru"),
        ("Die Regierung hat heute einen neuen Plan vorgestellt, der die Preise senken soll.", "other"),
        ("Уряд оголосив у понеділок, що підвищить податки вдруге цього року.", "other"),  # Ukrainian
        ("ok", "other"),
    ],
)
def test_detect_language(text, lang):
    assert detect_language(text) == lang


def test_lang_override():
    assert segment("Hello there my friend, how are you.", lang="et").lang == "et"
