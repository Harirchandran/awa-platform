"""
Prompt Registry — Versioned Prompt Management
Central prompt storage with DB-backed versioning and tenant overrides.
Falls back to file-based resolver for tenant-specific prompts.
"""
from typing import Optional, Dict, Any
from backend.ai.prompt_resolver import resolve_prompt, DEFAULT_PROMPTS
from backend.ops.logger import logger
class PromptRegistry:
    """
    Central prompt registry with versioning support.
    Priority chain:
    1. DB-backed tenant prompt override (if available)
    2. File-based tenant prompt (tenants/<slug>/prompts/<name>.txt)
    3. Platform default prompt
    """
    # Default prompt templates with version tracking
    VERSIONED_PROMPTS: Dict[str, Dict[str, Any]] = {
        "intent_classifier": {
            "version": 1,
            "template": """You are an intent classifier. Given the user's message, classify their intent.
Respond with ONLY a JSON object: {"intent": "<intent_name>", "confidence": <0.0-1.0>}
Valid intents: booking, lead_capture, support, faq, greeting, unknown"""
        },
        "booking_confirm": {
            "version": 1,
            "template": "Your booking has been confirmed! We look forward to seeing you."
        },
        "booking_cancel": {
            "version": 1,
            "template": "Your booking has been cancelled. Feel free to rebook anytime."
        },
        "escalation": {
            "version": 1,
            "template": "I'm connecting you with a human agent. They'll be with you shortly."
        },
        "fallback": {
            "version": 1,
            "template": "I'm not sure I understand. Could you rephrase that?"
        },
        "greeting": {
            "version": 1,
            "template": "Hello! How can I help you today?"
        }
    }
    def get_prompt(
        self,
        prompt_name: str,
        tenant_slug: Optional[str] = None,
        config_data: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Resolve a prompt with full priority chain:
        1. Tenant config override (config_data['prompts'][name])
        2. File-based tenant override
        3. Versioned platform default
        4. Static fallback
        """
        # 1. Check tenant config override
        if config_data:
            prompts_override = config_data.get("prompts", {})
            if prompt_name in prompts_override:
                logger.info(f"Using config override for prompt '{prompt_name}'")
                return prompts_override[prompt_name]
        # 2. File-based tenant override
        if tenant_slug:
            file_prompt = resolve_prompt(tenant_slug, prompt_name)
            # Check if it's not just the default fallback
            if file_prompt != DEFAULT_PROMPTS.get("fallback", "") and file_prompt != DEFAULT_PROMPTS.get(prompt_name, ""):
                return file_prompt
        # 3. Versioned platform default
        if prompt_name in self.VERSIONED_PROMPTS:
            return self.VERSIONED_PROMPTS[prompt_name]["template"]
        # 4. Static fallback from prompt_resolver
        return DEFAULT_PROMPTS.get(prompt_name, DEFAULT_PROMPTS.get("fallback", "How can I help?"))
    def get_prompt_version(self, prompt_name: str) -> int:
        """Get the current version of a platform prompt."""
        entry = self.VERSIONED_PROMPTS.get(prompt_name)
        return entry["version"] if entry else 0
    def list_prompts(self) -> Dict[str, int]:
        """List all versioned platform prompts with their versions."""
        return {name: data["version"] for name, data in self.VERSIONED_PROMPTS.items()}
prompt_registry = PromptRegistry()