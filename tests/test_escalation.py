from callforge.orchestration.policies import EscalationPolicy


def test_human_request_escalates_and_creates_ticket(client):
    conversation_id = client.post(
        "/api/v1/conversations/start", json={"customer_name": "Luis"}
    ).json()["conversation_id"]

    body = client.post(
        f"/api/v1/conversations/{conversation_id}/message",
        json={"content": "Quiero hablar con un humano, esto es urgente"},
    ).json()

    assert body["escalated"] is True
    assert body["ticket_id"]
    assert body["conversation_status"] == "escalated"

    tickets = client.get("/api/v1/tickets").json()
    assert len(tickets) == 1
    assert tickets[0]["id"] == body["ticket_id"]
    assert tickets[0]["priority"] in ("low", "medium", "high", "urgent")

    ticket = client.get(f"/api/v1/tickets/{body['ticket_id']}").json()
    assert ticket["conversation_id"] == conversation_id
    assert ticket["description"]  # summary_for_human present


def test_escalation_policy_thresholds():
    policy = EscalationPolicy(
        quality_threshold=0.5, confidence_threshold=0.4, max_quality_retries=1
    )
    # Router decision wins
    assert policy.should_escalate("escalation", False, 0.9, 0.9, False)
    # Agent suggestion wins
    assert policy.should_escalate("support", True, 0.9, 0.9, False)
    # Low confidence escalates
    assert policy.should_escalate("support", False, 0.2, 0.9, False)
    # Low quality only escalates after retries are exhausted
    assert not policy.should_escalate("support", False, 0.9, 0.3, False)
    assert policy.should_escalate("support", False, 0.9, 0.3, True)
    # Healthy reply does not escalate
    assert not policy.should_escalate("support", False, 0.9, 0.9, True)
    # Retry logic
    assert policy.should_retry(0.3, 0)
    assert not policy.should_retry(0.3, 1)
    assert not policy.should_retry(0.9, 0)
