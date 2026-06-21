from callforge.domain.entities import CompanionMemory
from callforge.infrastructure.knowledge.embeddings import EmbeddingProvider
from callforge.infrastructure.memory import MemoryStore


class InMemoryMemoryRepo:
    def __init__(self):
        self.items = []

    def add(self, memory):
        self.items.append(memory)

    def list_all(self):
        return list(self.items)

    def count(self):
        return len(self.items)


class TopicEmbedder(EmbeddingProvider):
    """dim0 = música, dim1 = familia."""

    name = "fake"
    TOPICS = (("música", "rock", "metal", "banda"), ("mamá", "familia", "hermano", "papá"))

    def embed(self, texts):
        return [
            [float(sum(1 for w in t if w in x.lower())) or 0.01 for t in self.TOPICS]
            for x in texts
        ]


def test_remember_and_recall_by_meaning():
    repo = InMemoryMemoryRepo()
    store = MemoryStore(repo, embedder=TopicEmbedder())
    store.remember(["Le gusta el rock y el metal", "Tiene un hermano menor"], "c1")
    assert repo.count() == 2

    # Query about music -> recalls the music memory first
    recalled = store.recall("oye, qué banda me recomiendas", k=1)
    assert any("rock" in r for r in recalled)


def test_remember_filters_nada_and_blanks():
    repo = InMemoryMemoryRepo()
    store = MemoryStore(repo)
    n = store.remember(["NADA", "  ", "Se llama Cris"], "c1")
    assert n == 1
    assert repo.items[0].content == "Se llama Cris"


def test_remember_dedups_near_duplicates():
    repo = InMemoryMemoryRepo()
    store = MemoryStore(repo, embedder=TopicEmbedder())
    store.remember(["Le gusta el rock y el metal"], "c1")
    # The same fact again has an identical embedding -> skipped as duplicate.
    added = store.remember(["Le gusta el rock y el metal"], "c2")
    assert added == 0
    assert repo.count() == 1


def test_recent_contents_returns_recent_known_facts():
    repo = InMemoryMemoryRepo()
    for i in range(5):
        repo.add(CompanionMemory(content=f"dato {i}"))
    store = MemoryStore(repo)
    assert store.recent_contents(limit=2) == ["dato 3", "dato 4"]


def test_recall_without_embedder_returns_recent():
    repo = InMemoryMemoryRepo()
    for i in range(8):
        repo.add(CompanionMemory(content=f"dato {i}"))
    store = MemoryStore(repo, embedder=None)
    recalled = store.recall("lo que sea", k=3)
    assert recalled == ["dato 5", "dato 6", "dato 7"]


def test_companion_memory_persists_via_migration(tmp_path):
    from sqlalchemy import inspect

    from callforge.infrastructure.database import build_engine, run_migrations

    engine = build_engine(f"sqlite:///{tmp_path / 'mem.db'}")
    run_migrations(engine)
    assert "companion_memories" in inspect(engine).get_table_names()
