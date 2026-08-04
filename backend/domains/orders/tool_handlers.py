from typing import Any, Dict, List, Optional

from backend.tools.registry import ToolExecutionResult


import re

UUID_REGEX = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _make_public_option(index: int, item: Dict[str, Any]) -> Dict[str, Any]:
    """Build a customer-visible option entry from a catalog item.

    Contains only index, name, and optionally a public SKU.
    No UUIDs, item_id, tenant_id, internal DB references, or signed URLs.
    """
    option: Dict[str, Any] = {
        "index": index,
        "name": str(item.get("name") or ""),
    }
    sku = str(item.get("sku") or "").strip()
    # Only include SKU if it is NOT a UUID (UUIDs are internal IDs in disguise)
    if sku and not UUID_REGEX.match(sku):
        option["sku"] = sku
    price = item.get("price")
    if price is not None:
        option["price"] = price
    return option


def build_pending_disambiguation(matches: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build the backend-only pending_disambiguation object.

    - ``options`` is the LLM/customer-visible list (index, name, optional safe SKU).
    - ``_backend_refs`` is a backend-only list of full item dicts, keyed by 1-based
      index.  It is NEVER forwarded to the LLM prompt, customer output, quick replies,
      or tool schema arguments.
    """
    options = [_make_public_option(i + 1, item) for i, item in enumerate(matches)]
    backend_refs = [{"index": i + 1, "item": matches[i]} for i in range(len(matches))]
    return {
        "options": options,
        "_backend_refs": backend_refs,
    }


def format_disambiguation_message(pending: Dict[str, Any]) -> str:
    """Format a customer-facing clarification message from pending_disambiguation.

    Contains only index numbers and item names — no UUIDs or internal IDs.
    """
    options = pending.get("options") or []
    lines = ["I found a few options. Which one would you like?"]
    for opt in options:
        name = str(opt.get("name") or "")
        idx = opt.get("index", "?")
        price = opt.get("price")
        price_str = f" ({price})" if price is not None else ""
        lines.append(f"{idx}. {name}{price_str}")
    lines.append("Reply with the number or name of your choice.")
    return "\n".join(lines)


def resolve_disambiguation_selection(
    message: str,
    pending: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Try to resolve a disambiguation pending-selection from the customer message.

    Returns the backend item dict for the selected option, or None if unresolved.
    Never searches the catalog DB for the customer message as an item name.
    """
    if not isinstance(pending, dict):
        return None
    backend_refs = pending.get("_backend_refs") or []
    options = pending.get("options") or []
    text = str(message or "").strip()
    if not text:
        return None

    # 1. Numeric index match
    if re.fullmatch(r"\d{1,2}", text):
        idx = int(text)
        for ref in backend_refs:
            if ref.get("index") == idx:
                return ref["item"]
        return None  # out-of-range number — no silent fallback

    # 2. Label/name match (case-insensitive exact)
    text_lower = text.lower()
    for opt in options:
        if str(opt.get("name") or "").lower() == text_lower:
            idx = opt["index"]
            for ref in backend_refs:
                if ref.get("index") == idx:
                    return ref["item"]

    # 3. Fuzzy label match (difflib) — against option names only
    from difflib import get_close_matches
    names = [str(opt.get("name") or "").lower() for opt in options]
    fuzzy = get_close_matches(text_lower, names, n=1, cutoff=0.7)
    if fuzzy:
        matched_name = fuzzy[0]
        for opt in options:
            if str(opt.get("name") or "").lower() == matched_name:
                idx = opt["index"]
                for ref in backend_refs:
                    if ref.get("index") == idx:
                        return ref["item"]

    return None


def _deduplicate_catalog_candidates(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return each physical catalog item once, preferring the DB record.

    The active in-memory order config is hydrated from the catalog DB, while
    this resolver also queries that DB directly.  Both views can therefore
    describe one physical item.  Identity, not its label or SKU, is the only
    safe deduplication key: two rows with the same name must still remain
    ambiguous.
    """
    unique: Dict[str, Dict[str, Any]] = {}
    without_identity: List[Dict[str, Any]] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        item_id = item.get("id")
        if item_id in (None, ""):
            # Do not collapse identity-less legacy entries by name; that could
            # hide a genuine catalog ambiguity.
            without_identity.append(item)
            continue
        key = str(item_id)
        existing = unique.get(key)
        if existing is None or item.get("source") == "catalog_db":
            unique[key] = item
    return [*unique.values(), *without_identity]


async def resolve_item_by_name_or_sku(context: Any, module: Any, catalog_service: Any, identifier: str) -> Dict[str, Any]:
    val = str(identifier or "").strip()
    if not val or UUID_REGEX.match(val):
        return {"status": "unknown"}

    candidates = []
    config_items = module.get_catalog_items(context.config.config_data, enabled_only=True)
    candidates.extend(config_items)

    try:
        db_items = await catalog_service.list_items(context.tenant.id)
        if db_items:
            for item in db_items:
                item_copy = dict(item)
                item_copy["source"] = "catalog_db"
                candidates.append(item_copy)
    except Exception:
        pass

    candidates = _deduplicate_catalog_candidates(candidates)
    val_lower = val.lower()

    # 1. Exact SKU match has precedence over a colliding product name.
    sku_matches = []
    for item in candidates:
        sku = str(item.get("sku") or "").strip().lower()
        if sku and sku == val_lower:
            sku_matches.append(item)
    if len(sku_matches) == 1:
        return {"status": "success", "item": sku_matches[0]}
    elif len(sku_matches) > 1:
        return {"status": "ambiguous", "matches": sku_matches}

    # 2. Exact case-insensitive name match
    name_matches = []
    for item in candidates:
        name = str(item.get("name") or "").strip().lower()
        if name == val_lower:
            name_matches.append(item)
    if len(name_matches) == 1:
        return {"status": "success", "item": name_matches[0]}
    elif len(name_matches) > 1:
        return {"status": "ambiguous", "matches": name_matches}

    # 3. Normalized exact name match
    def normalize_str(s: str) -> str:
        return re.sub(r"[^a-z0-9]", "", s.lower())

    val_norm = normalize_str(val)
    norm_matches = []
    for item in candidates:
        name_norm = normalize_str(str(item.get("name") or ""))
        if name_norm and name_norm == val_norm:
            norm_matches.append(item)
    if len(norm_matches) == 1:
        return {"status": "success", "item": norm_matches[0]}
    elif len(norm_matches) > 1:
        return {"status": "ambiguous", "matches": norm_matches}

    # 4. Unique substring/fuzzy match
    substring_matches = []
    for item in candidates:
        name = str(item.get("name") or "").strip().lower()
        if val_lower in name:
            substring_matches.append(item)
    if len(substring_matches) == 1:
        return {"status": "success", "item": substring_matches[0]}
    elif len(substring_matches) > 1:
        return {"status": "ambiguous", "matches": substring_matches}

    # Catalog services may provide a direct authoritative resolver in addition to
    # list_items.  Its result follows the same mapping contract in every runtime.
    if hasattr(catalog_service, "resolve_catalog_item"):
        try:
            item = await catalog_service.resolve_catalog_item(context.tenant.id, val)
            if isinstance(item, dict) and item:
                return {"status": "success", "item": item}
        except Exception:
            pass

    return {"status": "unknown"}


async def execute_order_tool(
    context: Any,
    module: Any,
    order_service: Any,
    catalog_service: Any,
    logger: Any,
) -> ToolExecutionResult:
    step_data = dict(context.session_data.state.step_data)

    # Guard: if pending_disambiguation is still set, the item is not resolved yet.
    # Re-emit the clarification message instead of attempting to execute.
    pending_disambig = step_data.get("pending_disambiguation")
    if isinstance(pending_disambig, dict) and pending_disambig.get("options"):
        clarification = format_disambiguation_message(pending_disambig)
        return ToolExecutionResult(
            tool_key="order.execute",
            success=False,
            user_message=clarification,
            validation_errors={"item_name": "Pending disambiguation must be resolved before order confirmation."},
        )

    order_config = module._order_config(context.config.config_data)
    requested_items = module._requested_items(step_data)
    resolved_items: List[Dict[str, Any]] = []
    currency = order_config.get("currency", "INR")
    fulfillment_type = str(step_data.get("fulfillment_type") or "pickup").lower()
    payment_method = str(step_data.get("payment_method") or "not_collected").lower()
    allowed_fulfillment = set(order_config.get("fulfillment_types") or ["pickup", "delivery"])

    if fulfillment_type not in allowed_fulfillment:
        return ToolExecutionResult(
            tool_key="order.execute",
            success=False,
            user_message="That fulfillment option is not available for this business.",
            validation_errors={"fulfillment_type": "Unsupported fulfillment option."},
        )
    allowed_payment_methods = set(order_config.get("payment_methods") or [])
    if payment_method != "not_collected" and allowed_payment_methods and payment_method not in allowed_payment_methods:
        return ToolExecutionResult(
            tool_key="order.execute",
            success=False,
            user_message="That payment method is not available for this business.",
            validation_errors={"payment_method": "Unsupported payment method."},
        )
    if fulfillment_type == "delivery" and not str(step_data.get("delivery_address") or "").strip():
        return ToolExecutionResult(
            tool_key="order.execute",
            success=False,
            user_message="I need a delivery address before placing a delivery order.",
            validation_errors={"delivery_address": "Delivery address is required for delivery orders."},
        )

    # Resolve name/SKU to internal IDs before service call
    resolved_items_cache = {}
    updated_requested_items = []
    for requested in requested_items:
        identifier = requested.get("item_id")
        if not identifier:
            return ToolExecutionResult(
                tool_key="order.execute",
                success=False,
                user_message="I couldn’t find that item in our catalog. Please check the name or choose from the menu.",
                validation_errors={"item_name": "Missing item identifier."},
            )

        res = await resolve_item_by_name_or_sku(context, module, catalog_service, str(identifier))
        if res["status"] == "unknown":
            return ToolExecutionResult(
                tool_key="order.execute",
                success=False,
                user_message="I couldn’t find that item in our catalog. Please check the name or choose from the menu.",
                validation_errors={"item_name": "Item not found in catalog."},
            )
        elif res["status"] == "ambiguous":
            # Store pending_disambiguation in session state (backend-only _backend_refs).
            # The options list exposed to the customer contains ONLY index, name, and
            # optionally a public SKU — no UUIDs, no item_id, no tenant_id.
            pending = build_pending_disambiguation(res["matches"])
            # Persist in scoped and legacy step_data
            module_name = module.get_module_name()
            scoped = context.session_data.state.scoped_step_data.get(module_name)
            if not isinstance(scoped, dict):
                scoped = {}
            scoped["pending_disambiguation"] = pending
            context.session_data.state.scoped_step_data[module_name] = scoped
            context.session_data.state.step_data["pending_disambiguation"] = pending
            clarification = format_disambiguation_message(pending)
            return ToolExecutionResult(
                tool_key="order.execute",
                success=False,
                user_message=clarification,
                validation_errors={"item_name": "Ambiguous item name."},
            )

        resolved_item = res["item"]
        new_requested = dict(requested)
        new_requested["item_id"] = resolved_item["id"]
        updated_requested_items.append(new_requested)
        resolved_items_cache[str(resolved_item["id"])] = resolved_item

    requested_items = updated_requested_items

    for requested in requested_items:
        item = resolved_items_cache.get(str(requested.get("item_id")))
        if not item:
            item = module.resolve_item(requested.get("item_id"), context.config.config_data)
        if not item:
            item = await catalog_service.resolve_catalog_item(context.tenant.id, requested.get("item_id"))
        if not item:
            logger.warning("Order execution blocked: item not found")
            return ToolExecutionResult(
                tool_key="order.execute",
                success=False,
                user_message="I couldn't find that item in the active catalog. Please choose an available item.",
                validation_errors={"item_id": "Catalog item is unavailable or inactive."},
            )
        quantity = module._coerce_quantity(requested.get("quantity"))
        unit_price = float(item.get("price") or 0)
        line_total = unit_price * quantity
        currency = item.get("currency") or currency
        resolved_items.append(
            {
                "item": item,
                "quantity": quantity,
                "notes": requested.get("notes") or step_data.get("notes"),
                "unit_price": unit_price,
                "line_total": line_total,
            }
        )
    order_items = [module._order_item_snapshot(entry) for entry in resolved_items]
    subtotal = sum(float(entry["line_total"]) for entry in resolved_items)
    tax_amount = module._coerce_money(order_config.get("tax_amount"))
    delivery_fee = module._coerce_money(order_config.get("delivery_fee")) if fulfillment_type == "delivery" else 0.0
    total_amount = subtotal + tax_amount + delivery_fee
    customer_snapshot = {
        "name": step_data.get("customer_name") or getattr(context.user, "name", None),
        "phone": step_data.get("phone") or getattr(context.user, "phone_number", None),
    }
    channel_type = (context.incoming_message or {}).get("channel_type") or "whatsapp"
    try:
        order = await order_service.create_order(
            tenant_id=context.tenant.id,
            customer_id=context.user.id,
            conversation_id=context.session_data.conversation_id,
            channel_type=channel_type,
            status="new",
            currency=currency,
            subtotal_amount=subtotal,
            total_amount=total_amount,
            fulfillment_type=fulfillment_type,
            payment_method=payment_method,
            payment_status="not_collected",
            customer_snapshot=customer_snapshot,
            delivery_address=step_data.get("delivery_address"),
            order_data={
                **step_data,
                "tax_amount": tax_amount,
                "delivery_fee": delivery_fee,
            },
            items=order_items,
            idempotency_key=module._idempotency_key(context),
        )
        context.session_data.state.step_data["order_id"] = str(order.get("id"))
        logger.info(f"Order created: {order.get('id')} for tenant {context.tenant.id}")
        return ToolExecutionResult(
            tool_key="order.execute",
            success=True,
            user_message="I've placed the order.",
            created_resource_type="order",
            created_resource_id=str(order.get("id")),
            data={
                "order_id": str(order.get("id")),
                "item_count": len(order_items),
                "subtotal_amount": subtotal,
                "total_amount": total_amount,
                "currency": currency,
                "fulfillment_type": fulfillment_type,
                "payment_status": order.get("payment_status"),
            },
        )
    except ValueError as e:
        safe_message = str(e) or "I couldn't place the order with those catalog details."
        logger.info("Order unavailable for tenant %s: %s", context.tenant.id, safe_message)
        return ToolExecutionResult(
            tool_key="order.execute",
            success=False,
            user_message=safe_message,
            validation_errors={"order": safe_message},
        )
    except Exception as e:
        logger.error(f"Order persistence failed: {e}")
        return ToolExecutionResult(
            tool_key="order.execute",
            success=False,
            user_message="I couldn't place the order right now. Please try again shortly.",
        )
