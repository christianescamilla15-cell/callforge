"""Aggregated metrics queries for the /metrics endpoint, scoped per tenant.

Tables without a tenant_id column (messages, escalations, agent_runs,
llm_usage, feedback) are scoped through their conversation."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from callforge.domain.entities import DEFAULT_TENANT_ID
from callforge.infrastructure import models as m


class MetricsReader:
    def __init__(self, session: Session, tenant_id: str = DEFAULT_TENANT_ID) -> None:
        self._s = session
        self._tenant = tenant_id

    def _tenant_conversations(self):
        return select(m.ConversationModel.id).where(
            m.ConversationModel.tenant_id == self._tenant
        )

    def snapshot(self) -> dict:
        conv_ids = self._tenant_conversations()

        conversations_total = self._scalar(
            select(func.count(m.ConversationModel.id)).where(
                m.ConversationModel.tenant_id == self._tenant
            )
        )
        by_status = dict(
            self._s.execute(
                select(m.ConversationModel.status, func.count(m.ConversationModel.id))
                .where(m.ConversationModel.tenant_id == self._tenant)
                .group_by(m.ConversationModel.status)
            ).all()
        )
        messages_total = self._scalar(
            select(func.count(m.MessageModel.id)).where(
                m.MessageModel.conversation_id.in_(conv_ids)
            )
        )
        tickets_total = self._scalar(
            select(func.count(m.TicketModel.id)).where(
                m.TicketModel.tenant_id == self._tenant
            )
        )
        escalations_total = self._scalar(
            select(func.count(m.EscalationModel.id)).where(
                m.EscalationModel.conversation_id.in_(conv_ids)
            )
        )
        agent_runs_total = self._scalar(
            select(func.count(m.AgentRunModel.id)).where(
                m.AgentRunModel.conversation_id.in_(conv_ids)
            )
        )
        errors_total = self._scalar(
            select(func.count(m.AgentRunModel.id)).where(
                m.AgentRunModel.error.is_not(None),
                m.AgentRunModel.conversation_id.in_(conv_ids),
            )
        )
        avg_quality = self._s.execute(
            select(func.avg(m.AgentRunModel.confidence)).where(
                m.AgentRunModel.agent_name == "quality",
                m.AgentRunModel.conversation_id.in_(conv_ids),
            )
        ).scalar()
        llm_rows = self._s.execute(
            select(
                m.LLMUsageModel.provider,
                func.count(m.LLMUsageModel.id),
                func.coalesce(func.sum(m.LLMUsageModel.tokens_in), 0),
                func.coalesce(func.sum(m.LLMUsageModel.tokens_out), 0),
                func.coalesce(func.sum(m.LLMUsageModel.estimated_cost), 0.0),
            )
            .where(m.LLMUsageModel.conversation_id.in_(conv_ids))
            .group_by(m.LLMUsageModel.provider)
        ).all()
        feedback_count = self._scalar(
            select(func.count(m.FeedbackModel.id)).where(
                m.FeedbackModel.conversation_id.in_(conv_ids)
            )
        )
        avg_rating = self._s.execute(
            select(func.avg(m.FeedbackModel.rating)).where(
                m.FeedbackModel.conversation_id.in_(conv_ids)
            )
        ).scalar()

        resolved = by_status.get("resolved", 0)
        escalated = by_status.get("escalated", 0)
        closed_like = resolved + escalated + by_status.get("closed", 0)

        return {
            "tenant_id": self._tenant,
            "conversations": {
                "total": conversations_total,
                "by_status": by_status,
                "resolution_rate": (resolved / closed_like) if closed_like else None,
            },
            "messages_total": messages_total,
            "tickets_total": tickets_total,
            "escalations_total": escalations_total,
            "agent_runs_total": agent_runs_total,
            "agent_errors_total": errors_total,
            "avg_quality_score": float(avg_quality) if avg_quality is not None else None,
            "llm_usage": [
                {
                    "provider": provider,
                    "calls": calls,
                    "tokens_in": int(tin),
                    "tokens_out": int(tout),
                    "estimated_cost_usd": round(float(cost), 6),
                }
                for provider, calls, tin, tout, cost in llm_rows
            ],
            "feedback": {
                "count": feedback_count,
                "avg_rating": float(avg_rating) if avg_rating is not None else None,
            },
        }

    def _scalar(self, stmt) -> int:
        return self._s.execute(stmt).scalar() or 0
