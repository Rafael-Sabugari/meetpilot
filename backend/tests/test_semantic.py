"""Semantic search in the second brain (with a deterministic fake embedder)."""

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from app.brain.embeddings import Vector, cosine, pack, unpack
from app.brain.search import MemoryDoc, hybrid, split_passages, tokens
from app.core.config import Settings
from app.main import create_app
from app.providers.llm.fake import FakeLLMProvider
from app.providers.stt.fake import FakeSTTProvider
from tests.conftest import register_and_login

# Words that mean the same thing land on the same axis, like a real embedding model would.
CONCEPTS = [
    {"migracao", "migrar", "migrei", "importacao", "importei", "carga", "migration"},
    {"cliente", "clientes", "customer", "contas", "accounts"},
    {"incidente", "falha", "erro", "outage", "problema"},
    {"bolo", "receita", "cenoura", "chocolate"},
]


class FakeEmbedder:
    name = "fake-embedder"

    def __init__(self) -> None:
        self.calls = 0

    async def embed(self, texts: Sequence[str], *, wait: bool = True) -> list[Vector]:
        self.calls += 1
        out = []
        for t in texts:
            words = set(tokens(t))
            v = [float(len(words & c)) for c in CONCEPTS] + [0.01]
            out.append(tuple(v))
        return out


def test_pack_roundtrip_and_cosine() -> None:
    vecs = [(1.0, 0.0, 2.0), (0.5, 0.5, 0.0)]
    assert [tuple(round(x, 5) for x in v) for v in unpack(pack(vecs))] == vecs
    assert unpack(b"") == [] and pack([]) == b""
    assert round(cosine((1, 0), (1, 0)), 3) == 1.0 and cosine((1, 0), (0, 0)) == 0.0


def test_hybrid_finds_by_meaning_when_words_differ() -> None:
    import asyncio

    emb = FakeEmbedder()
    now = datetime(2026, 11, 26, tzinfo=UTC)
    raw = [
        MemoryDoc(
            "a", "note", "Projeto Northwind", now, "Importei 2 milhões de contas com Bulk API."
        ),
        MemoryDoc("b", "note", "Receita", now, "Bolo de cenoura com chocolate."),
    ]

    async def vec(d: MemoryDoc) -> MemoryDoc:
        parts = split_passages(d)
        vs = await emb.embed([f"{p.title}\n{p.text}" for p in parts])
        return MemoryDoc(d.id, d.kind, d.title, d.occurred_at, d.text, tuple(vs))

    docs = [asyncio.run(vec(d)) for d in raw]
    query = "como foi a migração?"
    [qv] = asyncio.run(emb.embed([query]))
    assert hybrid(docs, query, None) == [], "words alone find nothing"
    hits = hybrid(docs, query, qv)
    assert hits[0].passage.memory_id == "a"
    strict = hybrid(docs, query, qv, strict=True)
    assert [h.passage.memory_id for h in strict] == ["a"], "unrelated memory left out"


def test_notes_are_indexed_and_searched_by_meaning(tmp_path: Path) -> None:
    emb = FakeEmbedder()
    llm = FakeLLMProvider()
    s = Settings(
        env="test", jwt_secret="k" * 40, database_url=f"sqlite+aiosqlite:///{tmp_path / 's.db'}"
    )
    app = create_app(s, stt_provider=FakeSTTProvider(), llm_provider=llm, embedder=emb)
    with TestClient(app) as c:
        auth = register_and_login(c)
        r = c.post(
            "/api/v1/brain/memories",
            json={
                "title": "Projeto Northwind",
                "text": "Importei 2 milhões de contas com Bulk API.",
            },
            headers=auth,
        )
        note_id = r.json()["id"]
        c.post(
            "/api/v1/brain/memories",
            json={"title": "Receita", "text": "Bolo de cenoura."},
            headers=auth,
        )
        found = c.get("/api/v1/brain/memories", params={"q": "migração"}, headers=auth).json()
        assert [m["id"] for m in found] == [note_id]
        c.post("/api/v1/brain/ask", json={"question": "Como foi a migração?"}, headers=auth)
        assert "Importei 2 milhões" in llm.calls[-1]["input_text"]
        mid = c.post("/api/v1/meetings", json={"language": "pt-BR"}, headers=auth).json()[
            "meeting"
        ]["id"]
        c.post(
            f"/api/v1/meetings/{mid}/copilot",
            json={"question": "Fala da sua migração de dados", "transcript": []},
            headers=auth,
        )
        assert "Importei 2 milhões" in llm.calls[-1]["input_text"], "live answers use meaning too"
        assert c.delete(f"/api/v1/brain/memories/{note_id}", headers=auth).status_code == 204


def test_queries_never_wait_for_the_model_to_load() -> None:
    import asyncio

    from app.brain.embeddings import FastEmbedder

    async def run() -> list[Vector]:
        emb = FastEmbedder("no-such/model-for-tests")
        out = await emb.embed(["oi"], wait=False)  # returns at once, loads in background
        await asyncio.sleep(0.05)
        return out + await emb.embed(["oi"])  # loading failed -> empty, never raises

    assert asyncio.run(run()) == []
