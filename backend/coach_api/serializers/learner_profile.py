def serialize_learner_profile_shell(
    profile,
    source,
    *,
    canonical_start_date,
    clean_text,
    format_date,
    student_activity_available,
    format_coach_rag_value,
) -> dict:
    """Serialize the stable Case File shell without performing another lookup."""
    def optional_date(value):
        return format_date(value) if value else None

    source_aptem = getattr(source, "aptem_id", None)
    profile_aptem = getattr(profile, "aptem_id", None)
    source_aptem_id = int(str(source_aptem).strip()) if student_activity_available(source_aptem) else None
    profile_aptem_id = int(str(profile_aptem).strip()) if student_activity_available(profile_aptem) else None
    identity_conflict = bool(source_aptem_id and profile_aptem_id and source_aptem_id != profile_aptem_id)
    aptem_id = None if identity_conflict else source_aptem_id or profile_aptem_id
    raw_kind = clean_text(getattr(source, "learner_type", None) or getattr(profile, "learner_type", None)).casefold()
    kind = "commercial" if raw_kind == "commercial" else "apprenticeship"
    return {
        "identity": {
            "learnerId": str(profile.id),
            "enrolmentId": str(profile.enrolment_id) if profile.enrolment_id else None,
            "aptemId": str(aptem_id) if aptem_id else None,
            "kind": kind,
            "source": "conflict" if identity_conflict else ("aptem" if aptem_id else "native"),
            "identityConflict": identity_conflict,
        },
        "profile": {
            "name": clean_text(getattr(profile, "full_name", None) or getattr(source, "username", None)) or None,
            "email": clean_text(getattr(profile, "email", None) or getattr(source, "email", None)) or None,
            "programme": clean_text(getattr(profile, "programme", None) or getattr(source, "programme", None)) or None,
            "cohort": clean_text(getattr(profile, "cohort", None) or getattr(source, "cohort", None)) or None,
            "group": clean_text(getattr(profile, "group_name", None) or getattr(source, "group", None)) or None,
            "employer": clean_text(getattr(source, "employer", None)) or None,
            "coachName": clean_text(getattr(profile, "coach_name", None) or getattr(source, "coach_name", None)) or None,
            "coachEmail": clean_text(getattr(profile, "coach_email", None) or getattr(source, "coach_email", None)) or None,
            "status": clean_text(getattr(profile, "programme_status", None) or getattr(source, "programme_status", None)) or None,
            "learnerStartDate": clean_text(getattr(source, "learner_start_date", None)) or None,
            "learnerEndDate": clean_text(getattr(source, "learner_end_date", None)) or None,
            "startDate": canonical_start_date,
            "plannedEndDate": optional_date(getattr(profile, "end_date", None) or getattr(source, "end_date", None)),
            "gatewayReviewDate": optional_date(getattr(profile, "gateway_review_date", None)),
            "coachRag": format_coach_rag_value(getattr(profile, "coach_rag", None)) or None,
        },
    }

