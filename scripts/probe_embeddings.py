"""Sanity probe: real nomic-embed-text cosine ranges for the similarity floor."""
import sys

sys.path.insert(0, "src")
from callforge.infrastructure.knowledge.embeddings import OllamaEmbeddingProvider  # noqa: E402
from callforge.infrastructure.knowledge.store import _cosine  # noqa: E402

e = OllamaEmbeddingProvider()
texts = [
    "Como reiniciar el modem. Desconecta el modem 60 segundos y vuelve a conectarlo.",
    "Politica de facturacion. Las facturas se emiten el dia 1 de cada mes.",
    "mi internet no funciona, el modem tiene luz roja",
    "cuanto cuesta una pizza grande",
]
v = e.embed(texts)
print(f"query-internet vs doc-modem:   {_cosine(v[2], v[0]):.3f}")
print(f"query-internet vs doc-factura: {_cosine(v[2], v[1]):.3f}")
print(f"query-pizza    vs doc-modem:   {_cosine(v[3], v[0]):.3f}")
print(f"query-pizza    vs doc-factura: {_cosine(v[3], v[1]):.3f}")
