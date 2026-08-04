from typing import Any


async def execute_faq_tool(context: Any, logger: Any) -> bool:
    logger.info(
        f"FAQ execution skipped for tenant {context.tenant.id}; FAQ answers are handled by retrieval context"
    )
    return False
