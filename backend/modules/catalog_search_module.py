from __future__ import annotations

import re
from typing import Any, Dict, List

from backend.ai.security import sanitize_untrusted_text
from backend.core.context import ExecutionContext
from backend.domains.catalog.service import catalog_service
from backend.modules.base_module import BaseModule, ToolMetadata, ValidationResult
from backend.tools.registry import ToolExecutionResult


UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
OPAQUE_FIELD_RE = re.compile(r"\b(id|uuid|token|secret|signed|storage|url)\b", re.IGNORECASE)
MAX_RESULTS = 5


def _safe_text(value: Any, max_chars: int) -> str:
    text = sanitize_untrusted_text(value, max_chars=max_chars)
    text = UUID_RE.sub("[redacted]", text)
    text = URL_RE.sub("[redacted]", text)
    return text.strip()


def _safe_sku(value: Any) -> str:
    sku = _safe_text(value, max_chars=48)
    if not sku or UUID_RE.search(str(value or "")) or URL_RE.search(str(value or "")):
        return ""
    if OPAQUE_FIELD_RE.search(sku) or len(sku) > 40:
        return ""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._/-]{0,39}", sku):
        return ""
    return sku


def _description_snippet(value: Any) -> str:
    text = _safe_text(value, max_chars=180)
    if len(text) <= 140:
        return text
    return text[:137].rstrip() + "..."


def _in_stock(item: Dict[str, Any]) -> bool:
    if item.get("is_available") is False or item.get("enabled") is False:
        return False
    if item.get("stock_tracking_enabled") is True:
        try:
            return float(item.get("stock_quantity") or 0) > 0
        except (TypeError, ValueError):
            return False
    return True


def _safe_result(item: Dict[str, Any]) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "name": _safe_text(item.get("name") or "Item", max_chars=120) or "Item",
        "category": _safe_text(item.get("category_name") or "", max_chars=80),
        "price": item.get("price"),
        "description": _description_snippet(item.get("description") or ""),
        "in_stock": _in_stock(item),
        "availability": "in_stock" if _in_stock(item) else "unavailable",
    }
    sku = _safe_sku(item.get("sku"))
    if sku:
        result["sku"] = sku
    return {key: value for key, value in result.items() if value not in (None, "")}


def _matches_category(item: Dict[str, Any], category_name: str) -> bool:
    if not category_name:
        return True
    category = str(item.get("category_name") or "").strip().lower()
    return category_name in category or category in category_name


def _format_results_message(results: List[Dict[str, Any]], query: str) -> str:
    if not results:
        safe_query = _safe_text(query, max_chars=80) or "that item"
        return f"I couldn't find active available catalog items matching {safe_query}."
    lines = ["I found these catalog matches:"]
    for result in results:
        parts = [result["name"]]
        if result.get("sku"):
            parts.append(f"SKU {result['sku']}")
        if result.get("price") not in (None, ""):
            parts.append(f"INR {result['price']}")
        if result.get("category"):
            parts.append(result["category"])
        parts.append("in stock" if result.get("in_stock") else "not available")
        lines.append("- " + " - ".join(str(part) for part in parts if part not in (None, "")))
    return "\n".join(lines)


class CatalogSearchModule(BaseModule):
    def get_module_name(self) -> str:
        return "catalog_search"

    def get_tool_metadata(self, config: Dict[str, Any]) -> ToolMetadata:
        return ToolMetadata(
            risk_level="read",
            requires_confirmation=False,
            requires_verified_customer=False,
            idempotency_scope="none",
        )

    def get_required_fields(self, config: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [
            {"key": "query", "label": "Catalog search query", "type": "string", "required": True},
            {"key": "category_name", "label": "Optional category name", "type": "string", "required": False},
        ]

    def get_tool_schema(self, config: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Product/item name or SKU to search in the current tenant catalog.",
                },
                "category_name": {
                    "type": "string",
                    "description": "Optional public category name to narrow catalog search.",
                },
            },
            "required": ["query"],
        }

    def validate_input(
        self,
        field_def: Dict[str, Any],
        value: Any,
        tenant_config: Dict[str, Any] | None = None,
    ) -> ValidationResult:
        if field_def.get("key") == "query" and not str(value or "").strip():
            return ValidationResult(False, "Search query is required.")
        return ValidationResult(True)

    async def execute(self, context: ExecutionContext) -> ToolExecutionResult:
        step_data = context.session_data.state.get_task_step_data("order")
        query = str(step_data.get("query") or "").strip()
        category_name = str(step_data.get("category_name") or "").strip().lower()
        if not query:
            return ToolExecutionResult(
                tool_key="order.catalog_search",
                success=False,
                denied_reason="missing_query",
                user_message="Please tell me what catalog item to search for.",
            )

        rows = await catalog_service.list_items(
            context.tenant.id,
            search=query,
            active_only=True,
            available_only=True,
        )
        results: List[Dict[str, Any]] = []
        for row in rows or []:
            row_tenant_id = row.get("tenant_id")
            if row_tenant_id is not None and str(row_tenant_id) != str(context.tenant.id):
                continue
            if not _matches_category(row, category_name):
                continue
            safe = _safe_result(row)
            if safe.get("name"):
                results.append(safe)
            if len(results) >= MAX_RESULTS:
                break

        return ToolExecutionResult(
            tool_key="order.catalog_search",
            success=True,
            user_message=_format_results_message(results, query),
            data={
                "results": results,
                "result_count": len(results),
                "max_results": MAX_RESULTS,
            },
        )


catalog_search_module = CatalogSearchModule()
