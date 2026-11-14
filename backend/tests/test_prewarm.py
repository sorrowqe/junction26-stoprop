"""Pre-warm must never block startup or /api/health, and must survive a broken router."""
import asyncio
import time

from fastapi.testclient import TestClient

from app import config
from app.llm import client as llm_client
from app.main import app
from fakellm import FakeLLM, install


def test_db_lives_in_the_persistent_data_volume():
    # in the container REPO_ROOT is /app, and compose mounts ./data:/app/data
    assert config.DB_PATH == config.REPO_ROOT / "data" / "stoprop.db"
    assert config.DATA_DIR == config.REPO_ROOT / "data"


def test_slow_router_does_not_block_startup_or_health(monkeypatch):
    install(monkeypatch, FakeLLM(delay=5))  # every call hangs for 5 s
    t0 = time.monotonic()
    with TestClient(app) as c:
        r = c.get("/api/health")
        elapsed = time.monotonic() - t0
    assert r.status_code == 200 and r.json()["status"] == "ok"
    assert elapsed < 2, elapsed


def test_crashing_router_only_logs(monkeypatch, caplog):
    monkeypatch.setenv("ROUTER_API_KEY", "sk-test-not-real")

    async def boom(*a, **k):
        raise RuntimeError("router exploded")

    monkeypatch.setattr(llm_client, "chat", boom)
    with TestClient(app) as c:
        for _ in range(50):
            if "pre-warm failed" in caplog.text:
                break
            time.sleep(0.02)
        assert c.get("/api/health").status_code == 200
    assert "pre-warm failed" in caplog.text


def test_prewarm_cached_second_time(monkeypatch, _isolated):
    from app.analyze import prewarm_samples
    fake = install(monkeypatch, FakeLLM())
    asyncio.run(prewarm_samples())
    first = len(fake.calls)
    asyncio.run(prewarm_samples())  # e.g. after a redeploy with the same model + prompt version
    assert first == 3 and len(fake.calls) == 3
    assert _isolated.cache_size() > 0


def test_prewarm_skipped_without_key():
    from app.analyze import prewarm_samples
    asyncio.run(prewarm_samples())  # conftest makes any real LLM call fail loudly
