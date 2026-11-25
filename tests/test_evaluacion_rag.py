"""Evaluación del RAG: conjunto versionado, métricas deterministas, compuerta del CI y controles negativos.

Lo que se cuida y cómo se demuestra que la prueba no es de adorno:
* el conjunto es consistente (fuentes que existen, hechos clave textuales en su fuente, todo documento usado);
* el caché de embeddings (vectores reales de nomic-embed-text) cubre cada texto: un faltante degradaría el
  híbrido a palabras clave EN SILENCIO, así que la corrida cuenta los fallos y la compuerta los rechaza;
* cada métrica se prueba con casos calculados a mano (no copiando su fórmula);
* la compuerta pasa con la medición real y FALLA con cada sabotaje: embeddings barajados, recuperador al azar
  y respuestas con las cifras alteradas; también falla por línea de comandos (lo mismo que corre el CI);
* el informe versionado (docs/evaluacion_rag.md) trae exactamente las cifras medidas.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from callforge.config import Settings
from callforge.evaluation import rag
from callforge.evaluation.answers import (
    ABSTENTION_REPLY,
    AgentAnswerer,
    ExtractiveAnswerer,
    LLMJudge,
    parse_citations,
)
from callforge.evaluation.dataset import load_eval_set, validate
from callforge.evaluation.embedding_cache import (
    CachedEmbeddingProvider,
    EmbeddingCacheMiss,
    ShuffledEmbeddingProvider,
    cache_key,
    decode_vector,
    encode_vector,
)
from callforge.evaluation.metrics import (
    citation_coverage,
    claims,
    hit_at_k,
    key_fact_recall,
    reciprocal_rank,
    token_f1,
    verify_answer,
)
from callforge.infrastructure.knowledge.store import KnowledgeSnippet
from callforge.infrastructure.llm.mock_provider import MockProvider
from tests.apoyo import GuionLLM

K = rag.TOP_K


@pytest.fixture(scope="module")
def conjunto():
    return load_eval_set()


@pytest.fixture(scope="module")
def medicion():
    return rag.run_evaluation()


@pytest.fixture(scope="module")
def controles():
    return {nombre: rag.run_evaluation(sabotage=nombre) for nombre in rag.SABOTAGES}


# --------------------------------------------------------------------------- #
# El conjunto de evaluación
# --------------------------------------------------------------------------- #


def test_el_conjunto_es_consistente_y_cubre_toda_la_base(conjunto):
    assert validate(conjunto) == []
    assert len(conjunto.documents) == 40
    assert len(conjunto.questions) == 86
    por_tipo = {k: sum(q.kind == k for q in conjunto.questions)
                for k in ("literal", "parafrasis", "multi", "fuera_de_dominio")}
    assert por_tipo == {"literal": 32, "parafrasis": 32, "multi": 14, "fuera_de_dominio": 8}
    assert {d.origin for d in conjunto.documents} == {"repo", "sintetico"}


def test_la_validacion_detecta_un_conjunto_roto(conjunto):
    """Control negativo del validador: una fuente inexistente y un hecho que no está en su fuente."""
    from dataclasses import replace

    roto = replace(conjunto, questions=(
        replace(conjunto.questions[0], sources=("KB99",)),
        replace(conjunto.questions[1], key_facts=("cifra que no existe",)),
        *conjunto.questions[2:],
    ))
    errores = validate(roto)
    assert any("KB99 no existe" in e for e in errores)
    assert any("cifra que no existe" in e for e in errores)


# --------------------------------------------------------------------------- #
# Caché de embeddings (vectores reales, sin red en el CI)
# --------------------------------------------------------------------------- #


def test_el_cache_es_del_modelo_de_produccion_y_cubre_cada_texto(conjunto):
    cache = CachedEmbeddingProvider(rag.EMBEDDINGS)
    assert cache.model == Settings.model_fields["embedding_model"].default == "nomic-embed-text"
    assert cache.dimensions == 768
    textos = [d.fragment for d in conjunto.documents] + [q.text for q in conjunto.questions]
    assert [t for t in textos if t not in cache] == []


def test_la_llave_del_cache_incluye_el_modelo_y_un_texto_editado_es_un_fallo(conjunto):
    cache = CachedEmbeddingProvider(rag.EMBEDDINGS)
    pregunta = conjunto.questions[0].text
    assert cache_key("nomic-embed-text", pregunta) != cache_key("otro-modelo", pregunta)
    with pytest.raises(EmbeddingCacheMiss):
        cache.embed([pregunta + " (editada)"])
    assert cache.misses == 1


def test_float16_ida_y_vuelta_conserva_el_vector():
    vector = [0.1234, -0.5, 0.0, 1.0]
    assert decode_vector(encode_vector(vector)) == pytest.approx(vector, abs=1e-3)


def test_el_barajado_no_deja_a_ningun_documento_con_su_vector(conjunto):
    cache = CachedEmbeddingProvider(rag.EMBEDDINGS)
    fragmentos = [d.fragment for d in conjunto.documents]
    barajado = ShuffledEmbeddingProvider(cache, fragmentos)
    assert all(barajado.embed([f]) != cache.embed([f]) for f in fragmentos)
    assert barajado.embed([conjunto.questions[0].text]) == cache.embed([conjunto.questions[0].text])


def test_un_cache_incompleto_hace_fallar_la_compuerta_aunque_el_hibrido_responda(conjunto, tmp_path):
    """Sin un vector el híbrido degrada a palabras clave: la corrida lo cuenta y la compuerta lo rechaza."""
    datos = json.loads(rag.EMBEDDINGS.read_text(encoding="utf-8"))
    del datos["vectores"][cache_key(datos["modelo"], conjunto.questions[0].text)]
    incompleto = tmp_path / "incompleto.json"
    incompleto.write_text(json.dumps(datos), encoding="utf-8")
    resultados = rag.run_evaluation(embedder=CachedEmbeddingProvider(incompleto), snapshots=[])
    assert resultados["embeddings"]["fallos_de_cache"] > 0
    assert any(f.startswith("embeddings:") for f in rag.gate_failures(resultados))


# --------------------------------------------------------------------------- #
# Métricas, con casos calculados a mano
# --------------------------------------------------------------------------- #


def test_hit_mrr_y_cobertura_de_citas():
    ranking = ["KB07", "KB03", "KB01", "KB12"]
    assert (hit_at_k(ranking, {"KB01"}, 1), hit_at_k(ranking, {"KB01"}, 3)) == (0.0, 1.0)
    assert reciprocal_rank(ranking, {"KB01"}) == pytest.approx(1 / 3)
    assert reciprocal_rank(ranking, {"KB99"}) == 0.0
    assert reciprocal_rank([], {"KB01"}) == 0.0
    assert citation_coverage(ranking, {"KB03", "KB12"}, 3) == 0.5   # KB12 quedó en el 4.º lugar
    assert citation_coverage(ranking, {"KB03", "KB07"}, 2) == 1.0


FRAGMENTO = "Cómo reiniciar el módem\nDesconecta el módem de la corriente 60 segundos y vuelve a conectarlo."


def test_una_afirmacion_respaldada_por_el_fragmento_citado_es_fiel():
    revision = verify_answer("Desconecta el módem de la corriente 60 segundos.", [1], [FRAGMENTO])
    assert revision.faithfulness == 1.0
    assert revision.claims[0].supported


def test_una_cifra_que_no_esta_en_el_fragmento_no_es_fiel():
    revision = verify_answer("Desconecta el módem de la corriente 90 segundos.", [1], [FRAGMENTO])
    assert revision.faithfulness == 0.0
    assert revision.claims[0].missing_numbers == ["90"]


def test_sin_citas_ninguna_afirmacion_esta_respaldada():
    revision = verify_answer("Desconecta el módem de la corriente 60 segundos.", [], [FRAGMENTO])
    assert revision.faithfulness == 0.0


def test_una_cita_a_un_fragmento_que_no_se_mostro_es_fantasma():
    revision = verify_answer("Desconecta el módem de la corriente 60 segundos.", [1, 4], [FRAGMENTO])
    assert revision.phantom_citations == [4]
    assert revision.faithfulness == 1.0   # la [1] sí la respalda


def test_la_abstencion_no_se_califica_como_afirmacion():
    revision = verify_answer(ABSTENTION_REPLY, [], [FRAGMENTO])
    assert revision.abstained and revision.faithfulness is None
    assert claims("¿Algo más en lo que te ayude? Hola.") == []   # preguntas y saludos no son afirmaciones


def test_hechos_clave_y_f1_contra_la_referencia():
    assert key_fact_recall("Paga hasta el día 10 sin recargo de $80.", ["dia 10", "$80", "48 horas"]) == \
        pytest.approx(2 / 3)
    assert token_f1("el módem se reinicia", "el módem se reinicia") == 1.0
    assert token_f1("pizza grande", "reiniciar el módem") == 0.0


# --------------------------------------------------------------------------- #
# Generadores de respuesta y juez
# --------------------------------------------------------------------------- #


def _snippets(*docs) -> list[KnowledgeSnippet]:
    return [KnowledgeSnippet(document_id=d.id, title=d.title, content=d.content, score=1.0) for d in docs]


def test_el_extractivo_copia_y_cita_y_sin_fragmentos_se_abstiene(conjunto):
    kb01, kb02 = conjunto.document("KB01"), conjunto.document("KB02")
    respuesta = ExtractiveAnswerer().answer("¿Cuántos segundos desconecto el módem?", _snippets(kb02, kb01))
    assert respuesta.citations == [2] and "60 segundos" in respuesta.reply
    revision = verify_answer(respuesta.reply, respuesta.citations, [kb02.fragment, kb01.fragment])
    assert revision.faithfulness == 1
    assert ExtractiveAnswerer().answer("¿Y la pizza?", []).reply == ABSTENTION_REPLY


def test_el_support_agent_de_produccion_devuelve_sus_citas(conjunto):
    kb01 = conjunto.document("KB01")
    llm = GuionLLM({"SupportAgent": [{"reply": "Desconecta el módem 60 segundos.", "confidence": 0.9,
                                      "needs_more_info": False, "suggest_escalation": False,
                                      "citations": [1, "2", "x"]}]})
    respuesta = asyncio.run(AgentAnswerer(llm).answer("¿Cómo reinicio el módem?", _snippets(kb01)))
    assert respuesta.citations == [1, 2]
    assert "KNOWLEDGE CONTEXT:\n[1] Cómo reiniciar el módem" in llm.llamadas[0][1]
    assert parse_citations("1") == [] and parse_citations(["[3]", 4.5, True]) == [3]
    # forma real de un modelo de 1.5B: «[n] Título del fragmento»
    assert parse_citations(["[1] Cómo reiniciar el módem", "2 Política", "10 segundos"]) == [1, 2, 10]


def test_la_respuesta_enlatada_del_mock_no_cita_y_el_verificador_la_reprueba(conjunto):
    """Control negativo de la fidelidad: una respuesta genérica sin citas no pasa por fiel."""
    kb01 = conjunto.document("KB01")
    respuesta = asyncio.run(AgentAnswerer(MockProvider()).answer("¿Cómo reinicio el módem?", _snippets(kb01)))
    revision = verify_answer(respuesta.reply, respuesta.citations, [kb01.fragment])
    assert respuesta.citations == [] and revision.faithfulness == 0.0


def test_el_juez_da_un_veredicto_por_afirmacion_y_fuera_de_contrato_no_cuenta():
    afirmaciones = ["Desconecta el módem 60 segundos.", "La visita cuesta 999 pesos."]
    bueno = GuionLLM({"GroundingJudge": [{"veredictos": [True, False]}]})
    assert asyncio.run(LLMJudge(bueno).judge(afirmaciones, [FRAGMENTO])) == [True, False]
    for respuesta in ({"veredictos": [True]}, {"veredictos": ["si", "no"]}, {"otro": 1}):
        malo = GuionLLM({"GroundingJudge": [respuesta]})
        assert asyncio.run(LLMJudge(malo).judge(afirmaciones, [FRAGMENTO])) is None


# --------------------------------------------------------------------------- #
# Medición, compuerta y controles negativos
# --------------------------------------------------------------------------- #


def test_la_medicion_usa_embeddings_reales_sin_un_solo_fallo_de_cache(medicion):
    assert medicion["embeddings"]["llamadas"] > 0
    assert medicion["embeddings"]["fallos_de_cache"] == 0
    assert medicion["parametros"]["top_k"] == Settings.model_fields["knowledge_top_k"].default
    piso = Settings.model_fields["knowledge_min_similarity"].default
    assert medicion["parametros"]["piso_similitud"] == piso


def test_la_compuerta_pasa_con_la_medicion_real(medicion):
    assert rag.gate_failures(medicion) == []


@pytest.mark.parametrize(("sabotaje", "umbral_que_debe_caer"), [
    ("embeddings_barajados", f"recuperacion.hibrido hit@{K}"),
    ("recuperador_azar", f"recuperacion.palabras hit@{K}"),
    ("respuestas_alteradas", "respuestas.extractivo_hibrido fidelidad"),
])
def test_control_negativo_la_compuerta_falla_con_cada_sabotaje(controles, sabotaje, umbral_que_debe_caer):
    fallas = rag.gate_failures(controles[sabotaje])
    assert any(f.startswith(umbral_que_debe_caer) for f in fallas), fallas


def test_control_negativo_las_metricas_caen_de_verdad(medicion, controles):
    real = medicion["recuperacion"]["hibrido"]["resumen"]
    barajado = controles["embeddings_barajados"]["recuperacion"]["hibrido"]["resumen"]
    azar = controles["recuperador_azar"]["recuperacion"]["hibrido"]["resumen"]
    assert barajado[f"hit@{K}"] < real[f"hit@{K}"] / 2
    assert azar[f"hit@{K}"] < real[f"hit@{K}"] / 2
    assert barajado["mrr"] < real["mrr"] / 2
    alteradas = controles["respuestas_alteradas"]["respuestas"]["extractivo_hibrido"]["resumen"]
    assert alteradas["fidelidad"] < medicion["respuestas"]["extractivo_hibrido"]["resumen"]["fidelidad"]


def test_la_compuerta_por_linea_de_comandos_es_la_del_ci(capsys):
    assert rag.main(["--compuerta"]) == 0
    assert rag.main(["--sabotaje", "embeddings_barajados", "--compuerta"]) == 1
    assert "compuerta: FALLA" in capsys.readouterr().out


def test_cada_umbral_de_la_compuerta_existe_en_la_medicion(medicion):
    compuerta = json.loads(rag.GATE.read_text(encoding="utf-8"))
    for seccion, umbrales in compuerta["umbrales"].items():
        grupo, nombre = seccion.split(".", 1)
        assert set(umbrales) <= set(medicion[grupo][nombre]["resumen"]), seccion
        for metrica, minimo in umbrales.items():   # umbral medido, no de adorno: no más de 0.10 abajo
            assert medicion[grupo][nombre]["resumen"][metrica] - minimo <= 0.10, (seccion, metrica)


def test_el_informe_versionado_trae_las_cifras_medidas():
    assert rag.main(["--revisar-informe"]) == 0


def test_el_informe_desactualizado_se_detecta(tmp_path, monkeypatch):
    copia = tmp_path / "evaluacion_rag.md"
    texto = Path(rag.REPORT).read_text(encoding="utf-8")
    copia.write_text(texto.replace("| Palabras clave (caída) | ", "| Palabras clave (caída) | 0.0 % | ", 1),
                     encoding="utf-8")
    monkeypatch.setattr(rag, "REPORT", copia)
    assert rag.main(["--revisar-informe"]) == 1
