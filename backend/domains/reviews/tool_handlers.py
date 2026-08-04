import json
from typing import Any, Dict, Optional

from backend.tools.registry import ToolExecutionResult

# Minimum valid positive_rating_threshold value — used for defensive clamping.
_THRESHOLD_MIN = 1
_THRESHOLD_MAX = 5
_THRESHOLD_DEFAULT = 4


async def execute_reviews_tool(context: Any, review_service: Any, logger: Any) -> ToolExecutionResult:
    step_data = context.session_data.state.step_data
    rating = _parse_rating(step_data.get("rating"))
    feedback = step_data.get("feedback") or step_data.get("feedback_text")
    reference = step_data.get("reference") or "conversation"

    # ------------------------------------------------------------------ #
    # Validation: reject an unparseable rating up-front so the LLM can   #
    # ask the customer to re-enter before any DB write occurs.            #
    # ------------------------------------------------------------------ #
    if step_data.get("rating") not in (None, "") and rating is None:
        return ToolExecutionResult(
            tool_key="reviews.execute",
            success=False,
            user_message="Please share a rating from 1 to 5.",
            validation_errors={"rating": "Rating must be a number from 1 to 5."},
        )

    # ------------------------------------------------------------------ #
    # Application-layer gate: resolve the tenant threshold BEFORE any DB  #
    # write.  This is a second line of defence; the DB layer also enforces#
    # the threshold in action_review_request, but we set the action name  #
    # here so that the URL-write branch in the DB layer is never reached  #
    # for low-rating submissions.                                         #
    # ------------------------------------------------------------------ #
    positive_threshold = _THRESHOLD_DEFAULT
    if rating is not None:
        try:
            settings = await review_service.get_review_settings(context.tenant.id)
            raw = settings.get("positive_rating_threshold")
            positive_threshold = (
                max(_THRESHOLD_MIN, min(_THRESHOLD_MAX, int(raw)))
                if raw is not None
                else _THRESHOLD_DEFAULT
            )
        except Exception as exc:
            # Non-fatal: fall back to the conservative default. A missing
            # settings row is an internal error, not a customer error.
            logger.warning(
                "Could not fetch review_settings for tenant %s; defaulting threshold to %d: %s",
                context.tenant.id,
                _THRESHOLD_DEFAULT,
                exc,
            )

    # Determine routing before any DB write.
    is_low_rating = rating is not None and rating < positive_threshold
    is_high_rating = rating is not None and rating >= positive_threshold

    try:
        review = await review_service.create_review_request(
            tenant_id=context.tenant.id,
            status="responded" if rating else "draft",
            source="manual",
            customer_id=context.user.id,
            conversation_id=context.session_data.conversation_id,
            metadata={
                "source": "concierge_tool",
                "reference": str(reference),
            },
            idempotency_key=f"concierge:{context.session_data.conversation_id}:review:{reference}",
        )
        if not review:
            return ToolExecutionResult(
                tool_key="reviews.execute",
                success=False,
                user_message="I couldn't save the review request with those details.",
            )

        if is_low_rating:
            # ------------------------------------------------------------------ #
            # LOW RATING PATH — strict escalation lockdown.                       #
            #                                                                     #
            # Use action="escalate_negative" which:                               #
            #   a) sets status = "negative_feedback_escalated"                   #
            #   b) does NOT enter the URL-write branch                            #
            #      (`action in {"submit_rating", "request_public_review"}`)       #
            #      in db_client.action_review_request, guaranteeing               #
            #      public_review_url is never written to the row.                 #
            # The rating and feedback are saved via a separate update call first  #
            # so the event timeline is fully populated.                           #
            # ------------------------------------------------------------------ #
            review = await review_service.update_review_request(
                context.tenant.id,
                review["id"],
                {
                    "rating": rating,
                    "feedback_text": str(feedback) if feedback else None,
                    "metadata": {
                        **(review.get("metadata") or {}),
                        "source": "concierge_tool",
                        "reference": str(reference),
                        "gate": "application_layer_escalation",
                        "threshold_at_submission": positive_threshold,
                    },
                },
            )
            review = await review_service.action_review_request(
                context.tenant.id,
                review["id"],
                action="escalate_negative",
                actor_id=None,
                rating=rating,
                feedback_text=str(feedback) if feedback else None,
                # Explicitly pass None — never pass a public URL on this path.
                public_review_url=None,
                metadata={
                    "source": "concierge_tool",
                    "reference": str(reference),
                    "gate": "application_layer_escalation",
                    "threshold_at_submission": positive_threshold,
                },
            )

        elif is_high_rating:
            # ------------------------------------------------------------------ #
            # HIGH RATING PATH — normal submit_rating flow.                       #
            #                                                                     #
            # Pass public_review_url=None to let the DB layer resolve it from    #
            # review_settings.public_review_url using its own threshold check.   #
            # This preserves the DB-layer guard as an independent second layer.  #
            # ------------------------------------------------------------------ #
            review = await review_service.action_review_request(
                context.tenant.id,
                review["id"],
                action="submit_rating",
                actor_id=None,
                rating=rating,
                feedback_text=str(feedback) if feedback else None,
                public_review_url=None,  # let DB layer resolve from settings
                metadata={"source": "concierge_tool", "reference": str(reference)},
            )

        elif feedback:
            # No rating supplied — save written feedback only.
            existing_metadata = review.get("metadata") or {}
            if isinstance(existing_metadata, str):
                try:
                    existing_metadata = json.loads(existing_metadata)
                except Exception:
                    existing_metadata = {}
            if not isinstance(existing_metadata, dict):
                existing_metadata = {}
            review = await review_service.update_review_request(
                context.tenant.id,
                review["id"],
                {
                    "feedback_text": str(feedback),
                    "metadata": {
                        **existing_metadata,
                        "source": "concierge_tool",
                        "reference": str(reference),
                    },
                },
            )

    except Exception as exc:
        logger.error(
            "Failed to persist review request",
            extra={"extra_data": {"reason_code": "review_persistence_failed", "error_type": type(exc).__name__}},
        )
        return ToolExecutionResult(
            tool_key="reviews.execute",
            success=False,
            user_message="I couldn't save the review right now. Please try again shortly.",
        )

    if not review:
        return ToolExecutionResult(
            tool_key="reviews.execute",
            success=False,
            user_message="I couldn't save the review with those details.",
        )

    logger.info(
        "Review request %s persisted for tenant %s (status=%s, low_rating_gate=%s)",
        review.get("id"),
        context.tenant.id,
        review.get("status"),
        is_low_rating,
    )

    user_message = _review_user_message(review, rating)

    # Strip the public URL from the returned data payload on the escalation
    # path — even if somehow the row ended up with a URL (e.g. pre-existing
    # value from a prior interaction), we must not surface it to the LLM.
    returned_public_url = None if is_low_rating else review.get("public_review_url")

    return ToolExecutionResult(
        tool_key="reviews.execute",
        success=True,
        user_message=user_message,
        created_resource_type="review_request",
        created_resource_id=str(review.get("id")),
        data={
            "rating": rating,
            "status": review.get("status"),
            "public_review_url": returned_public_url,
        },
    )


def _parse_rating(value: Any) -> Optional[int]:
    if value is None or str(value).strip() == "":
        return None
    try:
        rating = int(value)
    except (TypeError, ValueError):
        return None
    return rating if 1 <= rating <= 5 else None


def _review_user_message(review: Dict[str, Any], rating: Optional[int]) -> str:
    status = review.get("status")
    public_url = review.get("public_review_url")
    if status == "public_review_requested" and public_url:
        return (
            "Thanks for the positive feedback. If you're comfortable sharing it publicly, "
            f"please use this review link: {public_url}"
        )
    if status == "negative_feedback_escalated":
        return "Thanks for telling us. I've shared this feedback with the team so they can follow up."
    if rating:
        return "Thanks, I've saved your review."
    return "I've created the review request."
