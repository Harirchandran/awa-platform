from typing import Any, Dict

from backend.tools.registry import ToolExecutionResult


import re

UUID_REGEX = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


async def resolve_booking_service(context: Any, service_name_input: str) -> Dict[str, Any]:
    val = str(service_name_input or "").strip()
    if not val or UUID_REGEX.match(val):
        return {"status": "unknown"}

    config_services = context.config.config_data.get("services", []) or []
    val_lower = val.lower()

    # 1. Exact case-insensitive name match
    name_matches = []
    for svc in config_services:
        name = str(svc.get("name") or "").strip().lower()
        if name == val_lower:
            name_matches.append(svc)
    if len(name_matches) == 1:
        return {"status": "success", "service": name_matches[0]}
    elif len(name_matches) > 1:
        return {"status": "ambiguous", "matches": name_matches}

    # 2. Normalized exact match
    def normalize_str(s: str) -> str:
        return re.sub(r"[^a-z0-9]", "", s.lower())

    val_norm = normalize_str(val)
    norm_matches = []
    for svc in config_services:
        name_norm = normalize_str(str(svc.get("name") or ""))
        if name_norm and name_norm == val_norm:
            norm_matches.append(svc)
    if len(norm_matches) == 1:
        return {"status": "success", "service": norm_matches[0]}
    elif len(norm_matches) > 1:
        return {"status": "ambiguous", "matches": norm_matches}

    # 3. Unique substring/fuzzy match
    substring_matches = []
    for svc in config_services:
        name = str(svc.get("name") or "").strip().lower()
        if val_lower in name:
            substring_matches.append(svc)
    if len(substring_matches) == 1:
        return {"status": "success", "service": substring_matches[0]}
    elif len(substring_matches) > 1:
        return {"status": "ambiguous", "matches": substring_matches}

    return {"status": "unknown"}


async def execute_booking_tool(context: Any, module: Any, booking_service: Any, logger: Any) -> ToolExecutionResult:
    """Persist booking to the bookings table."""
    from typing import Dict
    step_data = context.session_data.state.step_data
    service_name_input = step_data.get("service_name")
    selected_service = None
    config_services = context.config.config_data.get("services", []) or []

    if service_name_input:
        res = await resolve_booking_service(context, service_name_input)
        if res["status"] == "unknown":
            return ToolExecutionResult(
                tool_key="booking.execute",
                success=False,
                user_message="I couldn’t find that service. Please choose one of the available services.",
                validation_errors={"service_name": "Service not found in catalog."},
            )
        elif res["status"] == "ambiguous":
            names = ", ".join([str(s.get("name")) for s in res["matches"] if s.get("name")])
            return ToolExecutionResult(
                tool_key="booking.execute",
                success=False,
                user_message=f"I found multiple services matching '{service_name_input}': {names}. Please specify which one you would like.",
                validation_errors={"service_name": "Ambiguous service name."},
            )
        selected_service = res["service"]
    else:
        service_id_input = step_data.get("service_id")
        if service_id_input:
            for svc in config_services:
                if str(svc.get("id")).lower() == str(service_id_input).lower() or str(svc.get("name")).lower() == str(service_id_input).lower():
                    selected_service = svc
                    break

    if not selected_service:
        return ToolExecutionResult(
            tool_key="booking.execute",
            success=False,
            user_message="I couldn’t find that service. Please choose one of the available services.",
            validation_errors={"service_name": "Service not found in catalog."},
        )

    service_key = selected_service["id"]
    service_name = selected_service.get("name", "unknown")
    booking_date = module._coerce_booking_date(
        module._configured_temporal_value(context, "date"),
        tenant_config=context.config.config_data
    )
    booking_time = module._coerce_booking_time(
        module._configured_temporal_value(context, "time"),
        tenant_config=context.config.config_data
    )
    settings = context.config.config_data.get("booking_settings") or {}
    service_duration_mins = module._service_duration_minutes(selected_service)
    validation_error = module._validate_booking_rules(
        context=context,
        service_key=str(service_key),
        service_name=str(service_name),
        booking_date=booking_date,
        booking_time=booking_time,
        duration_mins=service_duration_mins,
        settings=settings,
    )
    if validation_error:
        return ToolExecutionResult(
            tool_key="booking.execute",
            success=False,
            user_message=validation_error,
            validation_errors={"booking": validation_error},
        )
    booking_data = dict(step_data)
    booking_data.setdefault("service_duration_mins", service_duration_mins)
    booking_data.setdefault("service_id", service_key)
    try:
        booking = await booking_service.create_booking(
            tenant_id=context.tenant.id,
            customer_id=context.user.id,
            conversation_id=context.session_data.conversation_id,
            service_name=service_name,
            booking_date=booking_date,
            booking_time=booking_time,
            booking_data=booking_data,
            idempotency_key=module._idempotency_key(context, service_name, booking_date, booking_time),
            source_channel=(context.incoming_message or {}).get("channel_type") or "public_chat",
            duration_mins=service_duration_mins,
            buffer_before_mins=module._int_setting(settings, "buffer_before_mins", 0),
            buffer_after_mins=module._int_setting(settings, "buffer_after_mins", 0),
            max_bookings_per_day=module._optional_int_setting(settings, "max_bookings_per_day"),
        )
        logger.info(f"Booking created: {booking.get('id')} for tenant {context.tenant.id}")
        return ToolExecutionResult(
            tool_key="booking.execute",
            success=True,
            user_message="I've booked that appointment.",
            created_resource_type="booking",
            created_resource_id=str(booking.get("id")),
            data={
                "service_name": service_name,
                "booking_date": booking_date.isoformat(),
                "booking_time": booking_time.isoformat() if booking_time else None,
                "status": booking.get("status"),
            },
        )
    except ValueError as e:
        safe_message = str(e) or "That appointment time is not available."
        logger.info("Booking unavailable for tenant %s: %s", context.tenant.id, safe_message)
        return ToolExecutionResult(
            tool_key="booking.execute",
            success=False,
            user_message=safe_message,
            validation_errors={"booking": safe_message},
        )
    except Exception as e:
        logger.error(f"Booking persistence failed: {e}")
        return ToolExecutionResult(
            tool_key="booking.execute",
            success=False,
            user_message="I couldn't create the booking right now. Please try again shortly.",
        )


async def execute_booking_update_tool(context: Any, module: Any, booking_service: Any, logger: Any) -> ToolExecutionResult:
    """Update or cancel an existing booking."""
    step_data = context.session_data.state.step_data
    if not step_data:
        step_data = context.session_data.state.get_task_step_data("booking")

    booking_id_str = step_data.get("booking_id")
    action = step_data.get("action")

    if not booking_id_str:
        return ToolExecutionResult(
            tool_key="booking.update",
            success=False,
            user_message="Missing booking ID.",
            validation_errors={"booking_id": "Booking ID is required"},
        )

    try:
        from uuid import UUID
        booking_id = UUID(booking_id_str)
    except Exception:
        return ToolExecutionResult(
            tool_key="booking.update",
            success=False,
            user_message="Invalid booking ID format.",
            validation_errors={"booking_id": "Invalid UUID format"},
        )

    if action == "cancel":
        try:
            success = await booking_service.cancel_booking(
                tenant_id=context.tenant.id,
                booking_id=booking_id
            )
            if success:
                logger.info(f"Booking cancelled: {booking_id} for tenant {context.tenant.id}")
                return ToolExecutionResult(
                    tool_key="booking.update",
                    success=True,
                    user_message="I've cancelled that booking.",
                    data={"booking_id": str(booking_id), "action": "cancel", "status": "cancelled"},
                )
            else:
                return ToolExecutionResult(
                    tool_key="booking.update",
                    success=False,
                    user_message="I couldn't find a booking with that ID to cancel.",
                )
        except Exception as e:
            logger.error(f"Booking cancellation failed: {e}")
            return ToolExecutionResult(
                tool_key="booking.update",
                success=False,
                user_message="I couldn't cancel the booking right now. Please try again shortly.",
            )

    elif action == "reschedule":
        new_date_val = step_data.get("new_date")
        new_time_val = step_data.get("new_time")

        if not new_date_val and not new_time_val:
            return ToolExecutionResult(
                tool_key="booking.update",
                success=False,
                user_message="Please specify a new date or time for the rescheduling.",
                validation_errors={"new_date": "New date or time required"},
            )

        from backend.modules.booking_module import booking_module

        try:
            booking_date_str = None
            if new_date_val:
                booking_date_obj = booking_module._coerce_booking_date(
                    new_date_val,
                    tenant_config=context.config.config_data
                )
                booking_date_str = booking_date_obj.isoformat()

            booking_time_str = None
            if new_time_val:
                booking_time_obj = booking_module._coerce_booking_time(
                    new_time_val,
                    tenant_config=context.config.config_data
                )
                if booking_time_obj:
                    booking_time_str = booking_time_obj.isoformat()
        except ValueError as e:
            return ToolExecutionResult(
                tool_key="booking.update",
                success=False,
                user_message=str(e),
                validation_errors={"new_date": str(e)},
            )

        try:
            success = await booking_service.update_booking(
                tenant_id=context.tenant.id,
                booking_id=booking_id,
                booking_date=booking_date_str,
                booking_time=booking_time_str,
            )
            if success:
                logger.info(f"Booking rescheduled: {booking_id} to date={booking_date_str} time={booking_time_str} for tenant {context.tenant.id}")
                return ToolExecutionResult(
                    tool_key="booking.update",
                    success=True,
                    user_message="I've rescheduled that booking.",
                    data={
                        "booking_id": str(booking_id),
                        "action": "reschedule",
                        "new_date": booking_date_str,
                        "new_time": booking_time_str,
                    },
                )
            else:
                return ToolExecutionResult(
                    tool_key="booking.update",
                    success=False,
                    user_message="I couldn't find a booking with that ID to reschedule.",
                )
        except ValueError as e:
            safe_message = str(e) or "That appointment time is not available."
            logger.info("Rescheduling unavailable for tenant %s: %s", context.tenant.id, safe_message)
            return ToolExecutionResult(
                tool_key="booking.update",
                success=False,
                user_message=safe_message,
                validation_errors={"booking": safe_message},
            )
        except Exception as e:
            logger.error(f"Booking reschedule failed: {e}")
            return ToolExecutionResult(
                tool_key="booking.update",
                success=False,
                user_message="I couldn't reschedule the booking right now. Please try again shortly.",
            )

    else:
        return ToolExecutionResult(
            tool_key="booking.update",
            success=False,
            user_message=f"Unknown action: {action}.",
            validation_errors={"action": "Invalid action value"},
        )
