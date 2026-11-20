"""Conversation lifecycle: the allowed status transitions, in one place.

    ACTIVE ──(workflow escalates)──▶ ESCALATED ──(human closes)──▶ RESOLVED / CLOSED
      │  ▲                                                           │
      │  └──────(customer writes again on a RESOLVED one)────────────┘
      ├──(feedback "resolved" / close resolved)──▶ RESOLVED
      └──(close not resolved)──▶ CLOSED (terminal)

* ESCALATED belongs to a human: the bot never takes it back (no ESCALATED → ACTIVE).
* CLOSED is terminal: it accepts no message and cannot be reopened or re-closed.
* Staying in the same status is allowed (e.g. a second message while ACTIVE),
  except in CLOSED, where nothing is allowed.

See docs/adr/0003-ciclo-de-vida-de-la-conversacion.md.
"""
from __future__ import annotations

from callforge.domain.entities import Conversation, utcnow
from callforge.domain.value_objects import ConversationStatus

_S = ConversationStatus

TRANSITIONS: dict[ConversationStatus, frozenset[ConversationStatus]] = {
    _S.ACTIVE: frozenset({_S.ESCALATED, _S.RESOLVED, _S.CLOSED}),
    _S.RESOLVED: frozenset({_S.ACTIVE, _S.CLOSED}),
    _S.ESCALATED: frozenset({_S.RESOLVED, _S.CLOSED}),
    _S.CLOSED: frozenset(),
}


class InvalidTransitionError(Exception):
    """A status change the lifecycle does not allow."""

    def __init__(self, current: ConversationStatus, target: ConversationStatus) -> None:
        super().__init__(f"invalid conversation transition {current.value} -> {target.value}")
        self.current = current
        self.target = target


def can_transition(current: ConversationStatus, target: ConversationStatus) -> bool:
    if current is _S.CLOSED:
        return False
    return current == target or target in TRANSITIONS[current]


def transition(conversation: Conversation, target: ConversationStatus) -> None:
    """Move the conversation to ``target`` or raise InvalidTransitionError."""
    if not can_transition(conversation.status, target):
        raise InvalidTransitionError(conversation.status, target)
    if conversation.status != target:
        conversation.status = target
        conversation.updated_at = utcnow()
