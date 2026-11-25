"""RAG evaluation runner, CI gate and report.

    PYTHONPATH=src python -m callforge.evaluation.rag                    # measure and print
    PYTHONPATH=src python -m callforge.evaluation.rag --compuerta        # exit 1 under the thresholds
    PYTHONPATH=src python -m callforge.evaluation.rag --sabotaje embeddings_barajados --compuerta  # must fail
    PYTHONPATH=src python -m callforge.evaluation.rag --informe          # rewrite the block in the report
    PYTHONPATH=src python -m callforge.evaluation.rag --revisar-informe  # exit 1 if the report is stale
    python -m callforge.evaluation.rag embeddings --url URL --api openai|ollama   # recompute the cache
    python -m callforge.evaluation.rag llm --url URL --api openai|ollama --modelo M [--juez]  # snapshot

Everything the gate reads is deterministic: the keyword store, the hybrid store over the versioned
embedding cache (real nomic-embed-text vectors), the extractive baseline and the stored LLM snapshot.
Top-k and the similarity floor come from the production defaults in `Settings`.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean, median

import httpx

from callforge.config import Settings
from callforge.domain.entities import KnowledgeDocument
from callforge.domain.repositories import KnowledgeRepository
from callforge.evaluation.answers import AgentAnswerer, Answer, ExtractiveAnswerer, LLMJudge
from callforge.evaluation.dataset import DATA_DIR, EvalSet, load_eval_set, validate
from callforge.evaluation.embedding_cache import (
    CachedEmbeddingProvider,
    ShuffledEmbeddingProvider,
    write_cache,
)
from callforge.evaluation.metrics import (
    citation_coverage,
    content_stems,
    hit_at_k,
    key_fact_recall,
    reciprocal_rank,
    token_f1,
    verify_answer,
)
from callforge.infrastructure.knowledge.embeddings import EmbeddingProvider, OllamaEmbeddingProvider
from callforge.infrastructure.knowledge.store import (
    HybridKnowledgeStore,
    KeywordKnowledgeStore,
    KnowledgeSnippet,
)

ROOT = Path(__file__).resolve().parents[3]
REPORT = ROOT / "docs" / "evaluacion_rag.md"
GATE = DATA_DIR / "compuerta.json"
SNAPSHOTS = DATA_DIR / "respuestas"

_DEFAULTS = Settings.model_fields
TOP_K: int = _DEFAULTS["knowledge_top_k"].default
MIN_SIMILARITY: float = _DEFAULTS["knowledge_min_similarity"].default
EMBEDDING_MODEL: str = _DEFAULTS["embedding_model"].default
EMBEDDINGS = DATA_DIR / "embeddings" / f"{EMBEDDING_MODEL}.json"
CANDIDATES = DATA_DIR / "embeddings" / "candidatos"  # other embedders, measured for reference (not gated)
DEPTH = 10  # MRR looks this deep

SABOTAGES = ("embeddings_barajados", "recuperador_azar", "respuestas_alteradas")
SABOTAGE_LABELS = {"embeddings_barajados": "embeddings barajados", "recuperador_azar": "recuperador al azar",
                   "respuestas_alteradas": "cifras alteradas"}
KIND_LABELS = {"literal": "literales", "parafrasis": "paráfrasis", "multi": "multi",
               "fuera_de_dominio": "fuera de dominio"}
BEGIN, END = "<!-- metricas:inicio -->", "<!-- metricas:fin -->"
Answers = dict[str, tuple[Answer, list[str]]]  # question id -> (answer, ids of the fragments shown)


class InMemoryKnowledgeRepository(KnowledgeRepository):
    def __init__(self) -> None:
        self._documents: list[KnowledgeDocument] = []

    def add(self, document: KnowledgeDocument) -> None:
        self._documents.append(document)

    def list_all(self) -> list[KnowledgeDocument]:
        return list(self._documents)

    def update_embedding(self, document_id: str, embedding: list[float]) -> None:
        for document in self._documents:
            if document.id == document_id:
                document.embedding = embedding


class RandomRetriever:
    """Negative control: k documents at random (seeded by the query, so the run is reproducible)."""

    def __init__(self, eval_set: EvalSet, seed: int = 7) -> None:
        self._documents = eval_set.documents
        self._seed = seed

    def search(self, query: str, top_k: int = 3) -> list[KnowledgeSnippet]:
        rng = random.Random(f"{self._seed}:{query}")
        sample = rng.sample(self._documents, min(top_k, len(self._documents)))
        return [KnowledgeSnippet(document_id=d.id, title=d.title, content=d.content, score=0.0)
                for d in sample]


class FusionRetriever:
    """Reference only: reciprocal rank fusion (k=60) of the keyword and embedding rankings."""

    def __init__(self, *retrievers, k: int = 60, depth: int = 40) -> None:
        self._retrievers, self._k, self._depth = retrievers, k, depth

    def search(self, query: str, top_k: int = 3) -> list[KnowledgeSnippet]:
        scores: dict[str, float] = {}
        snippets: dict[str, KnowledgeSnippet] = {}
        for retriever in self._retrievers:
            for position, snippet in enumerate(retriever.search(query, top_k=self._depth), start=1):
                scores[snippet.document_id] = scores.get(snippet.document_id, 0.0) + 1 / (self._k + position)
                snippets.setdefault(snippet.document_id, snippet)
        ranked = sorted(scores, key=lambda d: (-scores[d], d))
        return [snippets[d] for d in ranked[:top_k]]


def build_store(eval_set: EvalSet, embedder: EmbeddingProvider | None = None,
                min_similarity: float = MIN_SIMILARITY):
    """The production stores loaded with the corpus: keyword without embedder, hybrid with one."""
    repository = InMemoryKnowledgeRepository()
    store = (HybridKnowledgeStore(repository, embedder=embedder, min_similarity=min_similarity)
             if embedder is not None else KeywordKnowledgeStore(repository))
    for d in eval_set.documents:
        store.add(KnowledgeDocument(id=d.id, title=d.title, content=d.content, tags=list(d.tags)))
    return store


def _overlap_by_kind(eval_set: EvalSet) -> dict[str, float]:
    """Mean content stems a question shares with its sources: how «literal» each kind really is."""
    overlaps: dict[str, list[int]] = {}
    for q in eval_set.questions:
        if q.answerable:
            source = set(content_stems(" ".join(eval_set.document(s).fragment for s in q.sources)))
            overlaps.setdefault(q.kind, []).append(len(set(content_stems(q.text)) & source))
    return {kind: round(mean(values), 2) for kind, values in overlaps.items()}


def _ratio(values: Sequence[float]) -> float:
    return round(mean(values), 4) if values else 0.0


# --------------------------------------------------------------------------- #
# Retrieval
# --------------------------------------------------------------------------- #


def evaluate_retrieval(store, eval_set: EvalSet, k: int = TOP_K) -> dict:
    rows, top_scores = [], {True: [], False: []}
    for q in eval_set.questions:
        snippets = store.search(q.text, top_k=DEPTH)
        rows.append((q, [s.document_id for s in snippets]))
        if snippets:
            top_scores[q.answerable].append(snippets[0].score)
    answerable = [(q, r) for q, r in rows if q.answerable]
    out_of_domain = [r for q, r in rows if not q.answerable]
    summary = {
        "hit@1": _ratio([hit_at_k(r, q.sources, 1) for q, r in answerable]),
        f"hit@{k}": _ratio([hit_at_k(r, q.sources, k) for q, r in answerable]),
        "hit@5": _ratio([hit_at_k(r, q.sources, 5) for q, r in answerable]),
        "mrr": _ratio([reciprocal_rank(r, q.sources) for q, r in answerable]),
        f"cobertura_citas@{k}": _ratio([citation_coverage(r, q.sources, k) for q, r in answerable]),
        "sin_resultados": _ratio([float(not r) for _, r in answerable]),
        "abstencion_fuera_de_dominio": _ratio([float(not r[:k]) for r in out_of_domain]),
    }
    for kind in ("literal", "parafrasis", "multi"):
        of_kind = [(q, r) for q, r in answerable if q.kind == kind]
        summary[f"hit@{k}.{kind}"] = _ratio([hit_at_k(r, q.sources, k) for q, r in of_kind])
    misses = sorted(q.id for q, r in answerable if not hit_at_k(r, q.sources, k))
    # Where a similarity floor could cut: the best score of each question, answerable vs. out of domain.
    separation = {"top1_con_respuesta_min": min(top_scores[True], default=0.0),
                  "top1_con_respuesta_mediana": median(top_scores[True]) if top_scores[True] else 0.0,
                  "top1_fuera_de_dominio_max": max(top_scores[False], default=0.0)}
    return {"resumen": summary, "fallos_top_k": misses,
            "separacion": {name: round(value, 3) for name, value in separation.items()}}


# --------------------------------------------------------------------------- #
# Answers
# --------------------------------------------------------------------------- #


def evaluate_answers(eval_set: EvalSet, answers: Answers) -> dict:
    """`answers`: question id -> (answer, ids of the fragments shown, in the order the agent saw them)."""
    faithfulness, fully_faithful, cited_ok, expected_cited, facts, f1 = [], [], [], [], [], []
    ood_abstained, wrongly_abstained, phantoms, errors = [], [], 0, 0
    for q in eval_set.questions:
        answer, shown = answers[q.id]
        if answer.error:
            errors += 1
        check = verify_answer(answer.reply, answer.citations, [eval_set.document(d).fragment for d in shown])
        phantoms += len(check.phantom_citations)
        if not q.answerable:
            ood_abstained.append(float(check.abstained))
            continue
        wrongly_abstained.append(float(check.abstained))
        if check.faithfulness is not None:
            faithfulness.append(check.faithfulness)
            fully_faithful.append(float(check.faithfulness == 1.0))
        valid = [c for c in check.cited if c not in check.phantom_citations]
        if not check.abstained:
            cited_ok.append(float(bool(valid)))
        expected_cited.append(float(any(shown[c - 1] in q.sources for c in valid)))
        facts.append(key_fact_recall(answer.reply, q.key_facts))
        f1.append(token_f1(answer.reply, q.reference))
    return {"resumen": {
        "fidelidad": _ratio(faithfulness),
        "respuestas_fieles": _ratio(fully_faithful),
        "con_cita_valida": _ratio(cited_ok),
        "cita_fuente_esperada": _ratio(expected_cited),
        "hechos_clave": _ratio(facts),
        "f1_referencia": _ratio(f1),
        "abstencion_fuera_de_dominio": _ratio(ood_abstained),
        "abstencion_indebida": _ratio(wrongly_abstained),
        "citas_fantasma": phantoms,
        "errores": errors,
    }}


def _alter_numbers(answer: Answer) -> Answer:
    """Sabotage: every number in the reply moves by one (60 → 61): the kind of slip a model makes."""
    return Answer(reply=re.sub(r"\d+", lambda m: str(int(m.group()) + 1), answer.reply),
                  citations=answer.citations)


def extractive_answers(eval_set: EvalSet, store, k: int = TOP_K,
                       tamper: Callable[[Answer], Answer] | None = None) -> Answers:
    answerer, answers = ExtractiveAnswerer(), {}
    for q in eval_set.questions:
        snippets = store.search(q.text, top_k=k)
        answer = answerer.answer(q.text, snippets)
        answers[q.id] = (tamper(answer) if tamper else answer, [s.document_id for s in snippets])
    return answers


def load_snapshot(path: Path) -> tuple[dict, dict[str, tuple[Answer, list[str]]]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    answers = {r["id"]: (Answer(reply=r["reply"], citations=list(r["citations"]), raw=r.get("raw", ""),
                                error=r.get("error")), list(r["mostrados"]))
               for r in data["respuestas"]}
    return data, answers


def judge_summary(eval_set: EvalSet, data: dict) -> dict | None:
    """Judge faithfulness and its agreement with the deterministic verifier (claims matched by text)."""
    per_answer, agree, compared = [], 0, 0
    for record in data["respuestas"]:
        judged = (record.get("juez") or {}).get("afirmaciones")
        if not judged:
            continue
        per_answer.append(mean(float(c["respaldada"]) for c in judged))
        check = verify_answer(record["reply"], record["citations"],
                              [eval_set.document(d).fragment for d in record["mostrados"]])
        deterministic = {c.text: c.supported for c in check.claims}
        for claim in judged:
            if claim["texto"] in deterministic:
                compared += 1
                agree += deterministic[claim["texto"]] == claim["respaldada"]
    if not per_answer:
        return None
    return {"fidelidad_juez": _ratio(per_answer), "acuerdo_con_verificador": round(agree / compared, 4)
            if compared else 0.0, "afirmaciones_comparadas": compared, "respuestas_juzgadas": len(per_answer)}


# --------------------------------------------------------------------------- #
# Whole run, gate and report
# --------------------------------------------------------------------------- #


def run_evaluation(eval_set: EvalSet | None = None, embedder: CachedEmbeddingProvider | None = None,
                   sabotage: str | None = None, snapshots: Sequence[Path] | None = None) -> dict:
    eval_set = eval_set or load_eval_set()
    errors = validate(eval_set)
    if errors:
        raise ValueError("conjunto de evaluación inconsistente: " + "; ".join(errors))
    if sabotage is not None and sabotage not in SABOTAGES:
        raise ValueError(f"sabotaje desconocido {sabotage!r}; opciones: {SABOTAGES}")
    embedder = embedder or CachedEmbeddingProvider(EMBEDDINGS)

    hybrid_embedder: EmbeddingProvider = embedder
    if sabotage == "embeddings_barajados":
        hybrid_embedder = ShuffledEmbeddingProvider(embedder, [d.fragment for d in eval_set.documents])
    keyword = build_store(eval_set)
    hybrid = build_store(eval_set, hybrid_embedder)
    if sabotage == "recuperador_azar":
        keyword = hybrid = RandomRetriever(eval_set)
    tamper = _alter_numbers if sabotage == "respuestas_alteradas" else None

    results: dict = {
        "conjunto": {"version": eval_set.version, "documentos": len(eval_set.documents),
                     "preguntas": len(eval_set.questions),
                     "por_tipo": {k: sum(q.kind == k for q in eval_set.questions)
                                  for k in ("literal", "parafrasis", "multi", "fuera_de_dominio")},
                     "traslape_con_la_fuente": _overlap_by_kind(eval_set)},
        "parametros": {"top_k": TOP_K, "piso_similitud": MIN_SIMILARITY, "modelo_embeddings": embedder.model,
                       "motor_embeddings": embedder.engine, "sabotaje": sabotage},
        "recuperacion": {
            "palabras": evaluate_retrieval(keyword, eval_set),
            "hibrido": evaluate_retrieval(hybrid, eval_set),
        },
        "respuestas": {
            "extractivo_hibrido": evaluate_answers(
                eval_set, extractive_answers(eval_set, hybrid, tamper=tamper)),
            "extractivo_palabras": evaluate_answers(
                eval_set, extractive_answers(eval_set, keyword, tamper=tamper)),
        },
    }
    if sabotage is None:  # context for the report: what the floor buys, and what another embedder buys
        no_floor = build_store(eval_set, embedder, min_similarity=-1.0)
        results["recuperacion"]["hibrido_sin_piso"] = evaluate_retrieval(no_floor, eval_set)
        fusion = FusionRetriever(keyword, no_floor)
        results["recuperacion"]["fusion_rrf"] = evaluate_retrieval(fusion, eval_set)
        for path in sorted(CANDIDATES.glob("*.json")):
            candidate = CachedEmbeddingProvider(path)
            for label, floor in (("sin_piso", -1.0), ("piso", MIN_SIMILARITY)):
                entry = evaluate_retrieval(build_store(eval_set, candidate, min_similarity=floor), eval_set)
                entry["modelo"] = candidate.model
                results["recuperacion"][f"candidato_{candidate.model}_{label}"] = entry
            if candidate.misses:
                raise ValueError(f"el caché candidato {path.name} no cubre el conjunto")
    for path in sorted(SNAPSHOTS.glob("*.json")) if snapshots is None else snapshots:
        data, answers = load_snapshot(path)
        if tamper:
            answers = {qid: (tamper(a), shown) for qid, (a, shown) in answers.items()}
        entry = evaluate_answers(eval_set, answers)
        entry["modelo"] = data["modelo"]
        if (judge := judge_summary(eval_set, data)) is not None:
            entry["juez"] = judge
        results["respuestas"][f"llm_{path.stem}"] = entry
    # proof that the hybrid numbers are embedding numbers (a cache miss degrades to keyword silently)
    results["embeddings"] = {"llamadas": embedder.calls, "fallos_de_cache": embedder.misses}
    return results


def _metric(results: dict, reference: str) -> float:
    """«recuperacion.hibrido hit@3» -> its value."""
    section, metric = reference.split(" ")
    group, name = section.split(".", 1)
    return results[group][name]["resumen"][metric]


def gate_failures(results: dict, gate: dict | None = None) -> list[str]:
    gate = gate or json.loads(GATE.read_text(encoding="utf-8"))
    failures = []
    for relation in gate.get("relaciones", []):  # e.g. the embeddings must add value over keyword search
        better, worse = relation["mayor_o_igual"], relation["que"]
        high, low = _metric(results, better), _metric(results, worse)
        if high < low:
            failures.append(f"{better} ({high:.4f}) < {worse} ({low:.4f})")
    if results["embeddings"]["llamadas"] == 0 or results["embeddings"]["fallos_de_cache"]:
        failures.append(f"embeddings: {results['embeddings']} (el híbrido no usó el caché completo)")
    for section, thresholds in gate["umbrales"].items():
        group, name = section.split(".", 1)
        measured = results[group].get(name)
        if measured is None:
            failures.append(f"{section}: no se midió")
            continue
        for metric, minimum in thresholds.items():
            value = measured["resumen"][metric]
            if value < minimum:
                failures.append(f"{section} {metric}: {value:.4f} < {minimum}")
    return failures


def _pct(value: float) -> str:
    return f"{value * 100:.1f} %"


def render_block(results: dict, controls: dict[str, dict]) -> str:
    k, p, data = results["parametros"]["top_k"], results["parametros"], results["conjunto"]
    kinds = ", ".join(f"{v} {KIND_LABELS[n]}" for n, v in data["por_tipo"].items())
    overlap = ", ".join(f"{KIND_LABELS[n]} {v}" for n, v in data["traslape_con_la_fuente"].items())
    lines = [BEGIN, "",
             f"Conjunto `{data['version']}`: {data['documentos']} documentos, {data['preguntas']} preguntas "
             f"({kinds}); raíces de contenido que comparte una pregunta con su fuente, en promedio: "
             f"{overlap}. "
             f"top-k = {k}, piso de similitud = {p['piso_similitud']}, embeddings `{p['modelo_embeddings']}` "
             f"({p['motor_embeddings']}).", "",
             "**Recuperación** (preguntas con respuesta; la abstención es sobre las de fuera de dominio)", "",
             f"| Recuperador | hit@1 | hit@{k} | hit@5 | MRR@{DEPTH} | cobertura de citas@{k} "
             "| sin resultados "
             f"| hit@{k} literal | hit@{k} paráfrasis | hit@{k} multi | abstención fuera de dominio |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    names = {"palabras": "Palabras clave (caída)", "hibrido": f"Híbrido (embeddings, piso {MIN_SIMILARITY})",
             "hibrido_sin_piso": "Embeddings sin piso (referencia)",
             "fusion_rrf": "Fusión RRF palabras + embeddings (referencia)"}

    def label(name: str, entry: dict) -> str:
        if name in names:
            return names[name]
        floor = "sin piso" if name.endswith("_sin_piso") else f"piso {MIN_SIMILARITY}"
        return f"Candidato `{entry['modelo']}`, {floor} (referencia)"

    rows = [(label(n, r), r) for n, r in results["recuperacion"].items()]
    rows += [(f"Control: {SABOTAGE_LABELS[n]}", c["recuperacion"]["hibrido"]) for n, c in controls.items()
             if n != "respuestas_alteradas"]
    for label, r in rows:
        s = r["resumen"]
        lines.append(f"| {label} | {_pct(s['hit@1'])} | {_pct(s[f'hit@{k}'])} | {_pct(s['hit@5'])} | "
                     f"{s['mrr']:.3f} | {_pct(s[f'cobertura_citas@{k}'])} | {_pct(s['sin_resultados'])} | "
                     f"{_pct(s[f'hit@{k}.literal'])} | {_pct(s[f'hit@{k}.parafrasis'])} | "
                     f"{_pct(s[f'hit@{k}.multi'])} | {_pct(s['abstencion_fuera_de_dominio'])} |")
    lines += ["", "**¿Dónde podría cortar un piso de similitud?** (mejor coseno de cada pregunta, sin piso)",
              "", "| Embeddings | mínimo con respuesta | mediana con respuesta | máximo fuera de dominio |",
              "|---|---|---|---|"]
    for name, entry in results["recuperacion"].items():
        if name.endswith("sin_piso"):
            s, model = entry["separacion"], entry.get("modelo", p["modelo_embeddings"])
            lines.append(f"| `{model}` | {s['top1_con_respuesta_min']:.3f} | "
                         f"{s['top1_con_respuesta_mediana']:.3f} | {s['top1_fuera_de_dominio_max']:.3f} |")
    lines += ["", "**Respuestas** (fidelidad y citas verificadas sin LLM contra los fragmentos citados)", "",
              "| Generador | fidelidad | respuestas 100 % fieles | con cita válida | cita la fuente esperada "
              "| hechos clave | F1 vs. referencia | abstención fuera de dominio | abstención indebida "
              "| citas fantasma |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    answer_names = {"extractivo_hibrido": "Extractivo sobre el híbrido",
                    "extractivo_palabras": "Extractivo sobre palabras clave"}
    answer_rows = [(answer_names.get(n, f"LLM `{r.get('modelo', n)}` sobre el híbrido"), r)
                   for n, r in results["respuestas"].items()]
    if "respuestas_alteradas" in controls:
        answer_rows.append((f"Control: {SABOTAGE_LABELS['respuestas_alteradas']} (extractivo)",
                            controls["respuestas_alteradas"]["respuestas"]["extractivo_hibrido"]))
    for label, r in answer_rows:
        s = r["resumen"]
        lines.append(f"| {label} | {_pct(s['fidelidad'])} | {_pct(s['respuestas_fieles'])} | "
                     f"{_pct(s['con_cita_valida'])} | {_pct(s['cita_fuente_esperada'])} | "
                     f"{_pct(s['hechos_clave'])} | {_pct(s['f1_referencia'])} | "
                     f"{_pct(s['abstencion_fuera_de_dominio'])} | {_pct(s['abstencion_indebida'])} | "
                     f"{s['citas_fantasma']} |")
    judged = [(n, r["juez"]) for n, r in results["respuestas"].items() if "juez" in r]
    if judged:
        lines += ["", "**Juez LLM (opcional, no entra a la compuerta)**", "",
                  "| Respuestas | fidelidad según el juez | acuerdo con el verificador determinista "
                  "| afirmaciones comparadas |", "|---|---|---|---|"]
        for name, j in judged:
            lines.append(f"| `{name}` | {_pct(j['fidelidad_juez'])} | {_pct(j['acuerdo_con_verificador'])} | "
                         f"{j['afirmaciones_comparadas']} |")
    lines += ["", "**Compuerta** (`evaluacion/rag/compuerta.json`)", "",
              "| Corrida | Resultado |", "|---|---|"]
    failures = gate_failures(results)
    lines.append(f"| Medición real | {'pasa' if not failures else 'FALLA: ' + '; '.join(failures)} |")
    for name, control in controls.items():
        control_failures = gate_failures(control)
        verdict = f"FALLA ({len(control_failures)} umbrales)" if control_failures else "pasa"
        lines.append(f"| Control: {SABOTAGE_LABELS[name]} | {verdict} |")
    lines += ["", END]
    return "\n".join(lines)


def full_run() -> tuple[dict, dict[str, dict]]:
    results = run_evaluation()
    controls = {name: run_evaluation(sabotage=name) for name in SABOTAGES}
    return results, controls


def _replace_block(text: str, block: str) -> str:
    start, end = text.index(BEGIN), text.index(END) + len(END)
    return text[:start] + block + text[end:]


def _print_summary(results: dict) -> None:
    for group in ("recuperacion", "respuestas"):
        for name, entry in results[group].items():
            summary = " · ".join(f"{m}={v}" for m, v in entry["resumen"].items())
            print(f"{group}.{name}: {summary}")
            if entry.get("fallos_top_k"):
                print(f"  fuera del top-{TOP_K}: {' '.join(entry['fallos_top_k'])}")
            if entry.get("juez"):
                print(f"  juez: {entry['juez']}")
    print(f"embeddings: {results['embeddings']}")


# --------------------------------------------------------------------------- #
# Offline producers (need a model; never run in the CI)
# --------------------------------------------------------------------------- #


class OpenAICompatibleEmbeddings(EmbeddingProvider):
    """`/v1/embeddings` of an OpenAI-compatible server (e.g. llama.cpp serving the same GGUF as Ollama)."""

    name = "openai"

    def __init__(self, base_url: str, model: str, timeout: float = 120.0) -> None:
        self._url, self._model, self._timeout = base_url.rstrip("/"), model, timeout

    def embed(self, texts: list[str]) -> list[list[float]]:
        response = httpx.post(f"{self._url}/v1/embeddings", json={"model": self._model, "input": texts},
                              timeout=self._timeout)
        response.raise_for_status()
        return [d["embedding"] for d in sorted(response.json()["data"], key=lambda d: d["index"])]


def compute_embeddings(provider: EmbeddingProvider, engine: str, eval_set: EvalSet | None = None,
                       model: str = EMBEDDING_MODEL, path: Path = EMBEDDINGS) -> Path:
    """Embed every fragment (title + content, as the hybrid store does) and every question."""
    eval_set = eval_set or load_eval_set()
    texts = sorted({d.fragment for d in eval_set.documents} | {q.text for q in eval_set.questions})
    vectors: dict[str, list[float]] = {}
    for start in range(0, len(texts), 16):
        batch = texts[start:start + 16]
        vectors.update(zip(batch, provider.embed(batch), strict=True))
    write_cache(path, model, engine, vectors)
    return path


async def produce_snapshot(llm, model: str, engine: str, with_judge: bool,
                           eval_set: EvalSet | None = None) -> Path:
    from callforge.evaluation.metrics import claims  # local: only this producer needs it

    eval_set = eval_set or load_eval_set()
    store = build_store(eval_set, CachedEmbeddingProvider(EMBEDDINGS))
    answerer, judge = AgentAnswerer(llm), LLMJudge(llm) if with_judge else None
    records = []
    for number, q in enumerate(eval_set.questions, start=1):
        snippets = store.search(q.text, top_k=TOP_K)
        answer = await answerer.answer(q.text, snippets)
        record = {"id": q.id, "mostrados": [s.document_id for s in snippets], "reply": answer.reply,
                  "citations": answer.citations, "raw": answer.raw, "error": answer.error}
        if judge is not None and answer.reply:
            found = claims(answer.reply)
            verdicts = await judge.judge(found, [s.title + "\n" + s.content for s in snippets])
            record["juez"] = ({"afirmaciones": [{"texto": c, "respaldada": v} for c, v in zip(found, verdicts,
                                                                                               strict=True)]}
                              if verdicts is not None else {"error": "fuera de contrato o caído"})
        records.append(record)
        print(f"[{number}/{len(eval_set.questions)}] {q.id} citas={answer.citations} error={answer.error}",
              flush=True)
    slug = re.sub(r"[^a-z0-9.]+", "-", model.lower()).strip("-")
    path = SNAPSHOTS / f"{slug}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "modelo": model, "motor": engine, "juez": model if with_judge else None,
        "generado": datetime.now(UTC).strftime("%Y-%m-%d"), "recuperador": "hibrido", "top_k": TOP_K,
        "conjunto": eval_set.version, "respuestas": records,
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return path


def _llm_provider(api: str, url: str, model: str):
    if api == "ollama":
        from callforge.infrastructure.llm.ollama_provider import OllamaProvider

        return OllamaProvider(base_url=url, model=model, timeout=300.0, num_ctx=4096)
    from callforge.infrastructure.llm.groq_provider import GroqProvider  # OpenAI-compatible client

    return GroqProvider(api_key="local", model=model, base_url=url, timeout=300.0)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m callforge.evaluation.rag",
                                     description="Evaluación del RAG de CallForge")
    sub = parser.add_subparsers(dest="comando")
    emb = sub.add_parser("embeddings", help="recalcula el caché de embeddings con el modelo real")
    emb.add_argument("--api", choices=("ollama", "openai"), default="ollama")
    emb.add_argument("--url", default="http://localhost:11434")
    emb.add_argument("--motor", required=True, help="descripción del motor (versión, formato del modelo)")
    llm = sub.add_parser("llm", help="genera la instantánea de respuestas del SupportAgent con un LLM local")
    llm.add_argument("--api", choices=("ollama", "openai"), default="ollama")
    llm.add_argument("--url", default="http://localhost:11434")
    llm.add_argument("--modelo", required=True)
    llm.add_argument("--motor", required=True)
    llm.add_argument("--juez", action="store_true", help="pide además el veredicto del juez LLM")
    parser.add_argument("--compuerta", action="store_true",
                        help="sale con 1 si una métrica queda bajo el umbral")
    parser.add_argument("--sabotaje", choices=SABOTAGES, help="control negativo: la compuerta debe fallar")
    parser.add_argument("--informe", action="store_true",
                        help=f"reescribe el bloque de cifras de {REPORT.name}")
    parser.add_argument("--revisar-informe", action="store_true",
                        help="sale con 1 si el informe no está al día")
    parser.add_argument("--json", type=Path, help="guarda los resultados completos en este archivo")
    args = parser.parse_args(argv)

    if args.comando == "embeddings":
        provider = (OllamaEmbeddingProvider(base_url=args.url, model=EMBEDDING_MODEL) if args.api == "ollama"
                    else OpenAICompatibleEmbeddings(args.url, EMBEDDING_MODEL))
        print(f"caché escrito: {compute_embeddings(provider, args.motor)}")
        return 0
    if args.comando == "llm":
        path = asyncio.run(produce_snapshot(_llm_provider(args.api, args.url, args.modelo), args.modelo,
                                            args.motor, args.juez))
        print(f"instantánea escrita: {path}")
        return 0

    if args.informe or args.revisar_informe:
        results, controls = full_run()
        block = render_block(results, controls)
        current = REPORT.read_text(encoding="utf-8")
        if args.revisar_informe:
            stale = _replace_block(current, block) != current
            print("el informe está al día" if not stale else
                  f"{REPORT} no coincide con la medición: corre --informe y revisa el texto")
            return 1 if stale else 0
        REPORT.write_text(_replace_block(current, block), encoding="utf-8")
        print(f"bloque de cifras actualizado en {REPORT}")
        return 0

    results = run_evaluation(sabotage=args.sabotaje)
    _print_summary(results)
    if args.json:
        args.json.write_text(json.dumps(results, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if args.compuerta:
        failures = gate_failures(results)
        for failure in failures:
            print(f"COMPUERTA: {failure}")
        print("compuerta: " + ("pasa" if not failures else f"FALLA ({len(failures)} umbrales)"))
        return 1 if failures else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
