"""LLM client: JSON extraction, retry policy and usage logging, through the real OpenAI SDK
with a mocked HTTP transport (no network)."""
import asyncio
import json
import pathlib

import httpx2
import pytest

from app.llm import client as llm_client
from app.llm.client import LLMError, extract_json_array
from app.llm.client import chat as real_chat  # imported before conftest patches the module attribute


# ------------------------------------------------------------------ JSON

@pytest.mark.parametrize(
    "text, expected",
    [
        ("[]", []),
        ('[{"a": 1}]', [{"a": 1}]),
        ('```json\n[{"a": 1}]\n```', [{"a": 1}]),
        ('Here you go:\n[{"a": 1}]\nHope this helps!', [{"a": 1}]),
        ('{"spans": [{"a": 1}]}', [{"a": 1}]),
        ('{"findings": []}', []),
        ("Nothing found.", None),
        ("[{broken json", None),
        ("", None),
        (None, None),
    ],
)
def test_extract_json_array(text, expected):
    assert extract_json_array(text) == expected


# ------------------------------------------------------------ transport

def _completion(content="[]", tin=100, tout=20):
    return {
        "id": "x", "object": "chat.completion", "created": 0, "model": "test-model",
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": tin, "completion_tokens": tout, "total_tokens": tin + tout},
    }


def _install(monkeypatch, responses):
    """responses: list of (status, json) or Exception, consumed in order."""
    calls = []

    def handler(request: httpx2.Request):
        calls.append(json.loads(request.content))
        r = responses.pop(0)
        if isinstance(r, Exception):
            raise r
        status, body = r
        return httpx2.Response(status, json=body)

    monkeypatch.setenv("ROUTER_API_KEY", "sk-test-not-real")
    monkeypatch.setenv("ROUTER_BASE_URL", "https://router.invalid/v1")
    monkeypatch.setattr(llm_client, "http_client_factory", lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))
    return calls


def run(coro):
    return asyncio.run(coro)


def test_success_logs_usage(monkeypatch, _isolated):
    calls = _install(monkeypatch, [(200, _completion('[{"x": 1}]', 120, 30))])
    out = run(real_chat([{"role": "user", "content": "hi"}], model="test-model"))
    assert out.text == '[{"x": 1}]' and out.input_tokens == 120 and out.output_tokens == 30
    assert calls[0]["temperature"] == 0 and calls[0]["model"] == "test-model"
    assert _isolated.usage_rows() == [("tagger", "test-model", 120, 30, 1, None)]


def test_retry_once_on_5xx(monkeypatch, _isolated):
    _install(monkeypatch, [(502, {"error": "bad gateway"}), (200, _completion())])
    out = run(real_chat([{"role": "user", "content": "hi"}], model="test-model"))
    assert out.text == "[]"
    rows = _isolated.usage_rows()
    assert [r[4] for r in rows] == [0, 1]  # failed attempt logged, then success


def test_gives_up_after_one_retry(monkeypatch, _isolated):
    _install(monkeypatch, [(500, {}), (503, {})])
    with pytest.raises(LLMError):
        run(real_chat([{"role": "user", "content": "hi"}], model="test-model"))
    assert len(_isolated.usage_rows()) == 2


def test_retry_on_timeout(monkeypatch, _isolated):
    _install(monkeypatch, [httpx2.ReadTimeout("slow"), (200, _completion())])
    assert run(real_chat([{"role": "user", "content": "hi"}], model="test-model")).text == "[]"


def test_no_retry_on_4xx(monkeypatch, _isolated):
    _install(monkeypatch, [(400, {"error": "bad request"}), (200, _completion())])
    with pytest.raises(LLMError):
        run(real_chat([{"role": "user", "content": "hi"}], model="test-model"))
    assert len(_isolated.usage_rows()) == 1


def test_not_configured(monkeypatch):
    monkeypatch.delenv("ROUTER_API_KEY", raising=False)
    with pytest.raises(LLMError):
        run(real_chat([{"role": "user", "content": "hi"}], model="test-model"))


def test_api_key_never_logged(monkeypatch, _isolated):
    _install(monkeypatch, [(401, {"error": "invalid key sk-test-not-real"})])
    with pytest.raises(LLMError) as exc:
        run(real_chat([{"role": "user", "content": "hi"}], model="test-model"))
    assert "sk-test" not in str(exc.value)
    assert all("sk-test" not in str(r) for r in _isolated.usage_rows())


def test_runtime_code_does_not_import_classic_httpx():
    """openai 3.x ships httpx2; classic httpx is only a dev/test dependency."""
    app_dir = pathlib.Path(__file__).resolve().parents[1] / "app"
    for path in app_dir.rglob("*.py"):
        for line in path.read_text(encoding="utf-8").splitlines():
            assert not line.strip().startswith(("import httpx ", "from httpx ")) and line.strip() != "import httpx", path


def test_credits_charged_logged(monkeypatch, _isolated):
    body = _completion("[]", 1000, 100)
    body["usage"]["credits_charged"] = 0.4321
    _install(monkeypatch, [(200, body)])
    out = run(real_chat([{"role": "user", "content": "hi"}], model="test-model"))
    assert out.credits == 0.4321
    assert _isolated.usage_today()["credits"] == 0.4321


def test_store_migrates_old_usage_table(tmp_path):
    import sqlite3
    from app.store import Store
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE usage_log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, day TEXT NOT NULL, "
                "kind TEXT NOT NULL, model TEXT NOT NULL, input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER "
                "NOT NULL DEFAULT 0, latency_ms INTEGER NOT NULL DEFAULT 0, ok INTEGER NOT NULL, error TEXT)")
    con.commit()
    con.close()
    s = Store(db)
    assert s.persistent
    s.log_usage("tagger", "m", 1, 2, 3, ok=True, credits=0.5)
    assert s.usage_today()["credits"] == 0.5
