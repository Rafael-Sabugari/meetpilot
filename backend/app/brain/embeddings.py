"""Semantic search for the second brain: local multilingual embeddings (no external API).

The model (about 220 MB, downloaded once from Hugging Face on first use) runs on the User's
own server, so memories never leave it. If it cannot be loaded, search falls back to the
word-based BM25 alone.
"""

import asyncio
import logging
import math
from collections.abc import Sequence
from typing import Protocol

log = logging.getLogger("meetpilot.brain")

DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

Vector = tuple[float, ...]


class Embedder(Protocol):
    name: str

    async def embed(self, texts: Sequence[str], *, wait: bool = True) -> list[Vector]:
        """`wait=False`: return [] at once if the model is not loaded yet (and start
        loading it), so a live answer never waits for a download."""
        ...


class FastEmbedder:
    """fastembed (ONNX) on CPU, loaded lazily in a worker thread."""

    def __init__(self, model: str = DEFAULT_MODEL, cache_dir: str | None = None) -> None:
        self.name = model
        self._cache_dir = cache_dir
        self._model: object | None = None
        self._failed = False
        self._lock = asyncio.Lock()
        self._warming: asyncio.Future[object | None] | None = None

    async def _load(self) -> object | None:
        if self._model is not None or self._failed:
            return self._model
        async with self._lock:
            if self._model is None and not self._failed:
                try:
                    from fastembed import TextEmbedding

                    self._model = await asyncio.to_thread(
                        TextEmbedding, model_name=self.name, cache_dir=self._cache_dir
                    )
                    log.info("embedding model ready", extra={"model": self.name})
                except Exception:
                    self._failed = True
                    log.warning(
                        "embedding model unavailable; using word search only", exc_info=True
                    )
        return self._model

    async def embed(self, texts: Sequence[str], *, wait: bool = True) -> list[Vector]:
        if not wait and self._model is None:
            if not self._failed and not self._lock.locked():
                task = asyncio.ensure_future(self._load())
                self._warming = task  # keep a reference while it loads
            return []
        model = await self._load()
        if model is None or not texts:
            return []

        def run() -> list[Vector]:
            out = model.embed(list(texts))  # type: ignore[attr-defined]
            return [tuple(float(x) for x in v) for v in out]

        return await asyncio.to_thread(run)


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def pack(vectors: Sequence[Vector]) -> bytes:
    """Little-endian float32, one vector after the other."""
    import struct

    if not vectors:
        return b""
    dim = len(vectors[0])
    return struct.pack(f"<I{dim * len(vectors)}f", dim, *(x for v in vectors for x in v))


def unpack(data: bytes | None) -> list[Vector]:
    import struct

    if not data or len(data) < 4:
        return []
    (dim,) = struct.unpack_from("<I", data)
    count = (len(data) - 4) // (4 * dim) if dim else 0
    flat = struct.unpack_from(f"<{dim * count}f", data, 4)
    return [tuple(flat[i * dim : (i + 1) * dim]) for i in range(count)]
