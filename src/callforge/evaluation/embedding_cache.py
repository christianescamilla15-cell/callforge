"""Versioned embedding cache: real embedder vectors, replayed offline in the CI.

The production embedder (nomic-embed-text via Ollama) is not available in the CI, so the vectors of
every corpus fragment and every question are computed once with the real model and stored in
`evaluacion/rag/embeddings/<model>.json` (float16, little endian, base64). The key is
sha256(model + text): a vector computed with another model, or for an edited text, is a MISS, and a
miss raises — the hybrid store would otherwise degrade silently to keyword search and the report
would show keyword numbers under the hybrid label. `misses` lets the runner prove it never happened.
"""
from __future__ import annotations

import base64
import hashlib
import json
import random
import struct
from collections.abc import Iterable, Sequence
from pathlib import Path

from callforge.infrastructure.knowledge.embeddings import EmbeddingProvider


def cache_key(model: str, text: str) -> str:
    return hashlib.sha256(f"{model}\n{text}".encode()).hexdigest()


def encode_vector(vector: Sequence[float]) -> str:
    return base64.b64encode(struct.pack(f"<{len(vector)}e", *vector)).decode("ascii")


def decode_vector(encoded: str) -> list[float]:
    raw = base64.b64decode(encoded)
    return list(struct.unpack(f"<{len(raw) // 2}e", raw))


class EmbeddingCacheMiss(KeyError):
    """A text has no stored vector for this model (dataset edited without recomputing the cache)."""


class CachedEmbeddingProvider(EmbeddingProvider):
    name = "cache"

    def __init__(self, path: Path) -> None:
        data = json.loads(path.read_text(encoding="utf-8"))
        self.model: str = data["modelo"]
        self.engine: str = data.get("motor", "")
        self.dimensions: int = data["dimensiones"]
        self._vectors = {key: decode_vector(value) for key, value in data["vectores"].items()}
        self.calls = 0
        self.misses = 0

    def __contains__(self, text: str) -> bool:
        return cache_key(self.model, text) in self._vectors

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            self.calls += 1
            vector = self._vectors.get(cache_key(self.model, text))
            if vector is None:
                self.misses += 1
                raise EmbeddingCacheMiss(f"sin vector de {self.model} para {text[:60]!r}")
            vectors.append(vector)
        return vectors


class ShuffledEmbeddingProvider(EmbeddingProvider):
    """Negative control: each document gets ANOTHER document's vector (fixed seed); queries unchanged.

    Same vectors, same norms, same cosine machinery — only the text↔vector alignment is broken, so a
    retrieval metric that survives this is not measuring meaning.
    """

    name = "shuffled"

    def __init__(self, inner: EmbeddingProvider, documents: Iterable[str], seed: int = 7) -> None:
        self._inner = inner
        texts = list(documents)
        shuffled = texts[:]
        rng = random.Random(seed)
        # a derangement: no document keeps its own vector
        while any(a == b for a, b in zip(texts, shuffled, strict=True)):
            rng.shuffle(shuffled)
        self._swap = dict(zip(texts, shuffled, strict=True))

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self._inner.embed([self._swap.get(t, t) for t in texts])


def write_cache(path: Path, model: str, engine: str, vectors: dict[str, Sequence[float]]) -> None:
    dimensions = {len(v) for v in vectors.values()}
    if len(dimensions) != 1:
        raise ValueError(f"dimensiones mezcladas en el caché: {sorted(dimensions)}")
    payload = {
        "modelo": model,
        "motor": engine,
        "dimensiones": dimensions.pop(),
        "codificacion": "float16 little endian, base64; llave sha256(modelo + '\\n' + texto)",
        "vectores": {cache_key(model, text): encode_vector(v) for text, v in sorted(vectors.items())},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    path.write_text(text, encoding="utf-8")
