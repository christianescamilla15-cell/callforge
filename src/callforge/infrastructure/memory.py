"""Companion long-term memory: durable facts about the person, recalled
across sessions by meaning. Recall uses local embeddings (cosine) with a
recent-fallback; writing embeds each fact. Cheap per turn — only the
periodic extraction (in the workflow) costs an LLM call."""
from __future__ import annotations

import logging

from callforge.domain.entities import CompanionMemory
from callforge.domain.repositories import MemoryRepository
from callforge.infrastructure.knowledge.embeddings import EmbeddingProvider
from callforge.infrastructure.knowledge.store import _cosine

logger = logging.getLogger(__name__)

# Facts more similar than this (cosine) are treated as the same fact and not
# re-stored, so the memory does not bloat with near-duplicates over time.
_DEDUP_THRESHOLD = 0.92


class MemoryStore:
    def __init__(
        self,
        repository: MemoryRepository,
        embedder: EmbeddingProvider | None = None,
    ) -> None:
        self._repo = repository
        self._embedder = embedder

    def count(self) -> int:
        return self._repo.count()

    def recent_contents(self, limit: int = 40) -> list[str]:
        """The known facts (most recent), for grounding extraction so the model
        only returns what's new. Cheap: no embedding, just a list."""
        return [m.content for m in self._repo.list_all()[-limit:]]

    def recall(self, query: str, k: int = 5) -> list[str]:
        """Most relevant memories for the current message, plus the newest one
        so the companion always has fresh context."""
        memories = self._repo.list_all()
        if not memories:
            return []
        newest = memories[-1].content
        if self._embedder is None:
            return [m.content for m in memories[-k:]]
        try:
            qv = self._embedder.embed([query])[0]
        except Exception as exc:  # noqa: BLE001 - degrade to recent
            logger.warning("Memory recall embed failed (%s); using recent", exc)
            return [m.content for m in memories[-k:]]

        scored = [
            (_cosine(qv, m.embedding), m.content) for m in memories if m.embedding
        ]
        scored.sort(key=lambda p: p[0], reverse=True)
        top = [c for _, c in scored[:k]]
        if newest not in top:  # always keep fresh context, without dropping relevance
            top.append(newest)
        return top

    def remember(self, facts: list[str], conversation_id: str | None = None) -> int:
        facts = [f.strip() for f in facts if f.strip() and f.strip().upper() != "NADA"]
        if not facts:
            return 0
        vectors = None
        if self._embedder is not None:
            try:
                vectors = self._embedder.embed(facts)
            except Exception as exc:  # noqa: BLE001 - store without embeddings
                logger.warning("Memory embed failed (%s); storing plain", exc)
        # Existing fact vectors, so we can skip near-duplicates. We also grow
        # this list as we add, to dedup within the same batch.
        known_vecs = [m.embedding for m in self._repo.list_all() if m.embedding]
        added = 0
        for i, fact in enumerate(facts):
            vec = vectors[i] if vectors else None
            if vec and known_vecs:
                if max(_cosine(vec, kv) for kv in known_vecs) >= _DEDUP_THRESHOLD:
                    logger.info("Skip duplicate memory: %s", fact)
                    continue
            self._repo.add(
                CompanionMemory(
                    content=fact,
                    embedding=vec,
                    source_conversation_id=conversation_id,
                )
            )
            if vec:
                known_vecs.append(vec)
            added += 1
        return added
