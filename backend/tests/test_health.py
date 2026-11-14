from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_ok(monkeypatch):
    monkeypatch.setenv("GIT_COMMIT", "abc12345")
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["commit"] == "abc12345"
    assert body["budget"]["mode"] == "ok"
    assert body["budget"]["tokens_today"] == 0
    assert body["llm"]["configured"] is False
    assert body["storage"] in ("sqlite", "memory")
    assert body["version"]


def test_health_commit_default(monkeypatch):
    monkeypatch.delenv("GIT_COMMIT", raising=False)
    assert client.get("/api/health").json()["commit"] == "dev"


def test_health_format_matches_deploy_sh():
    # deploy.sh greps for "commit": *"<8 hex chars>" in the raw response body
    import re
    raw = client.get("/api/health").text
    assert re.search(r'"commit": *"[^"]*"', raw)


def test_landing_page():
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "We flag the tactics, not the opinions." in r.text
