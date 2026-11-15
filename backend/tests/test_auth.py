from fastapi.testclient import TestClient

from tests.conftest import PASSWORD, register_and_login


def test_register_login_me(client: TestClient) -> None:
    headers = register_and_login(client, "Bia@Example.com")
    me = client.get("/api/v1/me", headers=headers).json()
    assert me["email"] == "bia@example.com"
    assert me["plan"] == "trial"
    assert me["store_transcripts"] is False  # privacy default


def test_password_hash_is_argon2_and_not_returned(client: TestClient) -> None:
    r = client.post("/api/v1/auth/register", json={"email": "c@example.com", "password": PASSWORD})
    assert "password" not in r.text and "hash" not in r.text


def test_duplicate_and_weak_password(client: TestClient) -> None:
    register_and_login(client)
    r = client.post(
        "/api/v1/auth/register", json={"email": "ana@example.com", "password": PASSWORD}
    )
    assert r.status_code == 409
    r = client.post("/api/v1/auth/register", json={"email": "z@example.com", "password": "short"})
    assert r.status_code == 422


def test_wrong_password_and_unknown_user_look_identical(client: TestClient) -> None:
    register_and_login(client)
    a = client.post("/api/v1/auth/login", json={"email": "ana@example.com", "password": "nope"})
    b = client.post("/api/v1/auth/login", json={"email": "who@example.com", "password": "nope"})
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()


def test_protected_routes_require_valid_access_token(client: TestClient) -> None:
    assert client.get("/api/v1/me").status_code == 401
    bad = {"Authorization": "Bearer not-a-token"}
    assert client.get("/api/v1/me", headers=bad).status_code == 401


def test_stream_token_cannot_be_used_as_access_token(
    client: TestClient, auth: dict[str, str]
) -> None:
    stream = client.post("/api/v1/meetings", json={}, headers=auth).json()["stream"]
    r = client.get("/api/v1/me", headers={"Authorization": f"Bearer {stream['stream_token']}"})
    assert r.status_code == 401


def test_login_rate_limit(client: TestClient) -> None:
    codes = [
        client.post(
            "/api/v1/auth/login", json={"email": "x@example.com", "password": "bad"}
        ).status_code
        for _ in range(12)
    ]
    assert codes[:10] == [401] * 10
    assert codes[10:] == [429, 429]


def _login(client: TestClient) -> dict[str, str]:
    client.post("/api/v1/auth/register", json={"email": "r@example.com", "password": PASSWORD})
    r = client.post("/api/v1/auth/login", json={"email": "r@example.com", "password": PASSWORD})
    body: dict[str, str] = r.json()
    return body


def test_refresh_rotates_tokens(client: TestClient) -> None:
    first = _login(client)
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert r.status_code == 200
    second = r.json()
    assert second["refresh_token"] != first["refresh_token"]
    me = client.get("/api/v1/me", headers={"Authorization": f"Bearer {second['access_token']}"})
    assert me.status_code == 200


def test_refresh_token_reuse_revokes_the_whole_family(client: TestClient) -> None:
    first = _login(client)
    second = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]}
    ).json()
    # Replaying the rotated token (e.g. stolen) fails and revokes the newer one too.
    replay = client.post("/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert replay.status_code == 401
    after = client.post("/api/v1/auth/refresh", json={"refresh_token": second["refresh_token"]})
    assert after.status_code == 401


def test_logout_revokes_refresh_token(client: TestClient) -> None:
    tokens = _login(client)
    assert (
        client.post(
            "/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]}
        ).status_code
        == 204
    )
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 401


def test_unknown_refresh_token_rejected(client: TestClient) -> None:
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": "x" * 40})
    assert r.status_code == 401
