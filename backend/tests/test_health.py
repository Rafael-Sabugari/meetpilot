from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def client() -> TestClient:
    return TestClient(create_app(Settings(env="test")))


def test_health_ok() -> None:
    r = client().get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["env"] == "test"
    assert r.headers["x-request-id"]


def test_request_id_is_propagated() -> None:
    r = client().get("/api/v1/health", headers={"x-request-id": "abc123"})
    assert r.headers["x-request-id"] == "abc123"


def test_readiness_reports_unconfigured_dependencies() -> None:
    r = client().get("/api/v1/health/ready")
    assert r.json()["dependencies"] == {"database": "not_configured", "redis": "not_configured"}


def test_docs_disabled_in_production() -> None:
    s = Settings(
        env="production",
        jwt_secret="x" * 32,
        database_url="sqlite+aiosqlite://",
        stt_provider="deepgram",
        deepgram_api_key="dg",
        llm_provider="openai",
        openai_api_key="sk-test",
    )
    c = TestClient(create_app(s))
    assert c.get("/api/docs").status_code == 404
