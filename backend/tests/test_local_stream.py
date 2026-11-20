"""Free mode: the desktop transcribes on the user's computer and sends text, not audio."""

import struct
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core.config import Settings
from app.main import create_app
from app.providers.llm.fake import FakeLLMProvider
from app.providers.stt.fake import FakeSTTProvider
from tests.conftest import register_and_login


def make_client(tmp_path: Path, **overrides: Any) -> tuple[TestClient, FakeLLMProvider]:
    llm = FakeLLMProvider()
    s = Settings(
        env="test",
        jwt_secret="k" * 40,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'local.db'}",
        copilot_auto_min_interval_seconds=0,
        copilot_utterance_gap_seconds=0,
        **overrides,
    )
    return TestClient(create_app(s, stt_provider=FakeSTTProvider(), llm_provider=llm)), llm


def segment(text: str, start: int, *, final: bool = True, source: str = "system") -> dict[str, Any]:
    return {
        "type": "transcript.segment",
        "source": source,
        "text": text,
        "start_ms": start,
        "end_ms": start + 1500,
        "is_final": final,
        "language": "pt",
    }


def run(
    c: TestClient, auth: dict[str, str], messages: list[Any], meeting: dict[str, Any] | None = None
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    m = (
        meeting
        or c.post(
            "/api/v1/meetings", json={"language": "pt-BR", "stt": "local"}, headers=auth
        ).json()
    )
    events: list[dict[str, Any]] = []
    with c.websocket_connect(m["stream"]["stream_path"]) as ws:
        ws.send_json({"type": "auth", "token": m["stream"]["stream_token"], "stt": "local"})
        for msg in messages:
            if isinstance(msg, bytes):
                ws.send_bytes(msg)
            else:
                ws.send_json(msg)
        ws.send_json({"type": "capture.stopped"})
        try:
            while True:
                events.append(ws.receive_json())
        except WebSocketDisconnect:
            pass
    return m, events


def of(events: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [e["payload"] for e in events if e["type"] == kind]


def test_local_text_gets_transcript_events_and_suggestions(tmp_path: Path) -> None:
    c, _ = make_client(tmp_path)
    with c:
        auth = register_and_login(c)
        _, events = run(
            c,
            auth,
            [
                segment("Qual é a sua", 0, final=False),
                segment("Qual é a sua experiência com Kubernetes?", 0),
                segment("Tenho três anos.", 2000, source="microphone"),
            ],
        )
        assert of(events, "capture.started")[0]["provider"] == "whisper-local"
        assert [p["text"] for p in of(events, "transcript.partial")] == ["Qual é a sua"]
        finals = of(events, "transcript.final")
        assert [f["segment_id"] for f in finals] == ["system-0", "microphone-2000"]
        assert of(events, "question.detected")[0]["text"].endswith("Kubernetes?")
        assert len(of(events, "answer.completed")) == 1
        usage = c.get("/api/v1/usage", headers=auth).json()
        assert usage["used_seconds"] == 0, "local transcription is free: no audio minutes used"
        assert usage["copilot_requests_used"] == 1


def test_works_with_no_transcription_minutes_left(tmp_path: Path) -> None:
    c, _ = make_client(tmp_path, trial_minutes_per_month=0)
    with c:
        auth = register_and_login(c)
        _, events = run(c, auth, [segment("Bom dia a todos.", 0)])
        assert of(events, "transcript.final")[0]["text"] == "Bom dia a todos."


def test_audio_is_ignored_in_local_mode(tmp_path: Path) -> None:
    c, _ = make_client(tmp_path)
    with c:
        auth = register_and_login(c)
        frame = struct.pack("<BI", 0, 0) + b"\x00\x00" * 320
        _, events = run(c, auth, [frame, frame])
        errors = [e for e in of(events, "error") if e["code"] == "audio_not_expected"]
        assert len(errors) == 1
        assert of(events, "transcript.final") == []


def test_finals_resent_after_reconnect_are_not_repeated(tmp_path: Path) -> None:
    c, _ = make_client(tmp_path)
    with c:
        auth = register_and_login(c)
        m, first = run(c, auth, [segment("Você pode explicar a arquitetura?", 0)])
        _, second = run(
            c,
            auth,
            [
                segment("Você pode explicar a arquitetura?", 0),  # re-sent
                segment("E o prazo, como fica?", 3000),
            ],
            meeting={
                "id": m["meeting"]["id"],
                "stream": c.post(
                    f"/api/v1/meetings/{m['meeting']['id']}/stream-token", headers=auth
                ).json(),
            },
        )
        assert [f["text"] for f in of(second, "transcript.final")] == ["E o prazo, como fica?"]
        assert len(of(first, "question.detected")) == 1


def test_invalid_segments_are_rejected_without_closing(tmp_path: Path) -> None:
    c, _ = make_client(tmp_path)
    with c:
        auth = register_and_login(c)
        _, events = run(
            c,
            auth,
            [
                {"type": "transcript.segment", "source": "speaker", "text": "x"},
                segment("x" * 2001, 0),
                segment("Tudo certo por aqui.", 5000),
            ],
        )
        assert [e["code"] for e in of(events, "error")] == ["bad_segment", "bad_segment"]
        assert [f["text"] for f in of(events, "transcript.final")] == ["Tudo certo por aqui."]


def test_cloud_mode_still_ignores_text_segments(tmp_path: Path) -> None:
    c, _ = make_client(tmp_path)
    with c:
        auth = register_and_login(c)
        m = c.post("/api/v1/meetings", json={"language": "pt-BR"}, headers=auth).json()
        events: list[dict[str, Any]] = []
        with c.websocket_connect(m["stream"]["stream_path"]) as ws:
            ws.send_json({"type": "auth", "token": m["stream"]["stream_token"]})
            ws.send_json(segment("Injetado pelo cliente?", 0))
            ws.send_json({"type": "capture.stopped"})
            try:
                while True:
                    events.append(ws.receive_json())
            except WebSocketDisconnect:
                pass
        assert of(events, "transcript.final") == []


def test_cloud_meetings_still_need_minutes(tmp_path: Path) -> None:
    c, _ = make_client(tmp_path, trial_minutes_per_month=0)
    with c:
        auth = register_and_login(c)
        r = c.post("/api/v1/meetings", json={"language": "pt-BR"}, headers=auth)
        assert r.status_code == 402
        r = c.post("/api/v1/meetings", json={"language": "pt-BR", "stt": "local"}, headers=auth)
        assert r.status_code == 201


def test_whisper_garbage_is_not_shown_or_sent_to_the_ai(tmp_path: Path) -> None:
    c, llm = make_client(tmp_path)
    with c:
        auth = register_and_login(c)
        _, events = run(
            c,
            auth,
            [
                segment("s, the.s, the.s, the.s, the.s, the.s, the.s, the.s", 0, final=False),
                segment("s, the.s, the.s, the.s, the.s, the.s, the.s, the.s, the.s", 0),
                segment("W-icкм. all gone Currys dob. sheesh serso. keuf. zij. ogo. Ro.", 4000),
                segment("Okay. That was clear.", 8000),
            ],
        )
        assert of(events, "transcript.partial") == []
        assert [f["text"] for f in of(events, "transcript.final")] == ["Okay. That was clear."]
        assert all("the.s" not in call["input_text"] for call in llm.calls)


def test_user_can_ask_for_a_reply_to_what_others_said(tmp_path: Path) -> None:
    c, llm = make_client(tmp_path)
    with c:
        auth = register_and_login(c)
        _, events = run(
            c,
            auth,
            [
                {"type": "copilot.settings", "auto_suggestions": "off", "translate_to": "pt-BR"},
                segment("Tell me about your last project and the team size.", 0),
                {"type": "copilot.request", "question": "Tell me about your last project."},
                {"type": "copilot.request", "question": "double press"},
            ],
        )
        started = of(events, "answer.started")
        assert [s["question_id"] for s in started] == ["r-1"], "auto off, double press ignored"
        assert started[0]["question"] == "Tell me about your last project."
        assert of(events, "answer.delta") and of(events, "answer.completed")
        assert of(events, "answer.translated")
        sent = [x for x in llm.calls if x["schema_name"] == "copilot_answer"][-1]["input_text"]
        assert "the User asked for a reply" in sent
