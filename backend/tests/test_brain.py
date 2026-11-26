from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.ai.context import ContextSegment, build_context
from app.ai.copilot import build_input, instructions
from app.brain.ai import render_details
from app.brain.search import MemoryDoc, recent, render, search, split_passages, tokens
from app.providers.llm.fake import FakeLLMProvider
from tests.conftest import register_and_login
from tests.test_live_assist import FINAL_FRAMES, frame, make_client

NOW = datetime(2026, 11, 24, tzinfo=UTC)


def doc(i: str, title: str, text: str, days_ago: int = 1) -> MemoryDoc:
    return MemoryDoc(i, "meeting", title, NOW - timedelta(days=days_ago), text)


# -- search ---------------------------------------------------------------------------------


def test_tokens_fold_accents_and_drop_stopwords() -> None:
    assert tokens("A Reunião com o João sobre Trigger.new") == [
        "reuniao",
        "joao",
        "trigger.new",
        "trigger",
        "new",
    ]


def test_search_finds_the_relevant_memory_first() -> None:
    docs = [
        doc(
            "a",
            "Entrevista Acme",
            "Perguntaram sobre Bulk API e migração de 2 milhões de registros.",
        ),
        doc("b", "Daily do time", "Status das tarefas, nada de migração."),
        doc("c", "Cliente Beta", "Proposta comercial e prazo de entrega em dezembro."),
    ]
    hits = search(docs, "o que falei sobre a migração com bulk api?", now=NOW)
    assert hits[0].passage.memory_id == "a"
    assert {h.passage.memory_id for h in hits} <= {"a", "b"}
    assert search(docs, "kubernetes", now=NOW) == []
    assert search(docs, "o que é isso?", now=NOW) == [], "only stopwords: nothing to match"


def test_recent_memories_win_ties() -> None:
    docs = [doc("old", "Acme", "prazo do projeto", 90), doc("new", "Acme", "prazo do projeto", 2)]
    assert search(docs, "prazo acme", now=NOW)[0].passage.memory_id == "new"
    assert [h.passage.memory_id for h in recent(docs, 1)] == ["new"]


def test_long_memories_are_split_into_passages() -> None:
    text = "\n\n".join(f"Parágrafo {i} " + "palavra " * 80 for i in range(10))
    parts = split_passages(doc("x", "Longa", text))
    assert len(parts) > 3
    assert all(len(p.text) <= 1_200 for p in parts)
    assert split_passages(doc("y", "Vazia", ""))[0].text == "Vazia"


def test_render_respects_the_budget_and_escapes_titles() -> None:
    docs = [doc(str(i), f'Reunião "{i}"', "Acme " * 100) for i in range(5)]
    hits = search(docs, "acme", now=NOW)
    out = render(hits, 1_200)
    assert out.count("<memory ") == 2
    assert "title=\"Reunião '0'\"" in out or "title=\"Reunião '" in out


def test_render_details_skips_empty_sections() -> None:
    md = render_details({"topics": ["Bulk API"], "decisions": [], "action_items": ["Enviar CV"]})
    assert md == "## Assuntos\n- Bulk API\n\n## Próximos passos\n- Enviar CV"


# -- copilot prompt -------------------------------------------------------------------------


def test_memory_goes_after_the_cached_user_context() -> None:
    ctx = build_context([ContextSegment(source="system", text="Oi", start_ms=0)], 1000)
    text = build_input("Q?", ctx, "pt-BR", "Sou o Rafael", (), '<memory id="1">x</memory>')
    assert text.startswith("<user_context>\nSou o Rafael\n</user_context>\n<memory>\n")
    assert "<memory>" not in build_input("Q?", ctx, "pt-BR", None, (), None)
    assert "14. <memory>" in instructions("pt-BR", "natural")


# -- API ------------------------------------------------------------------------------------


def note(c: TestClient, auth: dict[str, str], title: str, text: str) -> dict[str, Any]:
    r = c.post("/api/v1/brain/memories", json={"title": title, "text": text}, headers=auth)
    assert r.status_code == 201, r.text
    body: dict[str, Any] = r.json()
    return body


def test_notes_crud_search_and_isolation(tmp_path: Path) -> None:
    with make_client(tmp_path, FakeLLMProvider()) as c:
        auth = register_and_login(c)
        a = note(c, auth, "Projeto Acme", "Migrei 2 milhões de registros com Bulk API.")
        note(c, auth, "Receita de bolo", "Farinha, ovos e açúcar.")
        listed = c.get("/api/v1/brain/memories", headers=auth).json()
        assert len(listed) == 2 and listed[0]["kind"] == "note"
        found = c.get("/api/v1/brain/memories", params={"q": "bulk api"}, headers=auth).json()
        assert [m["id"] for m in found] == [a["id"]]
        r = c.patch(
            f"/api/v1/brain/memories/{a['id']}",
            json={"title": "Acme (migração)", "suggestions": [" x ", ""]},
            headers=auth,
        )
        assert r.json()["title"] == "Acme (migração)" and r.json()["suggestions"] == ["x"]
        other = register_and_login(c, "bia@example.com")
        assert c.get(f"/api/v1/brain/memories/{a['id']}", headers=other).status_code == 404
        assert c.get("/api/v1/brain/memories", headers=other).json() == []
        assert c.delete(f"/api/v1/brain/memories/{a['id']}", headers=auth).status_code == 204
        assert len(c.get("/api/v1/brain/memories", headers=auth).json()) == 1


def test_ask_and_briefing_cite_memories_and_meter_usage(tmp_path: Path) -> None:
    llm = FakeLLMProvider()
    with make_client(tmp_path, llm) as c:
        auth = register_and_login(c)
        a = note(c, auth, "Entrevista Acme", "Combinei enviar o portfólio até sexta para a Acme.")
        r = c.post(
            "/api/v1/brain/ask",
            json={
                "question": "O que combinei com a Acme?",
                "history": [{"role": "user", "text": "Oi"}, {"role": "brain", "text": "Olá"}],
            },
            headers=auth,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["answer"].startswith("(fake LLM) O que combinei")
        assert [s["id"] for s in body["sources"]] == [a["id"]]
        call = llm.calls[-1]
        assert call["schema_name"] == "brain_answer"
        assert "portfólio até sexta" in call["input_text"]
        assert "<conversation_so_far>" in call["input_text"]
        r = c.post("/api/v1/brain/briefing", json={"topic": "Reunião com a Acme"}, headers=auth)
        assert r.status_code == 200 and r.json()["sources"][0]["id"] == a["id"]
        assert "<topic>\nReunião com a Acme\n</topic>" in llm.calls[-1]["input_text"]


def _meeting_with_speech(c: TestClient, auth: dict[str, str], settings: dict[str, Any]) -> str:
    m = c.post("/api/v1/meetings", json={"language": "pt-BR"}, headers=auth).json()
    with c.websocket_connect(m["stream"]["stream_path"]) as ws:
        ws.send_json({"type": "auth", "token": m["stream"]["stream_token"]})
        ws.receive_json()
        ws.send_json({"type": "copilot.settings", **settings})
        for i in range(7 * FINAL_FRAMES):
            ws.send_bytes(frame(0, i))
        ws.send_json({"type": "capture.stopped"})
        try:
            while True:
                ws.receive_json()
        except WebSocketDisconnect:
            pass
    mid: str = m["meeting"]["id"]
    return mid


def test_live_answers_use_memory_and_meeting_is_remembered(tmp_path: Path) -> None:
    llm = FakeLLMProvider()
    with make_client(tmp_path, llm) as c:
        auth = register_and_login(c)
        note(c, auth, "Kubernetes na Beta", "Na Beta eu cuidei do cluster Kubernetes de produção.")
        mid = _meeting_with_speech(c, auth, {"remember": True})
        kube = [
            x
            for x in llm.calls
            if x["schema_name"] == "copilot_answer" and "Kubernetes?" in x["input_text"]
        ]
        assert kube and "cluster Kubernetes de produção" in kube[0]["input_text"]
        others = [x for x in llm.calls if x["schema_name"] == "copilot_answer"]
        assert any("<memory>" not in x["input_text"] for x in others), "only clear matches"

        r = c.post(f"/api/v1/meetings/{mid}/finish", headers=auth)
        assert r.status_code == 200 and r.json()["memory_id"]
        mem = c.get(f"/api/v1/brain/memories/{r.json()['memory_id']}", headers=auth).json()
        assert mem["status"] == "ready" and mem["kind"] == "meeting"
        assert mem["title"] == "Reunião de teste"
        assert "## Próximos passos" in mem["details"]
        assert mem["suggestions"] == ["(fake LLM) fato sobre você"]
        assert mem["meeting_id"] == mid
        # Finishing again does not create a second memory.
        assert c.post(f"/api/v1/meetings/{mid}/finish", headers=auth).json()["memory_id"] is None
        usage_kinds = [x["schema_name"] for x in llm.calls]
        assert "meeting_memory" in usage_kinds


def test_meetings_are_not_remembered_unless_asked(tmp_path: Path) -> None:
    llm = FakeLLMProvider()
    with make_client(tmp_path, llm) as c:
        auth = register_and_login(c)
        note(c, auth, "Kubernetes na Beta", "Na Beta eu cuidei do cluster Kubernetes de produção.")
        mid = _meeting_with_speech(c, auth, {"use_memory": False})
        assert all(
            "<memory>" not in x["input_text"]
            for x in llm.calls
            if x["schema_name"] == "copilot_answer"
        )
        r = c.post(f"/api/v1/meetings/{mid}/finish", headers=auth)
        assert r.json()["memory_id"] is None
        assert len(c.get("/api/v1/brain/memories", headers=auth).json()) == 1


def test_http_copilot_uses_memory_when_allowed(tmp_path: Path) -> None:
    llm = FakeLLMProvider()
    with make_client(tmp_path, llm) as c:
        auth = register_and_login(c)
        note(c, auth, "Projeto Acme", "Na Acme migrei 2 milhões de registros com Bulk API.")
        mid = c.post("/api/v1/meetings", json={"language": "pt-BR"}, headers=auth).json()[
            "meeting"
        ]["id"]
        body = {"question": "Como foi a migração com Bulk API na Acme?", "transcript": []}
        c.post(f"/api/v1/meetings/{mid}/copilot", json=body, headers=auth)
        assert "2 milhões de registros" in llm.calls[-1]["input_text"]
        c.post(f"/api/v1/meetings/{mid}/copilot", json={**body, "use_memory": False}, headers=auth)
        assert "<memory>" not in llm.calls[-1]["input_text"]


def test_shortcut_assistant_also_answers_general_questions(tmp_path: Path) -> None:
    llm = FakeLLMProvider()
    with make_client(tmp_path, llm) as c:
        auth = register_and_login(c)
        r = c.post(
            "/api/v1/brain/ask",
            json={"question": "Como bulkificar uma trigger?", "assistant": True},
            headers=auth,
        )
        assert r.status_code == 200, r.text
        call = llm.calls[-1]
        assert "personal assistant" in call["instructions"]
        assert "general questions" in call["instructions"]
        assert "<today>" in call["input_text"]
        c.post("/api/v1/brain/ask", json={"question": "Oi"}, headers=auth)
        assert "second brain" in llm.calls[-1]["instructions"]


def test_meeting_scenario_reaches_answers_and_question_checks(tmp_path: Path) -> None:
    llm = FakeLLMProvider()
    with make_client(tmp_path, llm) as c:
        auth = register_and_login(c)
        _meeting_with_speech(c, auth, {"scenario": "meeting", "auto_suggestions": "all"})
        answers = [x for x in llm.calls if x["schema_name"] == "copilot_answer"]
        assert answers and all("WORKING MEETING" in x["instructions"] for x in answers)
        checks = [x for x in llm.calls if x["schema_name"] == "question_check"]
        assert all("whole group" in x["instructions"] for x in checks)
        mid = c.post("/api/v1/meetings", json={"language": "pt-BR"}, headers=auth).json()[
            "meeting"
        ]["id"]
        c.post(f"/api/v1/meetings/{mid}/copilot", json={"question": "E agora?"}, headers=auth)
        assert "JOB INTERVIEW" in llm.calls[-1]["instructions"]
        body = {"question": "E agora?", "scenario": "meeting"}
        c.post(f"/api/v1/meetings/{mid}/copilot", json=body, headers=auth)
        assert "WORKING MEETING" in llm.calls[-1]["instructions"]


def test_rated_suggestions_become_examples(tmp_path: Path) -> None:
    from app.brain.examples import Example, pick, render_examples

    llm = FakeLLMProvider()
    with make_client(tmp_path, llm) as c:
        auth = register_and_login(c)

        def rate(qid: str, q: str, a: str, rating: int, scenario: str = "interview") -> None:
            body = {
                "question_id": qid,
                "question": q,
                "answer": a,
                "rating": rating,
                "scenario": scenario,
            }
            r = c.post("/api/v1/brain/feedback", json=body, headers=auth)
            assert r.status_code == 204, r.text

        rate("q1", "Como você lida com governor limits?", "Bulkifico tudo, curto e direto.", 1)
        rate("q2", "Fale sobre governor limits em triggers", "Boa pergunta! Então...", -1)
        rate("q3", "Qual a sua cor favorita?", "Azul.", 1, "meeting")
        rate("q2", "Fale sobre governor limits em triggers", "Boa pergunta! Então...", -1)
        listed = c.get("/api/v1/brain/feedback", headers=auth).json()
        assert {f["question_id"] for f in listed} == {"q1", "q2", "q3"}
        other = register_and_login(c, "bia@example.com")
        assert c.get("/api/v1/brain/feedback", headers=other).json() == []

        mid = c.post("/api/v1/meetings", json={"language": "pt-BR"}, headers=auth).json()[
            "meeting"
        ]["id"]
        body = {"question": "E os governor limits?", "transcript": []}
        c.post(f"/api/v1/meetings/{mid}/copilot", json=body, headers=auth)
        text = llm.calls[-1]["input_text"]
        assert "<rated_examples>" in text and "Bulkifico tudo" in text
        assert "<rejected>" in text and "Boa pergunta!" in text
        assert "Azul." not in text, "examples come from the same scenario"
        assert "16. <rated_examples>" in llm.calls[-1]["instructions"]

        rate("q1", "Como você lida com governor limits?", "x", 0)
        rate("q2", "x", "x", 0)
        c.post(f"/api/v1/meetings/{mid}/copilot", json=body, headers=auth)
        assert "<rated_examples>" not in llm.calls[-1]["input_text"]

    # Nothing related: the newest approved answers still show the User's style.
    examples = [Example("Sobre o projeto X", "Curto e direto.", 1), Example("a", "b", -1)]
    good, bad = pick(examples, "pergunta sem relação")
    assert good == [examples[0]] and bad == []
    assert render_examples([], []) is None
