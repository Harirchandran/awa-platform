"""Protocol and registry for workflow-specific field collection policies."""
from typing import Any, Dict, Protocol, Tuple, Optional

from backend.logic.engine.configured_field_collection import (
    WorkflowFieldDefinition,
    WorkflowFieldCandidate,
    WorkflowTurnEvidence,
)


class WorkflowFieldPolicy(Protocol):
    def field_definitions(self, context: Any) -> Tuple[WorkflowFieldDefinition, ...]:
        ...

    def semantic_evidence(self, context: Any, raw_extraction: Any) -> WorkflowTurnEvidence:
        ...

    def exact_fast_path(self, context: Any, focused_field: WorkflowFieldDefinition, text: str) -> Optional[WorkflowFieldCandidate]:
        ...

    def normalize_candidate(self, context: Any, field_def: WorkflowFieldDefinition, raw_value: Any) -> Any:
        ...

    def validate_candidate(self, context: Any, field_def: WorkflowFieldDefinition, normalized_value: Any) -> Optional[str]:
        ...

    def remaining_required_fields(self, context: Any) -> Tuple[WorkflowFieldDefinition, ...]:
        ...


# Registry for enabled and fully compliant policies.
# If a policy is incomplete, it must NOT be registered here.
WORKFLOW_FIELD_POLICIES: Dict[str, WorkflowFieldPolicy] = {}
