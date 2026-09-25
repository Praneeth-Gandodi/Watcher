from backend.contracts.events import EventEnvelope, EventType, parse_event
from backend.contracts.fixtures import EVENT_ID, expected_event_types, valid_event_payloads


def test_every_canonical_event_has_a_valid_payload() -> None:
    payloads = valid_event_payloads()
    assert {payload.event_type for payload in payloads} == expected_event_types()
    for payload in payloads:
        event = EventEnvelope(
            event_id=EVENT_ID,
            sequence=1,
            producer="contract-test",
            correlation_id="contract-suite",
            occurred_at_s=1.0,
            event_type=payload.event_type,
            payload=payload,
        )
        assert parse_event(event.model_dump(mode="json")) == payload


def test_event_vocabulary_is_stable() -> None:
    assert len(list(EventType)) == 15
