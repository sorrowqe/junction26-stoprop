"""Shared test setup: fresh in-memory store, no real LLM, clean rate limits for every test."""
import pytest

from app import ratelimit
from app.llm import client as llm_client
from app.pipeline import extract, tagger
from app.receipts import http as receipts_http
from app.receipts import service as receipts_service
from app.store import Store, set_store


class RealNetworkInTest(BaseException):
    """BaseException so that no 'except Exception' in app code can swallow it."""


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
    monkeypatch.setattr(llm_client, "embed", _no_network)

    async def _no_dns(host, port):
        raise RealNetworkInTest(f"real DNS lookup attempted in a test: {host}")

    def _no_http(*a, **k):
        raise RealNetworkInTest("real HTTP client created in a test")

    monkeypatch.setattr(extract, "resolver", _no_dns)
    monkeypatch.setattr(receipts_http, "client_factory", _no_http)
    from app.pipeline import oxylabs, websearch
    from app.receipts import pagecheck, rdap
    from app.swarm import collectors
    from app.swarm.collectors import http as collectors_http
    monkeypatch.setattr(collectors_http, "client_factory", _no_http)
    monkeypatch.setattr(oxylabs, "client_factory", _no_http)

    async def _no_whois(_domain: str):
        return None

    async def _no_pagecheck(_target: str):
        return None

    monkeypatch.setattr(rdap, "whois_lookup", _no_whois)
    monkeypatch.setattr(pagecheck, "inspect_target", _no_pagecheck)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("SEARCH_API_KEY", raising=False)
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.delenv("OXYLABS_API_KEY", raising=False)
    monkeypatch.delenv("OXYLABS_DAILY_CAP", raising=False)
    monkeypatch.delenv("RATE_LIMIT_MULTIPLIER", raising=False)
    monkeypatch.delenv("NEWS_SEARCH", raising=False)
    from app.verify import news as verify_news, run as verify_run
    verify_news.reset()
    verify_run.reset()
    oxylabs.reset()
    websearch.reset()
    monkeypatch.setenv("SEARCH_PROVIDER", "none")
    collectors.reset_cache()
    monkeypatch.delenv("FACTCHECK_API_KEY", raising=False)
    receipts_service.reset()
    yield store
    set_store(None)
