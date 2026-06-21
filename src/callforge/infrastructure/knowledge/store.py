"""Knowledge retrieval behind a single interface: search() -> ranked snippets.

Two implementations:
- KeywordKnowledgeStore: zero-dependency accent-stripped token overlap.
- HybridKnowledgeStore: local embeddings (cosine) with lazy backfill for
  documents ingested before embeddings existed, falling back to keyword
  search whenever the embedder is unavailable.
"""
from __future__ import annotations

import logging
import math
import re
import unicodedata
from dataclasses import dataclass

from callforge.domain.entities import KnowledgeDocument
from callforge.domain.repositories import KnowledgeRepository
from callforge.infrastructure.knowledge.embeddings import EmbeddingProvider

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = {
    "de", "la", "el", "los", "las", "un", "una", "que", "con", "por", "para",
    "mi", "tu", "su", "es", "en", "y", "o", "a", "no", "se", "me", "te", "lo",
    "the", "a", "an", "is", "are", "to", "of", "and", "or", "my", "your", "it",
    "in", "on", "for", "i", "do", "does", "not",
}


def _normalize(text: str) -> list[str]:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return [t for t in _TOKEN_RE.findall(text) if t not in _STOPWORDS]


@dataclass
class KnowledgeSnippet:
    document_id: str
    title: str
    content: str
    score: float


class KeywordKnowledgeStore:
    def __init__(self, repository: KnowledgeRepository) -> None:
        self._repo = repository

    def add(self, document: KnowledgeDocument) -> None:
        self._repo.add(document)

    def search(self, query: str, top_k: int = 3) -> list[KnowledgeSnippet]:
        query_terms = set(_normalize(query))
        if not query_terms:
            return []

        scored: list[KnowledgeSnippet] = []
        for doc in self._repo.list_all():
            doc_terms = _normalize(f"{doc.title} {doc.content} {' '.join(doc.tags)}")
            if not doc_terms:
                continue
            overlap = sum(1 for t in doc_terms if t in query_terms)
            if overlap == 0:
                continue
            score = overlap / math.sqrt(len(doc_terms))
            scored.append(
                KnowledgeSnippet(
                    document_id=doc.id,
                    title=doc.title,
                    content=doc.content,
                    score=round(score, 4),
                )
            )
        scored.sort(key=lambda s: s.score, reverse=True)
        return scored[:top_k]


def _embedding_text(doc: KnowledgeDocument) -> str:
    return f"{doc.title}\n{doc.content}"


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class HybridKnowledgeStore:
    """Embedding search with keyword fallback. Same interface as the keyword store."""

    def __init__(
        self,
        repository: KnowledgeRepository,
        embedder: EmbeddingProvider | None = None,
        min_similarity: float = 0.55,
    ) -> None:
        self._repo = repository
        self._embedder = embedder
        self._keyword = KeywordKnowledgeStore(repository)
        self._min_similarity = min_similarity

    def add(self, document: KnowledgeDocument) -> None:
        if self._embedder is not None:
            try:
                document.embedding = self._embedder.embed([_embedding_text(document)])[0]
            except Exception as exc:  # noqa: BLE001 - ingest must not fail on embedder
                logger.warning("Embedding at ingest failed (%s); stored without it", exc)
        self._repo.add(document)

    def search(self, query: str, top_k: int = 3) -> list[KnowledgeSnippet]:
        if self._embedder is None:
            return self._keyword.search(query, top_k)
        try:
            return self._vector_search(query, top_k)
        except Exception as exc:  # noqa: BLE001 - degrade, never break the workflow
            logger.warning("Vector search failed (%s); keyword fallback", exc)
            return self._keyword.search(query, top_k)

    def _vector_search(self, query: str, top_k: int) -> list[KnowledgeSnippet]:
        documents = self._repo.list_all()
        if not documents:
            return []

        # Lazy backfill: embed (and persist) documents ingested before
        # embeddings existed, so old knowledge participates in ranking.
        missing = [d for d in documents if not d.embedding]
        if missing:
            vectors = self._embedder.embed([_embedding_text(d) for d in missing])
            for doc, vector in zip(missing, vectors):
                doc.embedding = vector
                self._repo.update_embedding(doc.id, vector)

        query_vector = self._embedder.embed([query])[0]
        scored = sorted(
            (
                (_cosine(query_vector, d.embedding), d)
                for d in documents
                if d.embedding
            ),
            key=lambda pair: pair[0],
            reverse=True,
        )
        return [
            KnowledgeSnippet(
                document_id=d.id, title=d.title, content=d.content, score=round(s, 4)
            )
            for s, d in scored[:top_k]
            if s >= self._min_similarity
        ]
