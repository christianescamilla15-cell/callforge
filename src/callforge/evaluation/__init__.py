"""Offline evaluation of the knowledge retrieval (RAG) that the support agents rely on.

Nothing here is imported by the running service: it is the measuring instrument. Retrieval and
citation metrics are deterministic (no LLM) and gate the CI; the LLM answer run and the LLM judge
are optional and only read back from a versioned snapshot. See docs/evaluacion_rag.md.
"""
