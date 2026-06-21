from __future__ import annotations

from callforge.application.dtos import StartConversationInput, StartConversationOutput
from callforge.application.unit_of_work import UnitOfWork
from callforge.domain.entities import Conversation, Customer
from callforge.domain.value_objects import Channel


class StartConversation:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self, data: StartConversationInput) -> StartConversationOutput:
        customer = None
        if data.customer_external_id:
            customer = self._uow.customers.get_by_external_id(data.customer_external_id)
        if customer is None:
            customer = Customer(
                external_id=data.customer_external_id,
                name=data.customer_name,
                email=data.customer_email,
            )
            self._uow.customers.add(customer)

        try:
            channel = Channel(data.channel)
        except ValueError:
            channel = Channel.API

        conversation = Conversation(customer_id=customer.id, channel=channel)
        self._uow.conversations.add(conversation)
        self._uow.commit()
        return StartConversationOutput(
            conversation_id=conversation.id,
            customer_id=customer.id,
            status=conversation.status.value,
        )
