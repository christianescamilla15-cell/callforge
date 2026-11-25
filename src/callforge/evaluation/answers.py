"""Answer generators under evaluation and the optional LLM judge.

* `ExtractiveAnswerer` — deterministic baseline (no LLM): picks the sentences of the retrieved
  fragments that share the most content with the question and cites where they came from. It measures
  the retrieval → answer path in the CI, where there is no model.
* `AgentAnswerer` — the production `SupportAgent` (same prompt, same JSON contract) over any
  `LLMProvider`; run locally against a small model, its answers are versioned as a snapshot.
* `LLMJudge` — optional second opinion on faithfulness: a model says, claim by claim, whether the cited
  fragments back it. It never gates the CI.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from callforge.agents.base import AgentContext, extract_json
from callforge.agents.support_agent import SupportAgent
from callforge.evaluation.metrics import content_stems, split_sentences
from callforge.infrastructure.knowledge.store import KnowledgeSnippet
from callforge.infrastructure.llm.base import LLMProvider, LLMUnavailableError

ABSTENTION_REPLY = "No encontré esa información en la base de conocimiento."
_CITATION = re.compile(r"^\[?(\d+)\]?(?:\s|$)")


@dataclass
class Answer:
    reply: str
    citations: list[int] = field(default_factory=list)
    raw: str = ""
    error: str | None = None


def parse_citations(value: object) -> list[int]:
    """`citations` from the agent JSON. Models write them as 2, "2", "[2]" or "[2] Título del fragmento"
    (seen with a 1.5B model): the leading fragment number counts; anything else is dropped."""
    if not isinstance(value, list):
        return []
    parsed = []
    for item in value:
        if isinstance(item, bool):
            continue
        match = _CITATION.match(str(item).strip())  # 4.5 does not match: "4" is followed by "."
        if match:
            parsed.append(int(match.group(1)))
    return parsed


class ExtractiveAnswerer:
    """Deterministic baseline: up to `max_sentences` sentences copied from the retrieved fragments."""

    def __init__(self, max_sentences: int = 2) -> None:
        self._max = max_sentences

    def answer(self, question: str, snippets: Sequence[KnowledgeSnippet]) -> Answer:
        if not snippets:
            return Answer(reply=ABSTENTION_REPLY)
        wanted = set(content_stems(question))
        candidates = []
        for rank, snippet in enumerate(snippets, start=1):
            for position, sentence in enumerate(split_sentences(snippet.content)):
                overlap = len(wanted & set(content_stems(sentence)))
                candidates.append((overlap, -rank, -position, sentence, rank))
        candidates.sort(reverse=True)
        chosen = [c for c in candidates if c[0] > 0][: self._max]
        if not chosen:  # no shared words (a paraphrase): trust the ranking, open the top fragment
            chosen = [c for c in candidates if c[4] == 1][: self._max]
        chosen.sort(key=lambda c: (-c[1], -c[2]))  # reading order: fragment rank, then position
        return Answer(reply=" ".join(c[3] for c in chosen), citations=sorted({c[4] for c in chosen}))


class AgentAnswerer:
    """The production SupportAgent answering with the retrieved fragments as its knowledge context."""

    def __init__(self, llm: LLMProvider) -> None:
        self._agent = SupportAgent(llm)

    async def answer(self, question: str, snippets: Sequence[KnowledgeSnippet]) -> Answer:
        ctx = AgentContext(conversation_id="evaluacion-rag", user_message=question, knowledge=list(snippets))
        result = await self._agent.run(ctx)
        if result.failed:
            return Answer(reply="", raw="", error=result.run.error or "fallo del proveedor")
        return Answer(reply=str(result.data.get("reply") or "").strip(),
                      citations=parse_citations(result.data.get("citations")), raw=result.text)


JUDGE_SYSTEM = """You are the GroundingJudge of a customer support quality team.
Recibes FRAGMENTOS de la base de conocimiento y AFIRMACIONES numeradas de una respuesta.
Para cada afirmación decide si los fragmentos la respaldan: true sólo si lo que afirma (incluidas
cifras, plazos y montos) se deduce de los fragmentos; false si agrega, contradice o inventa algo.
Responde SOLO con JSON: {"veredictos": [true|false, ...]} con un valor por afirmación, en orden."""


class LLMJudge:
    """Per-claim grounding verdicts. It is given every fragment the agent was SHOWN, while the deterministic
    verifier only accepts the fragments the answer CITES: the judge asks «is it in the context?», the
    verifier also asks «did it cite the right place?». Their disagreement is reported, never gated."""

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    async def judge(self, claims: Sequence[str], fragments: Sequence[str]) -> list[bool] | None:
        """One verdict per claim, or None when the judge is down or answers out of contract."""
        if not claims:
            return []
        prompt = "FRAGMENTOS:\n" + "\n---\n".join(f"[{i}] {f}" for i, f in enumerate(fragments, 1))
        prompt += "\n\nAFIRMACIONES:\n" + "\n".join(f"{i}. {c}" for i, c in enumerate(claims, 1))
        try:
            result = await self._llm.complete(JUDGE_SYSTEM, [{"role": "user", "content": prompt}],
                                              json_mode=True)
        except LLMUnavailableError:
            return None
        data = extract_json(result.text) or {}
        verdicts = data.get("veredictos")
        if not isinstance(verdicts, list) or len(verdicts) != len(claims):
            return None
        if not all(isinstance(v, bool) for v in verdicts):
            return None
        return verdicts
