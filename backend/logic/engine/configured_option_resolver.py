"""Pure prompt-scoped matching for configuration-owned workflow choices."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Collection, Mapping, Sequence
import unicodedata


@dataclass(frozen=True)
class ConfiguredOptionMatch:
    workflow: str
    field_updates: Mapping[str, Any]
    public_label: str
    evidence_source: str = "exact_configured_option"


def _normalize(value: Any) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).casefold().split())


def _unique_normalized_match(customer_text: str, options: Sequence[Mapping[str, Any]]) -> tuple[Mapping[str, Any], str] | None:
    """Resolve an exact label or a safe unique label prefix.

    The candidate set is configuration-owned and already scoped to the active
    workflow field. Prefix resolution deliberately requires at least three
    normalized characters and never guesses when more than one option matches.
    """
    normalized = _normalize(customer_text)
    if not normalized:
        return None
    exact = [option for option in options if _normalize(option.get("name")) == normalized]
    if len(exact) == 1:
        return exact[0], "exact_configured_option"
    if exact or len(normalized) < 3:
        return None
    prefixes = [option for option in options if _normalize(option.get("name")).startswith(normalized)]
    if len(prefixes) == 1:
        return prefixes[0], "unique_configured_prefix"
    return None


def _module_is_enabled(workflow: str, config_data: Mapping[str, Any]) -> bool:
    modules = config_data.get("modules")
    if not isinstance(modules, Mapping):
        return True
    module_config = modules.get(workflow)
    return not isinstance(module_config, Mapping) or module_config.get("enabled", True) is not False


def _unique_scalar_match(customer_text: str, options: Any) -> Any | None:
    if not isinstance(options, (list, tuple)):
        return None
    normalized = _normalize(customer_text)
    matches = [option for option in options if _normalize(option) == normalized]
    return matches[0] if len(matches) == 1 else None


def _module_config(config_data: Mapping[str, Any], workflow: str) -> Mapping[str, Any]:
    modules = config_data.get("modules")
    if not isinstance(modules, Mapping):
        return {}
    module_config = modules.get(workflow)
    return module_config if isinstance(module_config, Mapping) else {}


def resolve_configured_option(
    *,
    customer_text: str,
    active_workflow: str | None,
    requested_fields: Sequence[str],
    config_data: Mapping[str, Any],
    runtime_available_modules: Collection[str],
) -> ConfiguredOptionMatch | None:
    """Return one prompt-scoped configured choice without mutating input."""
    if not active_workflow or active_workflow not in runtime_available_modules:
        return None
    if not _module_is_enabled(active_workflow, config_data):
        return None

    normalized_text = _normalize(customer_text)
    requested = frozenset(requested_fields)
    if not normalized_text or not requested:
        return None

    if active_workflow == "booking" and "service_name" in requested:
        services = config_data.get("services")
        if not isinstance(services, (list, tuple)):
            return None
        candidates = [
            service
            for service in services
            if isinstance(service, Mapping)
            and service.get("enabled", True) is not False
            and service.get("name") not in (None, "")
        ]
        resolved = _unique_normalized_match(customer_text, candidates)
        if resolved is None:
            return None
        service, evidence_source = resolved
        canonical_name = str(service["name"])
        updates: dict[str, Any] = {"service_name": canonical_name}
        stable_id = service.get("id")
        if (
            isinstance(stable_id, str) and bool(stable_id.strip())
        ) or (
            isinstance(stable_id, int) and not isinstance(stable_id, bool)
        ):
            updates["service_id"] = stable_id
        return ConfiguredOptionMatch("booking", updates, canonical_name, evidence_source)

    if active_workflow == "order":
        order_config = _module_config(config_data, "order")
        option_keys = {
            "fulfillment_type": "fulfillment_types",
            "payment_method": "payment_methods",
        }
        for field_name, config_key in option_keys.items():
            if field_name not in requested:
                continue
            match = _unique_scalar_match(customer_text, order_config.get(config_key))
            if match is not None:
                return ConfiguredOptionMatch("order", {field_name: match}, str(match))
        return None

    if active_workflow == "support":
        fields = _module_config(config_data, "support").get("fields")
        if not isinstance(fields, (list, tuple)):
            return None
        matches: list[tuple[str, Any]] = []
        for field_definition in fields:
            if not isinstance(field_definition, Mapping):
                continue
            field_name = field_definition.get("key")
            if not isinstance(field_name, str) or field_name not in requested:
                continue
            option = _unique_scalar_match(customer_text, field_definition.get("options"))
            if option is not None:
                matches.append((field_name, option))
        if len(matches) != 1:
            return None
        field_name, option = matches[0]
        return ConfiguredOptionMatch("support", {field_name: option}, str(option))

    if active_workflow == "reviews" and "rating" in requested and normalized_text in {"1", "2", "3", "4", "5"}:
        rating = int(normalized_text)
        return ConfiguredOptionMatch("reviews", {"rating": rating}, normalized_text)

    return None


def resolve_exact_configured_option(
    *,
    customer_text: str,
    active_workflow: str | None,
    requested_fields: Sequence[str],
    config_data: Mapping[str, Any],
    runtime_available_modules: Collection[str],
) -> ConfiguredOptionMatch | None:
    """Compatibility entry point retaining exact-only behavior for callers that require it."""
    match = resolve_configured_option(
        customer_text=customer_text,
        active_workflow=active_workflow,
        requested_fields=requested_fields,
        config_data=config_data,
        runtime_available_modules=runtime_available_modules,
    )
    return match if match is not None and match.evidence_source == "exact_configured_option" else None
