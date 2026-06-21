from enum import StrEnum


class Channel(StrEnum):
    API = "api"
    WHATSAPP = "whatsapp"
    WEBCHAT = "webchat"
    EMAIL = "email"
    PHONE = "phone"


class ConversationStatus(StrEnum):
    ACTIVE = "active"
    RESOLVED = "resolved"
    ESCALATED = "escalated"
    CLOSED = "closed"


class MessageRole(StrEnum):
    CUSTOMER = "customer"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class Intent(StrEnum):
    QUESTION = "question"
    TECHNICAL_ISSUE = "technical_issue"
    BILLING = "billing"
    COMPLAINT = "complaint"
    ACCOUNT = "account"
    HUMAN_REQUEST = "human_request"
    OTHER = "other"


class Urgency(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class TicketStatus(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"


class TicketPriority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class AgentName(StrEnum):
    ROUTER = "router"
    SUPPORT = "support"
    TROUBLESHOOTING = "troubleshooting"
    ESCALATION = "escalation"
    SUMMARIZER = "summarizer"
    QUALITY = "quality"
    COMPANION = "companion"


class NextAction(StrEnum):
    SUPPORT = "support"
    TROUBLESHOOTING = "troubleshooting"
    ESCALATION = "escalation"


class ResolutionStepStatus(StrEnum):
    PROPOSED = "proposed"  # given to the customer, awaiting their result
    ANSWERED = "answered"  # customer reported back
    RESOLVED = "resolved"  # this step fixed the problem
    FAILED = "failed"  # step did not help


class EventType(StrEnum):
    ROUTED = "routed"
    KNOWLEDGE_RETRIEVED = "knowledge_retrieved"
    QUALITY_CHECKED = "quality_checked"
    RETRIED = "retried"
    ESCALATED = "escalated"
    FALLBACK_USED = "fallback_used"
    ERROR = "error"
