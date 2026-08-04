from enum import Enum
class ConversationState(str, Enum):
    """
    Workflow State Machine States.
    Based on Part II-A: Conversation Workflow State Machine Contract.
    """
    NEW = "NEW"
    INTENT_IDENTIFIED = "INTENT_IDENTIFIED"
    COLLECTING_DATA = "COLLECTING_DATA"
    WAITING_CUSTOMER = "WAITING_CUSTOMER"
    VALIDATING = "VALIDATING"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    ESCALATED = "ESCALATED"