"""Shared test setup: fresh in-memory store, no real LLM, clean rate limits for every test."""
import pytest

from app import ratelimit
from app.llm import client as llm_client
from app.pipeline import tagger
from app.store import Store, set_store


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    # Never touch a real key or the network from tests.
    monkeypatch.delenv("ROUTER_API_KEY", raising=False)
    monkeypatch.setenv("MODEL_TAGGER", "test-model")
    monkeypatch.setenv("DAILY_TOKEN_CAP", "100000")
    store = Store(":memory:")
    set_store(store)
    ratelimit.limiter.reset()
    tagger.reset_inflight()

    async def _no_network(*args, **kwargs):  # any un-mocked LLM call fails loudly
        raise AssertionError("real LLM call attempted in a test")

    monkeypatch.setattr(llm_client, "chat", _no_network)
    yield store
    set_store(None)
