from backend.domains.bookings.tool_definitions import (
    get_booking_required_fields,
    get_booking_tool_metadata,
    get_booking_tool_schema,
    get_booking_update_tool_metadata,
    get_booking_update_tool_schema,
)
from backend.domains.knowledge.tool_definitions import (
    get_faq_required_fields,
    get_faq_tool_schema,
    is_faq_llm_tool,
)
from backend.domains.leads.tool_definitions import (
    get_lead_capture_required_fields,
    get_lead_capture_tool_metadata,
    get_lead_capture_tool_schema,
)
from backend.domains.orders.tool_definitions import (
    get_order_required_fields,
    get_order_tool_metadata,
    get_order_tool_schema,
)
from backend.domains.payments.tool_definitions import (
    get_payments_required_fields,
    get_payments_tool_metadata,
    get_payments_tool_schema,
)
from backend.domains.reminders.tool_definitions import (
    ALLOWED_TRIGGER_TYPES,
    get_reminders_required_fields,
    get_reminders_tool_metadata,
    get_reminders_tool_schema,
)
from backend.domains.reviews.tool_definitions import (
    get_reviews_required_fields,
    get_reviews_tool_metadata,
    get_reviews_tool_schema,
)
from backend.domains.support.tool_definitions import (
    get_human_handoff_required_fields,
    get_human_handoff_tool_metadata,
    get_human_handoff_tool_schema,
    get_support_required_fields,
    get_support_tool_metadata,
    get_support_tool_schema,
)
from backend.modules.booking_module import BookingModule
from backend.modules.booking_update_module import BookingUpdateModule
from backend.modules.faq_module import FaqModule
from backend.modules.human_handoff_module import HumanHandoffModule
from backend.modules.lead_capture_module import LeadCaptureModule
from backend.modules.order_module import OrderModule
from backend.modules.payments_module import PaymentsModule
from backend.modules.reminders_module import RemindersModule
from backend.modules.reviews_module import ReviewsModule
from backend.modules.support_module import SupportModule


CUSTOM_FIELDS = [
    {"key": "custom_text", "label": "Custom Text", "type": "string", "required": True},
    {"key": "custom_count", "label": "Custom Count", "type": "number", "required": False},
]


def test_booking_domain_tool_definition_matches_module():
    module = BookingModule()
    configs = [{}, {"modules": {"booking": {"enabled": True}}}, {"modules": {"booking": {"fields": CUSTOM_FIELDS}}}]
    for config in configs:
        assert get_booking_tool_metadata(config) == module.get_tool_metadata(config)
        assert get_booking_required_fields(config) == module.get_required_fields(config)
        assert get_booking_tool_schema(config) == module.get_tool_schema(config)


def test_order_domain_tool_definition_matches_module():
    module = OrderModule()
    configs = [{}, {"modules": {"order": {"enabled": True}}}, {"modules": {"order": {"fields": CUSTOM_FIELDS}}}]
    for config in configs:
        assert get_order_tool_metadata(config) == module.get_tool_metadata(config)
        assert get_order_required_fields(config) == module.get_required_fields(config)
        assert get_order_tool_schema(config) == module.get_tool_schema(config)


def test_payments_domain_tool_definition_matches_module():
    module = PaymentsModule()
    configs = [{}, {"modules": {"payments": {"fields": CUSTOM_FIELDS}}}]
    for config in configs:
        assert get_payments_tool_metadata(config) == module.get_tool_metadata(config)
        assert get_payments_required_fields(config) == module.get_required_fields(config)
        assert get_payments_tool_schema(config) == module.get_tool_schema(config)


def test_reminders_domain_tool_definition_matches_module():
    module = RemindersModule()
    configs = [{}, {"modules": {"reminders": {"fields": CUSTOM_FIELDS}}}]
    for config in configs:
        assert get_reminders_tool_metadata(config) == module.get_tool_metadata(config)
        assert get_reminders_required_fields(config) == module.get_required_fields(config)
        assert get_reminders_tool_schema(config) == module.get_tool_schema(config)
    assert ALLOWED_TRIGGER_TYPES == module.ALLOWED_TRIGGER_TYPES


def test_reviews_domain_tool_definition_matches_module():
    module = ReviewsModule()
    configs = [{}, {"modules": {"reviews": {"fields": CUSTOM_FIELDS}}}]
    for config in configs:
        assert get_reviews_tool_metadata(config) == module.get_tool_metadata(config)
        assert get_reviews_required_fields(config) == module.get_required_fields(config)
        assert get_reviews_tool_schema(config) == module.get_tool_schema(config)


def test_support_domain_tool_definition_matches_module():
    module = SupportModule()
    configs = [{}, {"modules": {"support": {"fields": CUSTOM_FIELDS}}}]
    for config in configs:
        assert get_support_tool_metadata(config) == module.get_tool_metadata(config)
        assert get_support_required_fields(config) == module.get_required_fields(config)
        assert get_support_tool_schema(config) == module.get_tool_schema(config)


def test_human_handoff_domain_tool_definition_matches_module():
    module = HumanHandoffModule()
    configs = [{}, {"modules": {"human_handoff": {"fields": CUSTOM_FIELDS}}}]
    for config in configs:
        assert get_human_handoff_tool_metadata(config) == module.get_tool_metadata(config)
        assert get_human_handoff_required_fields(config) == module.get_required_fields(config)
        assert get_human_handoff_tool_schema(config) == module.get_tool_schema(config)


def test_faq_domain_tool_definition_matches_module():
    module = FaqModule()
    configs = [{}, {"modules": {"faq": {"fields": CUSTOM_FIELDS}}}]
    for config in configs:
        assert get_faq_required_fields(config) == module.get_required_fields(config)
        assert get_faq_tool_schema(config) == module.get_tool_schema(config)
    assert is_faq_llm_tool() == module.is_llm_tool()


def test_lead_capture_domain_tool_definition_matches_module():
    module = LeadCaptureModule()
    configs = [{}, {"modules": {"lead_capture": {"fields": CUSTOM_FIELDS}}}]
    for config in configs:
        assert get_lead_capture_tool_metadata(config) == module.get_tool_metadata(config)
        assert get_lead_capture_required_fields(config) == module.get_required_fields(config)
        assert get_lead_capture_tool_schema(config) == module.get_tool_schema(config)


def test_booking_update_domain_tool_definition_matches_module():
    module = BookingUpdateModule()
    configs = [{}]
    for config in configs:
        assert get_booking_update_tool_metadata(config) == module.get_tool_metadata(config)
        assert get_booking_update_tool_schema(config) == module.get_tool_schema(config)
