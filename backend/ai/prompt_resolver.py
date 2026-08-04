"""
Tenant Prompt Resolver (Part XIV §61)
Loads tenant-specific prompt overrides with fallback to platform defaults.
"""
import os
from typing import Optional
from backend.ops.logger import logger
# Base directory for tenant files
TENANTS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "tenants")
# Platform default prompts
DEFAULT_PROMPTS = {
    "system_prompt": """You are a helpful WhatsApp business assistant.
Your goal is to help customers with their requests professionally and efficiently.
Be concise, friendly, and always ask for clarification if unsure.""",
    "greeting": "Hello! How can I help you today?",
    "fallback": "I'm not sure I understand. Could you rephrase that?",
    "escalation": "I'm connecting you with a human agent. They'll be with you shortly.",
    "booking_confirm": "Your booking has been confirmed! We look forward to seeing you.",
    "booking_cancel": "Your booking has been cancelled. Feel free to rebook anytime."
}
def resolve_prompt(tenant_slug: Optional[str], prompt_name: str) -> str:
    """
    Load a prompt file for a tenant with fallback to platform defaults.
    Priority:
    1. tenants/<slug>/prompts/<prompt_name>.txt (if exists)
    2. DEFAULT_PROMPTS[prompt_name] (platform fallback)
    Path traversal protection is enforced.
    Args:
        tenant_slug: The tenant's unique slug (e.g. "smiledent_clinic")
        prompt_name: Name of the prompt file without extension (e.g. "system_prompt")
    Returns:
        The prompt text content
    """
    # If no slug, return default
    if not tenant_slug:
        return DEFAULT_PROMPTS.get(prompt_name, DEFAULT_PROMPTS["fallback"])
    # Path traversal protection
    safe_slug = os.path.basename(tenant_slug)  # strips any ../
    safe_name = os.path.basename(prompt_name)   # strips any ../
    if safe_slug != tenant_slug or safe_name != prompt_name:
        logger.warning(f"Path traversal attempt blocked: slug={tenant_slug}, prompt={prompt_name}")
        return DEFAULT_PROMPTS.get(prompt_name, DEFAULT_PROMPTS["fallback"])
    # Try tenant-specific file
    tenant_prompt_path = os.path.join(TENANTS_DIR, safe_slug, "prompts", f"{safe_name}.txt")
    tenant_prompt_path = os.path.normpath(tenant_prompt_path)
    # Ensure resolved path is still within TENANTS_DIR
    if not tenant_prompt_path.startswith(os.path.normpath(TENANTS_DIR)):
        logger.warning(f"Path traversal attempt blocked: resolved to {tenant_prompt_path}")
        return DEFAULT_PROMPTS.get(prompt_name, DEFAULT_PROMPTS["fallback"])
    if os.path.isfile(tenant_prompt_path):
        try:
            with open(tenant_prompt_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    logger.info(f"Loaded custom prompt '{prompt_name}' for tenant '{safe_slug}'")
                    return content
        except Exception as e:
            logger.error(f"Error reading prompt file {tenant_prompt_path}: {e}")
    # Fallback to platform default
    return DEFAULT_PROMPTS.get(prompt_name, DEFAULT_PROMPTS["fallback"])
def list_tenant_prompts(tenant_slug: str) -> list:
    """List all custom prompt files for a tenant."""
    safe_slug = os.path.basename(tenant_slug)
    prompts_dir = os.path.join(TENANTS_DIR, safe_slug, "prompts")
    if not os.path.isdir(prompts_dir):
        return []
    return [
        f.replace(".txt", "")
        for f in os.listdir(prompts_dir)
        if f.endswith(".txt")
    ]