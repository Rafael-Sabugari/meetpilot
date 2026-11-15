from fastapi.testclient import TestClient

from tests.conftest import register_and_login


def test_create_list_get_finish_delete(client: TestClient, auth: dict[str, str]) -> None:
    r = client.post("/api/v1/meetings", json={"language": "en", "title": "Sync"}, headers=auth)
    assert r.status_code == 201
    body = r.json()
    mid = body["meeting"]["id"]
    assert body["meeting"]["status"] == "active"
    assert body["stream"]["stream_path"] == f"/api/v1/meetings/{mid}/stream"
    assert body["stream"]["expires_in"] == 120

    assert [m["id"] for m in client.get("/api/v1/meetings", headers=auth).json()] == [mid]
    assert client.get(f"/api/v1/meetings/{mid}", headers=auth).json()["title"] == "Sync"

    fin = client.post(f"/api/v1/meetings/{mid}/finish", headers=auth).json()
    assert fin["status"] == "finished" and fin["ended_at"]
    assert client.post(f"/api/v1/meetings/{mid}/stream-token", headers=auth).status_code == 409

    assert client.delete(f"/api/v1/meetings/{mid}", headers=auth).status_code == 204
    assert client.get(f"/api/v1/meetings/{mid}", headers=auth).status_code == 404


def test_users_cannot_see_or_touch_each_others_meetings(
    client: TestClient, auth: dict[str, str]
) -> None:
    mid = client.post("/api/v1/meetings", json={}, headers=auth).json()["meeting"]["id"]
    other = register_and_login(client, "eve@example.com")
    assert client.get("/api/v1/meetings", headers=other).json() == []
    for method, path in [
        ("get", f"/api/v1/meetings/{mid}"),
        ("post", f"/api/v1/meetings/{mid}/finish"),
        ("post", f"/api/v1/meetings/{mid}/stream-token"),
        ("delete", f"/api/v1/meetings/{mid}"),
    ]:
        assert client.request(method, path, headers=other).status_code == 404, path
    assert client.get(f"/api/v1/meetings/{mid}", headers=auth).status_code == 200


def test_invalid_language_rejected(client: TestClient, auth: dict[str, str]) -> None:
    assert client.post("/api/v1/meetings", json={"language": "xx"}, headers=auth).status_code == 422


def test_usage_starts_at_zero_with_trial_limit(client: TestClient, auth: dict[str, str]) -> None:
    u = client.get("/api/v1/usage", headers=auth).json()
    assert u["used_seconds"] == 0
    assert u["limit_seconds"] == 3600
    assert u["remaining_seconds"] == 3600
