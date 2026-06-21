from __future__ import annotations

import json
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from callforge.domain.entities import AgentRun, ResolutionStep
from callforge.domain.value_objects import AgentName
from callforge.infrastructure.knowledge.store import KnowledgeSnippet
from callforge.infrastructure.llm.base import LLMProvider, LLMUnavailableError

_FENCE_RE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$", re.MULTILINE)


def extract_json(text: str) -> dict | None:
    """Best-effort extraction of a JSON object from model output."""
    cleaned = _FENCE_RE.sub("", text).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


@dataclass
class AgentContext:
    conversation_id: str
    user_message: str
    history: list[dict[str, str]] = field(default_factory=list)
    knowledge: list[KnowledgeSnippet] = field(default_factory=list)
    routing: dict | None = None
    candidate_reply: str | None = None  # for QualityAgent
    quality_feedback: str | None = None  # injected on retries
    resolution_steps: list[ResolutionStep] = field(default_factory=list)


@dataclass
class AgentResult:
    agent_name: AgentName
    text: str
    data: dict
    confidence: float
    decision: str | None
    run: AgentRun
    failed: bool = False


class BaseAgent(ABC):
    name: AgentName
    system_prompt: str

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    @abstractmethod
    def build_user_prompt(self, ctx: AgentContext) -> str: ...

    def parse(self, data: dict) -> tuple[float, str | None]:
        """Return (confidence, decision) from the parsed JSON."""
        return float(data.get("confidence", 0.5)), None

    def _history_messages(self, ctx: AgentContext) -> list[dict[str, str]]:
        # Last 10 turns, mapped to chat roles.
        return [
            {"role": "user" if h["role"] == "customer" else "assistant", "content": h["content"]}
            for h in ctx.history[-10:]
        ]

    @staticmethod
    def format_knowledge(ctx: AgentContext) -> str:
        if not ctx.knowledge:
            return "KNOWLEDGE CONTEXT: (no relevant documents found)"
        blocks = [
            f"[{i + 1}] {s.title}\n{s.content}" for i, s in enumerate(ctx.knowledge)
        ]
        return "KNOWLEDGE CONTEXT:\n" + "\n---\n".join(blocks)

    async def run(self, ctx: AgentContext) -> AgentResult:
        user_prompt = self.build_user_prompt(ctx)
        messages = [*self._history_messages(ctx), {"role": "user", "content": user_prompt}]
        started = time.perf_counter()
        try:
            result = await self._llm.complete(
                self.system_prompt, messages, json_mode=True
            )
        except LLMUnavailableError as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            run = AgentRun(
                conversation_id=ctx.conversation_id,
                agent_name=self.name,
                input_text=user_prompt,
                output_text="",
                decision="error",
                confidence=0.0,
                latency_ms=latency_ms,
                model_used="",
                provider="",
                error=str(exc),
            )
            return AgentResult(
                agent_name=self.name,
                text="",
                data={},
                confidence=0.0,
                decision="error",
                run=run,
                failed=True,
            )

        data = extract_json(result.text) or {}
        confidence, decision = self.parse(data)
        run = AgentRun(
            conversation_id=ctx.conversation_id,
            agent_name=self.name,
            input_text=user_prompt,
            output_text=result.text,
            decision=decision,
            confidence=confidence,
            latency_ms=result.latency_ms,
            model_used=result.model,
            provider=result.provider,
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            estimated_cost=result.estimated_cost,
        )
        return AgentResult(
            agent_name=self.name,
            text=result.text,
            data=data,
            confidence=confidence,
            decision=decision,
            run=run,
        )
