"""Display categories for reviews, independent of durable booking routing keys."""


def imported_review_category(review_type):
    # Reuse the authoritative imported aliases without making review_history's
    # ownership helpers depend on this presentation module during import.
    from learner_api.review_history import REVIEW_TYPES

    value = str(review_type or "").strip().casefold()
    if value in {item.casefold() for item in REVIEW_TYPES["monthly-coaching"]}:
        return "mcr"
    if value in {item.casefold() for item in REVIEW_TYPES["progress-review"]}:
        return "progress-review"
    return "review"


def review_event_category(event):
    """Prefer original type metadata; never change the stored event identity."""
    if event.get("importedReviewType") is not None:
        return imported_review_category(event["importedReviewType"])
    code = str(event.get("reviewTypeCode") or "").strip().lower()
    if code:
        return {"mcm": "mcr", "progress_review": "progress-review"}.get(code, "review")
    return str(event.get("source") or "").strip().lower()
