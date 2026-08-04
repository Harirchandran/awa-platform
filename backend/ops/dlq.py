from datetime import datetime, timezone
from typing import Optional
from backend.schemas.canonical_message import CanonicalMessage, DLQMetadata, ProcessingStatus
class DLQManager:
    """
    Handles moving failed messages to DLQ with metadata enrichment.
    """
    @staticmethod
    def enrich_for_dlq(message: CanonicalMessage, reason: str, failure_code: Optional[str] = None) -> CanonicalMessage:
        """
        Appends DLQ metadata to the canonical message.
        """
        dlq_meta = DLQMetadata(
            dlq_reason=reason,
            dlq_timestamp=datetime.now(timezone.utc),
            last_failure_code=failure_code
        )
        message.dlq_metadata = dlq_meta
        message.processing.failure_code = failure_code # Also update processing block
        # Status update logic would happen in pipeline orchestrator
        return message
    @staticmethod
    def construct_replay_command(message: CanonicalMessage) -> str:
        """
        Helper to visualize replay (for CLI tool).
        """
        return f"REPLAY {message.event_id} (Tenant: {message.tenant_id})"
dlq_manager = DLQManager()