"""Regression tests for semantic-safe deterministic routing fallbacks."""

from backend.pipeline.message_router import MessageRouter


def _overlapping_service_and_catalog_config():
    return {
        "services": [
            {
                "id": "svc-cleaning",
                "name": "Dental Cleaning",
                "enabled": True,
            }
        ],
        "modules": {
            "booking": {"enabled": True},
            "order": {
                "enabled": True,
                "categories": [
                    {
                        "id": "cat-services",
                        "name": "Services",
                        "enabled": True,
                        "items": [
                            {
                                "id": "item-cleaning",
                                "name": "Dental Cleaning",
                                "enabled": True,
                            }
                        ],
                    }
                ],
            },
        },
    }


def test_temporal_service_request_beats_passive_catalog_name_collision():
    decision = MessageRouter().route_message(
        "I need a dental cleaning sometime next week after five",
        _overlapping_service_and_catalog_config(),
    )

    assert decision.intent == "booking"
    assert decision.reason == "configured_service_booking_candidate"


def test_explicit_order_language_still_routes_to_order():
    decision = MessageRouter().route_message(
        "I want to order dental cleaning",
        _overlapping_service_and_catalog_config(),
    )

    assert decision.intent == "order"
