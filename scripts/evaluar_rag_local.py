"""Produce the RAG evaluation inputs that need a model, in-process with llama.cpp (no Ollama, no server).

Manual (never in the CI): needs `pip install llama-cpp-python` and two GGUF files.

    PYTHONPATH=src python scripts/evaluar_rag_local.py embeddings --gguf nomic-embed-text-v1.5.f16.gguf
    PYTHONPATH=src python scripts/evaluar_rag_local.py llm --gguf qwen2.5-1.5b-instruct-q4_k_m.gguf \\
        --modelo qwen2.5-1.5b-instruct-q4_k_m --juez

`embeddings` rewrites evaluacion/rag/embeddings/nomic-embed-text.json with the same model Ollama serves as
`nomic-embed-text` (nomic-embed-text-v1.5, f16 GGUF). `llm` answers every question with the production
SupportAgent prompt over the hybrid retriever and, with --juez, asks the same small model for a per-claim
grounding verdict; the snapshot lands in evaluacion/rag/respuestas/. With Ollama available, the HTTP
subcommands of `python -m callforge.evaluation.rag` do the same against the real server.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import time
from importlib.metadata import version

from llama_cpp import Llama

from callforge.evaluation.rag import CANDIDATES, compute_embeddings, produce_snapshot
from callforge.infrastructure.knowledge.embeddings import EmbeddingProvider
from callforge.infrastructure.llm.base import LLMProvider, LLMResult

THREADS = max(1, min(6, (os.cpu_count() or 2) // 2))  # leave room for whatever else runs on the machine


class LlamaCppEmbeddings(EmbeddingProvider):
    name = "llama.cpp"

    def __init__(self, gguf: str) -> None:
        self._llm = Llama(model_path=gguf, embedding=True, n_ctx=2048, n_batch=2048, n_ubatch=2048,
                          n_threads=THREADS, verbose=False)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [list(map(float, self._llm.embed(text, normalize=True))) for text in texts]


class LlamaCppProvider(LLMProvider):
    """Chat completion with the GGUF's own chat template; JSON-constrained like json_mode in production."""

    name = "llama.cpp"

    def __init__(self, gguf: str, model: str) -> None:
        self._llm = Llama(model_path=gguf, n_ctx=4096, n_threads=THREADS, seed=7, verbose=False)
        self._model = model

    async def complete(self, system, messages, json_mode=False) -> LLMResult:
        started = time.perf_counter()
        response = self._llm.create_chat_completion(
            messages=[{"role": "system", "content": system}, *messages],
            temperature=0.0, max_tokens=384, seed=7,
            response_format={"type": "json_object"} if json_mode else None,
        )
        usage = response.get("usage", {})
        return LLMResult(text=response["choices"][0]["message"]["content"] or "", provider=self.name,
                         model=self._model, tokens_in=usage.get("prompt_tokens", 0),
                         tokens_out=usage.get("completion_tokens", 0),
                         latency_ms=int((time.perf_counter() - started) * 1000))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="comando", required=True)
    emb = sub.add_parser("embeddings")
    emb.add_argument("--gguf", required=True)
    emb.add_argument("--candidato", help="embedder candidato (p. ej. bge-m3): se mide como referencia en "
                                          "evaluacion/rag/embeddings/candidatos/, sin compuerta")
    llm = sub.add_parser("llm")
    llm.add_argument("--gguf", required=True)
    llm.add_argument("--modelo", required=True)
    llm.add_argument("--juez", action="store_true")
    args = parser.parse_args()
    engine = (f"llama-cpp-python {version('llama-cpp-python')} (CPU, en proceso) · "
              f"{os.path.basename(args.gguf)}")
    if args.comando == "embeddings":
        if args.candidato:
            print(compute_embeddings(LlamaCppEmbeddings(args.gguf), engine, model=args.candidato,
                                     path=CANDIDATES / f"{args.candidato}.json"))
        else:
            print(compute_embeddings(LlamaCppEmbeddings(args.gguf), engine))
    else:
        provider = LlamaCppProvider(args.gguf, args.modelo)
        print(asyncio.run(produce_snapshot(provider, args.modelo, engine + " · temperatura 0", args.juez)))


if __name__ == "__main__":
    main()
