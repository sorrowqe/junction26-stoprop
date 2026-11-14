import json

import pytest

from app import config
from app.pipeline.heuristics import extract_hints
from app.pipeline.profile import build_profile, guess_genre, word_spans
from app.pipeline.segment import segment

SAMPLES = {s["id"]: s["text"] for s in json.loads((config.WEB_DIR / "static" / "samples.json").read_text(encoding="utf-8"))}


def span(text, quote, label="loaded_language", color="red", experimental=False, reported=False):
    start = text.index(quote)
    return {"start": start, "end": start + len(quote), "quote": quote, "label": label, "color": color,
            "experimental": experimental, "reported_speech": reported}


def genre_of(text):
    seg = segment(text)
    return guess_genre(seg, extract_hints(seg), len(word_spans(text)))


@pytest.mark.parametrize("sample, genre", [("manipulative", "social"), ("neutral", "news"), ("opinion", "opinion")])
def test_genre_of_samples(sample, genre):
    assert genre_of(SAMPLES[sample]) == genre


def test_genre_unknown_for_short_text():
    assert genre_of("Short text here.") == "unknown"


def test_genre_social_hashtags():
    assert genre_of("Can't believe what happened at the game tonight, what a finish for the home team #derby #football @club") == "social"


def test_genre_news_et_dateline():
    text = ("TALLINN, 14. november – Valitsus kinnitas teisipäeval uue eelarve, teatas rahandusministeerium. "
            "Eelarve maht on 18 miljardit eurot, ütles minister pressikonverentsil.")
    assert genre_of(text) == "news"


def test_word_count_multilingual():
    assert len(word_spans("Kõik teavad, et see on nn. „täielik“ läbikukkumine.")) == 8
    assert len(word_spans("Всё пропало, — сказал он.")) == 4
    assert len(word_spans("It's a well-known fact.")) == 4


def test_counts_density_and_share():
    text = "one two three four five six seven eight nine ten"
    seg = segment(text)
    spans = [span(text, "two three"), span(text, "seven", label="fear_appeal", color="amber")]
    prof = build_profile(seg, spans, [], {"loaded_language": "Loaded language", "fear_appeal": "Fear appeal"})
    assert prof["words"] == 10
    assert prof["counts"] == {"loaded_language": 1, "fear_appeal": 1}
    assert prof["flagged_share"] == 0.3
    assert prof["density_per_1000"] == 200.0
    assert "2 passages flagged" in prof["summary"]


def test_experimental_reported_and_green_are_not_flagged():
    text = "alpha beta gamma delta epsilon zeta eta theta iota kappa"
    seg = segment(text)
    spans = [
        span(text, "alpha", experimental=True, color="amber"),
        span(text, "gamma", reported=True),
        span(text, "epsilon", label="supported_claim", color="green"),
        span(text, "eta", label="balance", color="blue"),
    ]
    prof = build_profile(seg, spans, [])
    assert prof["flagged_share"] == 0.0
    assert prof["density_per_1000"] == 0.0
    assert prof["counts"] == {"supported_claim": 1, "balance": 1}
    assert prof["experimental"] == {"count": 1, "counts": {"loaded_language": 1}}
    assert prof["reported_speech"] == 1
    assert "Nothing is flagged by default" in prof["summary"]
    assert "1 experimental hint hidden" in prof["summary"]
    assert "1 quoted passage" in prof["summary"]


def test_overlapping_spans_counted_once_in_share():
    text = "a b c d e f g h i j"
    seg = segment(text)
    prof = build_profile(seg, [span(text, "b c d"), span(text, "c d e")], [])
    assert prof["flagged_share"] == 0.4


def test_summary_is_neutral():
    for text in SAMPLES.values():
        seg = segment(text)
        s = build_profile(seg, [], extract_hints(seg))["summary"].lower()
        for word in ("propaganda", "liar", "fake", "biased", "manipulator", "dishonest"):
            assert word not in s


def test_empty_text_profile():
    prof = build_profile(segment(""), [], [])
    assert prof["words"] == 0 and prof["flagged_share"] == 0.0 and prof["density_per_1000"] == 0.0
