import pytest

from callforge.domain.entities import KnowledgeDocument
from callforge.infrastructure.knowledge.embeddings import EmbeddingProvider
from callforge.infrastructure.knowledge.store import (
    HybridKnowledgeStore,
    KeywordKnowledgeStore,
)


class InMemoryKnowledgeRepo:
    def __init__(self):
        self.docs = []
        self.embedding_updates = []

    def add(self, document):
        self.docs.append(document)

    def list_all(self):
        return list(self.docs)

    def update_embedding(self, document_id, embedding):
        self.embedding_updates.append(document_id)
        for doc in self.docs:
            if doc.id == document_id:
                doc.embedding = embedding


class TopicFakeEmbedder(EmbeddingProvider):
    """Deterministic embedder: dim0 = connectivity topic, dim1 = billing topic."""

    name = "fake"

    TOPICS = (
        ("internet", "modem", "router", "conexion", "red", "luz", "conectividad"),
        ("factura", "facturacion", "pago", "cobro", "recargo"),
    )

    def embed(self, texts):
        vectors = []
        for text in texts:
            lowered = text.lower()
            vectors.append(
                [
                    float(sum(1 for w in topic if w in lowered)) or 0.01
                    for topic in self.TOPICS
                ]
            )
        return vectors


class BrokenEmbedder(EmbeddingProvider):
    name = "broken"

    def embed(self, texts):
        raise ConnectionError("embedder down")


def _store_with_docs() -> KeywordKnowledgeStore:
    repo = InMemoryKnowledgeRepo()
    store = KeywordKnowledgeStore(repo)
    store.add(
        KnowledgeDocument(
            title="Como reiniciar el modem",
            content="Desconecta el modem 60 segundos, vuelve a conectarlo y espera a que las luces queden fijas.",
            tags=["internet", "modem"],
        )
    )
    store.add(
        KnowledgeDocument(
            title="Politica de facturacion",
            content="Las facturas se emiten el dia 1 de cada mes y se pueden descargar del portal.",
            tags=["billing"],
        )
    )
    return store


def test_retrieval_ranks_relevant_document_first():
    store = _store_with_docs()
    results = store.search("no tengo internet, creo que debo reiniciar el modem")
    assert results
    assert results[0].title == "Como reiniciar el modem"


def test_retrieval_handles_accents():
    store = _store_with_docs()
    results = store.search("¿Cómo descargo mi facturación del mes?")
    assert results
    assert results[0].title == "Politica de facturacion"


def test_retrieval_returns_empty_for_unrelated_query():
    store = _store_with_docs()
    assert store.search("xyzzy quux plugh") == []


def test_hybrid_store_ranks_by_meaning_without_shared_words():
    repo = InMemoryKnowledgeRepo()
    store = HybridKnowledgeStore(repo, embedder=TopicFakeEmbedder(), min_similarity=0.3)
    store.add(
        KnowledgeDocument(title="Conectividad", content="Revisar luz del router y la red")
    )
    store.add(
        KnowledgeDocument(title="Pagos", content="El cobro y recargo de tu factura")
    )
    # Query shares NO tokens with the connectivity doc after stopword/accent
    # handling that keyword search relies on ("modem" only in query).
    results = store.search("mi modem de internet no tiene conexion")
    assert results
    assert results[0].title == "Conectividad"


def test_hybrid_store_falls_back_to_keyword_when_embedder_fails():
    repo = InMemoryKnowledgeRepo()
    store = HybridKnowledgeStore(repo, embedder=BrokenEmbedder())
    # add() must not raise even with a broken embedder
    store.add(
        KnowledgeDocument(
            title="Como reiniciar el modem",
            content="Desconecta el modem 60 segundos y vuelve a conectarlo.",
        )
    )
    results = store.search("como reinicio el modem")
    assert results
    assert results[0].title == "Como reiniciar el modem"


def test_hybrid_store_backfills_missing_embeddings():
    repo = InMemoryKnowledgeRepo()
    # Document ingested pre-embeddings (straight through the repo)
    repo.add(KnowledgeDocument(title="Conectividad", content="luz del router"))
    store = HybridKnowledgeStore(repo, embedder=TopicFakeEmbedder(), min_similarity=0.3)

    results = store.search("internet caido, modem sin luz")
    assert results
    assert repo.embedding_updates  # backfill persisted
    assert repo.docs[0].embedding is not None


def test_hybrid_store_filters_below_min_similarity():
    repo = InMemoryKnowledgeRepo()
    store = HybridKnowledgeStore(repo, embedder=TopicFakeEmbedder(), min_similarity=0.9)
    store.add(KnowledgeDocument(title="Pagos", content="factura y recargo"))
    # Connectivity query vs billing doc -> near-orthogonal vectors, filtered out
    assert store.search("modem internet sin conexion") == []


@pytest.mark.parametrize("embedder", [None])
def test_hybrid_store_without_embedder_uses_keyword(embedder):
    repo = InMemoryKnowledgeRepo()
    store = HybridKnowledgeStore(repo, embedder=embedder)
    store.add(
        KnowledgeDocument(
            title="Politica de facturacion",
            content="Las facturas se emiten el dia 1 de cada mes.",
        )
    )
    results = store.search("politica de facturacion")
    assert results
    assert results[0].title == "Politica de facturacion"


def test_knowledge_endpoint_creates_document(client):
    response = client.post(
        "/api/v1/knowledge/documents",
        json={
            "title": "Horario de soporte",
            "content": "El soporte atiende de 9 a 18 horas de lunes a viernes.",
            "category": "general",
            "tags": ["horario"],
        },
    )
    assert response.status_code == 201
    assert response.json()["id"]
