import json
import struct
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core.config import Settings
from app.main import create_app
from app.providers.stt.fake import FakeSTTProvider
from tests.conftest import register_and_login

PCM = b"\x10\x00" * 320  # 20 ms of 16 kHz mono PCM16


def frame(source: int, seq: int, pcm: bytes = PCM) -> bytes:
    return struct.pack("<BI", source, seq) + pcm


def open_meeting(
    client: TestClient, auth: dict[str, str], language: str = "pt-BR"
) -> dict[str, Any]:
    r = client.post("/api/v1/meetings", json={"language": language}, headers=auth)
    assert r.status_code == 201, r.text
    body: dict[str, Any] = r.json()
    return body


def drain(ws: Any) -> list[dict[str, Any]]:
    """Receive events until the server closes the socket."""
    events: list[dict[str, Any]] = []
    try:
        while True:
            events.append(ws.receive_json())
    except WebSocketDisconnect:
        return events


def types(events: list[dict[str, Any]]) -> list[str]:
    return [e["type"] for e in events]


def test_happy_path_transcribes_and_meters_usage(
    client: TestClient, auth: dict[str, str], stt: FakeSTTProvider
) -> None:
    m = open_meeting(client, auth)
    mid = m["meeting"]["id"]
    with client.websocket_connect(m["stream"]["stream_path"]) as ws:
        ws.send_json({"type": "auth", "token": m["stream"]["stream_token"]})
        started = ws.receive_json()
        assert started["type"] == "capture.started"
        assert started["v"] == 1 and started["meeting_id"] == mid
        assert started["payload"]["audio_format"]["sample_rate"] == 16000
        for i in range(250):  # 5 s of system audio
            ws.send_bytes(frame(0, i))
        for i in range(50):  # 1 s of microphone
            ws.send_bytes(frame(1, i))
        ws.send_json({"type": "capture.stopped"})
        events = drain(ws)

    finals = [e for e in events if e["type"] == "transcript.final"]
    partials = [e for e in events if e["type"] == "transcript.partial"]
    assert partials, "interim results are forwarded"
    assert {f["payload"]["source"] for f in finals} == {"system", "microphone"}
    assert all(f["payload"]["language"] == "pt-BR" for f in finals)
    assert types(events)[-1] == "capture.stopped"
    seqs = [started["seq"]] + [e["seq"] for e in events]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)

    usage = client.get("/api/v1/usage", headers=auth).json()
    assert usage["used_seconds"] == pytest.approx(6.0)
    assert client.get(f"/api/v1/meetings/{mid}", headers=auth).json()["audio_seconds"] == 6.0
    assert {s.source for s in stt.sessions} == {"system", "microphone"}


def test_audio_sent_while_paused_is_never_forwarded_or_billed(
    client: TestClient, auth: dict[str, str], stt: FakeSTTProvider
) -> None:
    m = open_meeting(client, auth)
    with client.websocket_connect(m["stream"]["stream_path"]) as ws:
        ws.send_json({"type": "auth", "token": m["stream"]["stream_token"]})
        ws.receive_json()
        for i in range(10):
            ws.send_bytes(frame(0, i))
        ws.send_json({"type": "capture.paused"})
        for i in range(100):  # a misbehaving client keeps sending
            ws.send_bytes(frame(0, 10 + i))
        ws.send_json({"type": "capture.resumed"})
        for i in range(10):
            ws.send_bytes(frame(0, 110 + i))
        ws.send_json({"type": "capture.stopped"})
        events = drain(ws)
    assert "capture.paused" in types(events) and "capture.resumed" in types(events)
    assert stt.sessions[0].frames == 20
    assert client.get("/api/v1/usage", headers=auth).json()["used_seconds"] == pytest.approx(0.4)


@pytest.mark.parametrize(
    "first_message",
    [
        {"type": "auth", "token": "garbage"},
        {"type": "hello"},
        {"token": None},
    ],
)
def test_connection_requires_valid_stream_token(
    client: TestClient, auth: dict[str, str], first_message: dict[str, Any]
) -> None:
    m = open_meeting(client, auth)
    with client.websocket_connect(m["stream"]["stream_path"]) as ws:
        ws.send_json(first_message)
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_json()
    assert e.value.code == 4401


def test_access_token_is_not_a_stream_token(client: TestClient, auth: dict[str, str]) -> None:
    m = open_meeting(client, auth)
    access = auth["Authorization"].removeprefix("Bearer ")
    with client.websocket_connect(m["stream"]["stream_path"]) as ws:
        ws.send_json({"type": "auth", "token": access})
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_json()
    assert e.value.code == 4401


def test_token_for_one_meeting_cannot_open_another(
    client: TestClient, auth: dict[str, str]
) -> None:
    a = open_meeting(client, auth)
    b = open_meeting(client, auth)
    with client.websocket_connect(b["stream"]["stream_path"]) as ws:
        ws.send_json({"type": "auth", "token": a["stream"]["stream_token"]})
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_json()
    assert e.value.code == 4401


def test_finished_meeting_cannot_stream(client: TestClient, auth: dict[str, str]) -> None:
    m = open_meeting(client, auth)
    client.post(f"/api/v1/meetings/{m['meeting']['id']}/finish", headers=auth)
    with client.websocket_connect(m["stream"]["stream_path"]) as ws:
        ws.send_json({"type": "auth", "token": m["stream"]["stream_token"]})
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_json()
    assert e.value.code == 4404


def test_one_concurrent_stream_per_user(client: TestClient, auth: dict[str, str]) -> None:
    a = open_meeting(client, auth)
    b = open_meeting(client, auth)
    with client.websocket_connect(a["stream"]["stream_path"]) as ws_a:
        ws_a.send_json({"type": "auth", "token": a["stream"]["stream_token"]})
        assert ws_a.receive_json()["type"] == "capture.started"
        with client.websocket_connect(b["stream"]["stream_path"]) as ws_b:
            ws_b.send_json({"type": "auth", "token": b["stream"]["stream_token"]})
            with pytest.raises(WebSocketDisconnect) as e:
                ws_b.receive_json()
        assert e.value.code == 4409
        ws_a.send_json({"type": "capture.stopped"})
        drain(ws_a)
    # After the first stream ends, the user can stream again.
    c = open_meeting(client, auth)
    with client.websocket_connect(c["stream"]["stream_path"]) as ws_c:
        ws_c.send_json({"type": "auth", "token": c["stream"]["stream_token"]})
        assert ws_c.receive_json()["type"] == "capture.started"
        ws_c.send_json({"type": "capture.stopped"})
        drain(ws_c)


def test_malformed_frame_closes_with_4400(client: TestClient, auth: dict[str, str]) -> None:
    m = open_meeting(client, auth)
    with client.websocket_connect(m["stream"]["stream_path"]) as ws:
        ws.send_json({"type": "auth", "token": m["stream"]["stream_token"]})
        ws.receive_json()
        ws.send_bytes(b"\x00\x01\x02")
        events = drain(ws)
    assert types(events)[-1] == "capture.stopped"


def test_quota_exhaustion_stops_stream_and_blocks_new_meetings(stt: FakeSTTProvider) -> None:
    settings = Settings(env="test", jwt_secret="k" * 40, trial_minutes_per_month=0)
    with TestClient(create_app(settings, stt_provider=stt)) as client:
        auth = register_and_login(client)
        r = client.post("/api/v1/meetings", json={}, headers=auth)
        assert r.status_code == 402


def test_quota_exceeded_mid_stream(stt: FakeSTTProvider) -> None:
    # 1 minute plan; the user already used 59.9 s.
    settings = Settings(env="test", jwt_secret="k" * 40, trial_minutes_per_month=1)
    with TestClient(create_app(settings, stt_provider=stt)) as client:
        auth = register_and_login(client)
        m = open_meeting(client, auth)
        with client.websocket_connect(m["stream"]["stream_path"]) as ws:
            ws.send_json({"type": "auth", "token": m["stream"]["stream_token"]})
            ws.receive_json()
            for i in range(3010):
                ws.send_bytes(frame(0, i))
            events = drain(ws)
        errors = [e for e in events if e["type"] == "error"]
        assert errors and errors[0]["payload"]["code"] == "quota_exceeded"
        assert errors[0]["payload"]["retryable"] is False
        used = client.get("/api/v1/usage", headers=auth).json()["used_seconds"]
        assert used == pytest.approx(60.0)


def test_provider_failure_is_reported_as_retryable_error(
    client: TestClient, auth: dict[str, str]
) -> None:
    client.app.state.stt_provider = FakeSTTProvider(fail_after_frames=5)  # type: ignore[attr-defined]
    m = open_meeting(client, auth)
    with client.websocket_connect(m["stream"]["stream_path"]) as ws:
        ws.send_json({"type": "auth", "token": m["stream"]["stream_token"]})
        ws.receive_json()
        for i in range(10):
            ws.send_bytes(frame(0, i))
        ws.send_json({"type": "capture.stopped"})
        events = drain(ws)
    err = next(e for e in events if e["type"] == "error")
    assert err["payload"] == {
        "code": "stt_error",
        "message": "fake provider failure",
        "retryable": True,
    }
    # No transcript text or audio leaks into error payloads.
    assert "segment" not in json.dumps(err)


def test_replayed_frames_after_reconnect_are_not_retranscribed_or_rebilled(
    client: TestClient, auth: dict[str, str], stt: FakeSTTProvider
) -> None:
    m = open_meeting(client, auth)
    mid = m["meeting"]["id"]
    path = m["stream"]["stream_path"]
    with client.websocket_connect(path) as ws:
        ws.send_json({"type": "auth", "token": m["stream"]["stream_token"]})
        ws.receive_json()
        for i in range(100):
            ws.send_bytes(frame(0, i))
    # Connection dropped; the client reconnects with a fresh token and replays 90..149.
    token = client.post(f"/api/v1/meetings/{mid}/stream-token", headers=auth).json()
    with client.websocket_connect(path) as ws:
        ws.send_json({"type": "auth", "token": token["stream_token"]})
        ws.receive_json()
        for i in range(90, 150):
            ws.send_bytes(frame(0, i))
        ws.send_json({"type": "capture.stopped"})
        drain(ws)
    assert sum(s.frames for s in stt.sessions) == 150
    assert client.get("/api/v1/usage", headers=auth).json()["used_seconds"] == pytest.approx(3.0)


def test_replay_protection_survives_a_server_restart(settings: Settings) -> None:
    """Deploys restart the server; the client then replays its last frames."""
    first_stt = FakeSTTProvider()
    with TestClient(create_app(settings, stt_provider=first_stt)) as c1:
        auth = register_and_login(c1)
        m = open_meeting(c1, auth)
        mid, path = m["meeting"]["id"], m["stream"]["stream_path"]
        with c1.websocket_connect(path) as ws:
            ws.send_json({"type": "auth", "token": m["stream"]["stream_token"]})
            ws.receive_json()
            for i in range(100):
                ws.send_bytes(frame(0, i))
            ws.send_json({"type": "capture.stopped"})
            drain(ws)
    # New process, same database, fresh in-memory state.
    second_stt = FakeSTTProvider()
    with TestClient(create_app(settings, stt_provider=second_stt)) as c2:
        token = c2.post(f"/api/v1/meetings/{mid}/stream-token", headers=auth).json()
        with c2.websocket_connect(path) as ws:
            ws.send_json({"type": "auth", "token": token["stream_token"]})
            ws.receive_json()
            for i in range(50, 150):  # 50 replayed + 50 new
                ws.send_bytes(frame(0, i))
            ws.send_json({"type": "capture.stopped"})
            events = drain(ws)
        assert sum(s.frames for s in second_stt.sessions) == 50
        # Timestamps continue the meeting timeline (frame 100 = 2.0 s), not restart at 0.
        finals = [e["payload"] for e in events if e["type"] == "transcript.final"]
        assert finals and min(f["start_ms"] for f in finals) == 2000
        assert c2.get("/api/v1/usage", headers=auth).json()["used_seconds"] == pytest.approx(3.0)
