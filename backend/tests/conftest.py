from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.auth import router as auth_router
from app.core.config import Settings
from app.main import create_app
from app.providers.stt.fake import FakeSTTProvider

PASSWORD = "correct horse battery"


@pytest.fixture(autouse=True)
def _reset_rate_limiter() -> None:
    auth_router._login_limiter._events.clear()


@pytest.fixture
def stt() -> FakeSTTProvider:
    return FakeSTTProvider()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    # File-backed SQLite: behaves like a real database across connections.
    return Settings(
        env="test",
        jwt_secret="test-secret-" + "x" * 32,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
    )


@pytest.fixture
def client(settings: Settings, stt: FakeSTTProvider) -> Iterator[TestClient]:
    with TestClient(create_app(settings, stt_provider=stt)) as c:
        yield c


def register_and_login(client: TestClient, email: str = "ana@example.com") -> dict[str, str]:
    r = client.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD})
    assert r.status_code == 201, r.text
    r = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def auth(client: TestClient) -> dict[str, str]:
    return register_and_login(client)
