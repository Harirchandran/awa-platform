"""
Prompt Registry API Routes
GET /api/v1/prompts
PUT /api/v1/prompts/{prompt_name}
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from backend.schemas.response import APIResponse
from backend.api.dependencies import require_super_admin
from backend.db.client import db_client
from typing import Dict
import re
router = APIRouter(prefix="/prompts", tags=["AI Prompts"])
class UpdatePromptRequest(BaseModel):
    body: str
@router.get("", response_model=APIResponse)
async def get_all_prompts(admin: dict = Depends(require_super_admin)):
    """
    Super Admin only: Get all active global prompts mapping to frontend PromptEntry.
    """
    prompts = await db_client.get_all_prompts()
    return APIResponse.create_success(data=prompts)
import string

ALLOWED_KEYS_BY_PROMPT = {
    "system_prompt": {
        "business_name",
        "business_type",
        "ai_tone",
        "services",
        "working_hours",
        "custom_instructions_section",
        "faqs_section",
        "state",
        "module",
        "greeting_message",
    }
}

SAFE_NEGATION_MARKERS = (
    "do not",
    "don't",
    "never",
    "must not",
    "cannot",
    "can't",
    "refuse",
    "block",
    "prevent",
    "disallow",
    "reject",
)

SEMANTIC_GUARDRAIL_PATTERNS = (
    (
        "instruction_hierarchy_bypass",
        re.compile(r"\b(ignore|disregard|override)\s+(all\s+)?(previous|prior|platform|developer|system|security)\s+instructions?\b"),
    ),
    (
        "hidden_prompt_disclosure",
        re.compile(r"\b(reveal|show|print|quote|summarize|expose)\s+(the\s+)?(hidden\s+)?(system|developer|security)\s+(prompt|instructions?|rules?)\b"),
    ),
    (
        "safety_control_bypass",
        re.compile(
            r"\b(disable|bypass|skip|turn\s+off|ignore)\s+(the\s+)?"
            r"(capability\s*gateway|confirmation|otp|verification|payment\s+safety|idempotency|tenant\s+isolation|"
            r"grounding|grounded\s+answer|response\s+envelope|quick\s+repl(?:y|ies))\b"
        ),
    ),
    (
        "cross_tenant_access",
        re.compile(r"\b(allow|enable|use|access|share|show)\s+(cross[-\s]?tenant|other\s+tenant|all\s+tenant|tenant\s+data\s+from\s+other)\b"),
    ),
    (
        "internal_identifier_exposure",
        re.compile(r"\b(expose|show|return|include|display)\s+(uuid|uuids|internal\s+ids?|database\s+ids?|item_id|service_id)\b"),
    ),
    (
        "ungrounded_business_facts",
        re.compile(r"\b(make\s+up|invent|hallucinate|guess)\s+(business\s+facts?|prices?|polic(?:y|ies)|hours?|availability|products?|services?)\b"),
    ),
    (
        "forced_tool_execution",
        re.compile(r"\b(force|always|automatically|auto)\s+(execute|call|run)\s+(backend\s+)?tools?\s+(without|bypassing|ignoring)\b"),
    ),
)


def _is_negated(text: str, start: int) -> bool:
    prefix = text[max(0, start - 40):start]
    return any(marker in prefix for marker in SAFE_NEGATION_MARKERS)


def validate_prompt_semantics(prompt_text: str) -> None:
    normalized = re.sub(r"\s+", " ", str(prompt_text or "").lower()).strip()
    for reason, pattern in SEMANTIC_GUARDRAIL_PATTERNS:
        match = pattern.search(normalized)
        if match and not _is_negated(normalized, match.start()):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Unsafe global prompt content rejected: "
                    f"{reason}. Global prompts cannot weaken platform safety, tenant isolation, "
                    "tool validation, grounding, confirmation, verification, payment/idempotency, "
                    "or response envelope rules."
                ),
            )

@router.put("/{prompt_name}", response_model=APIResponse)
async def update_global_prompt(
    prompt_name: str,
    req: UpdatePromptRequest,
    admin: dict = Depends(require_super_admin)
):
    """
    Super Admin only: Update a global system prompt template.
    Creates a new version, deactivates old ones, and logs an audit event.
    """
    # Validate prompt template variables using string.Formatter
    try:
        formatter = string.Formatter()
        used_keys = set()
        for _, field_name, _, _ in formatter.parse(req.body):
            if field_name is not None:
                base_key = field_name.split('.')[0].split('[')[0].strip()
                if base_key:
                    used_keys.add(base_key)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid prompt template syntax: {exc}"
        )

    allowed_keys = ALLOWED_KEYS_BY_PROMPT.get(prompt_name, set())
    invalid_keys = used_keys - allowed_keys
    if invalid_keys:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Prompt '{prompt_name}' contains disallowed format keys: {', '.join(sorted(invalid_keys))}. "
                f"Allowed keys: {', '.join(sorted(allowed_keys)) or 'None'}"
            )
        )
    validate_prompt_semantics(req.body)

    # Force tenant_id = None for Global base prompt overrides
    success = await db_client.set_prompt(tenant_id=None, prompt_name=prompt_name, prompt_text=req.body)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update prompt")
    # Generate audit log explicitly for prompt changes
    await db_client.write_audit_log(
        tenant_id=admin.get("tenant_id"), # Storing audit string under caller's tenant
        actor_id=admin.get("user_id"),
        action="UPDATE_GLOBAL_PROMPT",
        resource_type="prompt_registry",
        resource_id=None,
        details={"prompt_name": prompt_name}
    )
    return APIResponse.create_success(data={"success": True, "prompt_name": prompt_name})
