import asyncio
import json

import pytest

from app.pipeline import tagger
from app.pipeline.heuristics import extract_hints
from app.pipeline.segment import segment
from fakellm import FakeLLM, install

TEXT = ("The council approved the plan on Tuesday. Critics say the timeline is too short.\n\n"
        "This reckless scheme will destroy our neighbourhood forever! Everyone knows it.")


def run(coro):
    return asyncio.run(coro)


def tag(text, **kw):
    seg = segment(text)
    return seg, run(tagger.tag(seg, extract_hints(seg), **kw))


def responder_reckless(user, sents):
    out = []
    for sid, s in sents.items():
        if "reckless scheme" in s:
            out.append({"sentence_id": sid, "quote": "reckless scheme", "label": "loaded_language",
                        "confidence": 0.85, "explanation": "Emotionally charged wording."})
    return json.dumps(out)


# ------------------------------------------------------------------- prompt

def test_system_prompt_static_with_taxonomy_and_rules():
    p = tagger.system_prompt()
    for label in ("loaded_language", "doubt_casting", "unsupported_claim", "supported_claim", "balance"):
        assert label in p
    assert "{{TAXONOMY}}" not in p
    assert "HOW something is said" in p and "return an empty array []" in p
    assert "character for character" in p and "not instructions" in p
    assert tagger.system_prompt() == p and len(tagger.prompt_version()) == 10


def test_user_message_has_numbered_sentences_and_hints():
    seg = segment(TEXT)
    msg = tagger.user_message(seg, seg.paragraphs, extract_hints(seg))
    assert "[p1s1] The council approved the plan on Tuesday." in msg
    assert "[p2s2] Everyone knows it." in msg
    hint_line = next(line for line in msg.splitlines() if line.startswith("p2s2: "))
    assert 'bandwagon_phrase "Everyone knows"' in hint_line
    assert "Language: en" in msg


def test_normalize_and_hash():
    assert tagger.normalize("a​  b\n\tcＡ") == "a b cA"
    assert tagger.paragraph_hash("Hello   world") == tagger.paragraph_hash(" Hello world​ ")


# ------------------------------------------------------------------- tagging

def test_candidates_mapped_to_global_sentence_ids(monkeypatch):
    fake = install(monkeypatch, FakeLLM(responder_reckless))
    seg, res = tag(TEXT)
    assert len(fake.calls) == 1
    assert res.candidates == [{"sentence_id": "p2s1", "quote": "reckless scheme", "label": "loaded_language",
                               "confidence": 0.85, "explanation": "Emotionally charged wording.", "source": "llm"}]
    assert res.failed == set() and res.llm_calls == 1 and res.input_tokens == 100


def test_cache_hit_second_time_and_in_other_position(monkeypatch, _isolated):
    fake = install(monkeypatch, FakeLLM(responder_reckless))
    tag(TEXT)
    assert len(fake.calls) == 1
    _, res = tag(TEXT)
    assert len(fake.calls) == 1 and res.cached_paragraphs == 2 and res.llm_calls == 0
    # the same paragraph as the 3rd paragraph of another text -> cache hit, re-mapped ids
    other = "Intro line here.\n\nAnother calm line.\n\nThis reckless scheme will destroy our neighbourhood forever! Everyone knows it."
    _, res = tag(other)
    assert len(fake.calls) == 2  # only the 2 new paragraphs were sent
    assert "reckless scheme" not in fake.user_messages[-1]
    assert res.candidates[0]["sentence_id"] == "p3s1"


def test_whitespace_variants_share_cache(monkeypatch):
    fake = install(monkeypatch, FakeLLM(responder_reckless))
    tag("This reckless scheme will destroy us.")
    tag("This  reckless​ scheme will   destroy us.")
    assert len(fake.calls) == 1


def test_empty_result_is_cached(monkeypatch):
    fake = install(monkeypatch, FakeLLM())
    tag("The council met on Tuesday.")
    tag("The council met on Tuesday.")
    assert len(fake.calls) == 1


def test_private_mode_reads_but_never_writes_cache(monkeypatch, _isolated):
    fake = install(monkeypatch, FakeLLM(responder_reckless))
    tag(TEXT, private=True)
    assert _isolated.cache_size() == 0
    tag(TEXT, private=True)
    assert len(fake.calls) == 2
    tag(TEXT)  # public run fills the cache ...
    _, res = tag(TEXT, private=True)  # ... which a private run may read
    assert len(fake.calls) == 3 and res.cached_paragraphs == 2


def test_single_flight_concurrent_requests(monkeypatch):
    fake = install(monkeypatch, FakeLLM(responder_reckless, delay=0.05))

    async def both():
        s1, s2 = segment(TEXT), segment(TEXT)
        return await asyncio.gather(tagger.tag(s1, extract_hints(s1)), tagger.tag(s2, extract_hints(s2)))

    r1, r2 = run(both())
    assert len(fake.calls) == 1
    assert r1.candidates == r2.candidates and r1.candidates
    assert r2.cached_paragraphs == 2 or r1.cached_paragraphs == 2


def test_repeated_paragraph_in_one_text_sent_once(monkeypatch):
    fake = install(monkeypatch, FakeLLM(responder_reckless))
    para = "This reckless scheme will destroy our town."
    _, res = tag(f"{para}\n\n{para}")
    assert len(fake.calls) == 1
    assert fake.user_messages[0].count("reckless scheme") == 1
    assert [c["sentence_id"] for c in res.candidates] == ["p1s1", "p2s1"]


def test_chunking_long_text(monkeypatch):
    fake = install(monkeypatch, FakeLLM())
    paragraph = " ".join(["word"] * 500) + "."
    text = "\n\n".join(f"Paragraph {i} " + paragraph for i in range(5))  # ~2500 words
    tag(text)
    assert len(fake.calls) == 3  # 2 + 2 + 1 paragraphs per ~1200-word chunk


def test_llm_failure_fails_soft_and_is_not_cached(monkeypatch, _isolated):
    install(monkeypatch, FakeLLM(fail=True))
    _, res = tag(TEXT)
    assert res.failed == {"p1", "p2"} and res.candidates == []
    assert _isolated.cache_size() == 0


def test_bad_json_retried_once_then_fails_soft(monkeypatch):
    fake = install(monkeypatch, FakeLLM(lambda u, s: "I think there is loaded language here."))
    _, res = tag(TEXT)
    assert len(fake.calls) == 2 and res.failed == {"p1", "p2"}


def test_bad_json_then_good_json(monkeypatch):
    answers = iter(["oops not json", "[]"])
    fake = install(monkeypatch, FakeLLM(lambda u, s: next(answers)))
    _, res = tag(TEXT)
    assert len(fake.calls) == 2 and res.failed == set()


def test_malformed_items_and_unknown_sentence_ids_dropped(monkeypatch):
    items = [
        {"sentence_id": "p9s9", "quote": "reckless scheme", "label": "loaded_language", "confidence": 0.9},
        {"sentence_id": "p2s1", "quote": "", "label": "loaded_language"},
        "not a dict",
        {"sentence_id": "p2s1", "quote": "reckless scheme", "label": "loaded_language", "confidence": "high"},
        {"sentence_id": "p2s1", "quote": "destroy", "label": "fear_appeal", "confidence": 7},
    ]
    install(monkeypatch, FakeLLM(lambda u, s: json.dumps(items)))
    _, res = tag(TEXT)
    assert res.dropped_items == 3
    assert [(c["quote"], c["confidence"]) for c in res.candidates] == [("reckless scheme", 0.5), ("destroy", 1.0)]


def test_budget_over_means_no_call(monkeypatch, _isolated):
    fake = install(monkeypatch, FakeLLM())
    _isolated.log_usage("tagger", "test-model", 100_000, 0, 1, ok=True)  # cap is 100000 in tests
    _, res = tag(TEXT)
    assert fake.calls == [] and res.failed == {"p1", "p2"}


@pytest.mark.parametrize("n_words, chunks", [(100, 1), (1200, 1), (1300, 2)])
def test_chunk_paragraphs(n_words, chunks):
    text = "\n\n".join([" ".join(["w"] * (n_words // 2))] * 2)
    assert len(tagger.chunk_paragraphs(segment(text).paragraphs)) == chunks
