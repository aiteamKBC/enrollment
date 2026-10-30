"""Read-only preview of Aptem reviews against Curriculum's existing occurrences.

An imported review has no native occurrence number. A matching date is useful
evidence, but cannot establish that identity or authorize a later adoption.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import connections

from curriculum_api import review_instances, review_types, schema_gate
from learner_api.review_history import REVIEW_TYPES, _serialize_review

from .models import CoachCalendarEvent


PR = "progress_review"
MCM = "mcm"
FAMILIES = {
    **{name.casefold(): PR for name in REVIEW_TYPES["progress-review"]},
    **{name.casefold(): MCM for name in REVIEW_TYPES["monthly-coaching"]},
}
SUMMARY_KEYS = {
    "HISTORICAL_COMPLETED": "historicalCompleted",
    "PROTECTED_AWAITING_SIGNATURE": "protectedAwaitingSignature",
    "PROTECTED_IN_PROGRESS": "protectedInProgress",
    "FUTURE_SCHEDULED_READY_FOR_ADOPTION": "scheduledReady",
    "FUTURE_SCHEDULED_NATIVE_ALREADY_EXISTS": "scheduledNativeExists",
    "FUTURE_SCHEDULED_BOOKING_UNRESOLVED": "scheduledBookingUnresolved",
    "FUTURE_SCHEDULED_OCCURRENCE_UNRESOLVED": "scheduledOccurrenceUnresolved",
    "FUTURE_NOT_SCHEDULED_READY_FOR_NATIVE": "notScheduledReady",
    "NATIVE_ALREADY_EXISTS": "nativeAlreadyExists",
    "CONFLICT": "conflicts",
    "MISSING_RECURRENCE_INPUT": "missingInputs",
    "UNSUPPORTED": "unsupported",
}

REASON_DETAILS = {
    "MISSING_ENROLMENT_SOURCE": "No enrolment.Created_users row resolves from this learner profile.",
    "MISSING_START_DATE": "The authoritative enrolment.Created_users.Learner_start_date is empty.",
    "INVALID_START_DATE": "The authoritative Learner_start_date cannot be parsed.",
    "MISSING_CURRICULUM_PROGRAMME": "The learner programme does not resolve to a Curriculum programme.",
    "MISSING_WINDOW_START": "Neither the enrolment schedule start nor its learner-profile mirror is available.",
    "MISSING_WINDOW_END": "Neither the enrolment schedule end nor its learner-profile mirror is available.",
    "INVALID_SCHEDULE_WINDOW": "The schedule end is not after its start.",
    "NO_APPLICABLE_NATIVE_REVIEW_TEMPLATE": "No enabled PR/MCM template applies to this learner's placement and status.",
    "MISSING_FAMILY_TEMPLATE": "No applicable enabled native template exists for this imported review family.",
    "CONFLICT_TEMPLATE_AMBIGUOUS": "More than one applicable native template exists for this review family.",
    "CONFLICT_OCCURRENCE_AMBIGUOUS": "More than one expected native occurrence is a candidate.",
    "CONFLICT_DATE_ONLY_MATCH": "The dates agree, but no durable or proven one-to-one occurrence identity links them.",
    "OCCURRENCE_UNRESOLVED": "No canonical native occurrence can be established for this imported review.",
    "BOOKING_UNRESOLVED": "A scheduled import has no durably identified existing calendar booking.",
    "CONFLICT_CALENDAR_ASSOCIATION": "Existing calendar and native review associations disagree or are duplicated.",
    "CONFLICT_OWNERSHIP": "The booking, native instance, or imported Aptem identity does not belong to this learner and coach.",
    "CONFLICT_MULTIPLE_NATIVE_OCCURRENCES": "Multiple native instances carry the same canonical occurrence identity.",
    "CONFLICT_MULTIPLE_IMPORTED_REVIEWS": "Multiple imported reviews claim one canonical occurrence identity.",
    "CONFLICT_IMPORT_FAMILY": "An unresolved imported review in this family may own this native occurrence.",
    "CONFLICT_PAST_OR_UNSUPPORTED_STATUS": "The imported status or planned date cannot enter future reconciliation.",
    "HISTORICAL_COMPLETED": "Completed Aptem history remains imported and immutable.",
    "PROTECTED_AWAITING_SIGNATURE": "An Aptem submission is awaiting signatures and is not eligible for adoption.",
    "PROTECTED_IN_PROGRESS": "Aptem work is in progress and is not eligible for adoption.",
    "NATIVE_ALREADY_EXISTS": "The canonical native occurrence already has an instance.",
    "UNSUPPORTED_IMPORT_TYPE": "This imported review type is outside the supported PR/MCM families.",
}

REASON_CODES = {
    "missing_created_users_row": "MISSING_ENROLMENT_SOURCE",
    "missing_start_date": "MISSING_START_DATE",
    "invalid_start_date": "INVALID_START_DATE",
    "missing_curriculum_programme": "MISSING_CURRICULUM_PROGRAMME",
    "missing_window_start": "MISSING_WINDOW_START",
    "missing_window_end": "MISSING_WINDOW_END",
    "invalid_schedule_window": "INVALID_SCHEDULE_WINDOW",
    "no_applicable_native_review_template": "NO_APPLICABLE_NATIVE_REVIEW_TEMPLATE",
    "missing_family_template": "MISSING_FAMILY_TEMPLATE",
    "multiple_applicable_native_templates": "CONFLICT_TEMPLATE_AMBIGUOUS",
    "multiple_candidate_occurrences": "CONFLICT_OCCURRENCE_AMBIGUOUS",
    "no_durable_occurrence_mapping": "OCCURRENCE_UNRESOLVED",
    "booking_not_identified": "BOOKING_UNRESOLVED",
    "calendar_native_identity_conflict": "CONFLICT_CALENDAR_ASSOCIATION",
    "calendar_occurrence_not_expected": "CONFLICT_CALENDAR_ASSOCIATION",
    "calendar_review_instance_missing_or_manual": "CONFLICT_CALENDAR_ASSOCIATION",
    "native_calendar_association_conflict": "CONFLICT_CALENDAR_ASSOCIATION",
    "conflicting_native_or_calendar_rows": "CONFLICT_CALENDAR_ASSOCIATION",
    "unlinked_existing_booking": "CONFLICT_CALENDAR_ASSOCIATION",
    "multiple_exact_bookings_or_occurrences": "CONFLICT_CALENDAR_ASSOCIATION",
    "booking_ownership_mismatch": "CONFLICT_OWNERSHIP",
    "aptem_learner_identity_conflict": "CONFLICT_OWNERSHIP",
    "multiple_native_instances": "CONFLICT_MULTIPLE_NATIVE_OCCURRENCES",
    "multiple_imported_reviews": "CONFLICT_MULTIPLE_IMPORTED_REVIEWS",
    "unreconciled_imported_family": "CONFLICT_IMPORT_FAMILY",
    "historical_import_date_collision": "CONFLICT_DATE_ONLY_MATCH",
    "unsupported_status_or_past_open_review": "CONFLICT_PAST_OR_UNSUPPORTED_STATUS",
}


def _reason_code(classification, reason, *, candidate_count=0):
    if reason == "no_durable_occurrence_mapping":
        return "CONFLICT_DATE_ONLY_MATCH" if candidate_count == 1 else "OCCURRENCE_UNRESOLVED"
    return REASON_CODES.get(reason) or {
        "HISTORICAL_COMPLETED": "HISTORICAL_COMPLETED",
        "PROTECTED_AWAITING_SIGNATURE": "PROTECTED_AWAITING_SIGNATURE",
        "PROTECTED_IN_PROGRESS": "PROTECTED_IN_PROGRESS",
        "FUTURE_SCHEDULED_BOOKING_UNRESOLVED": "BOOKING_UNRESOLVED",
        "FUTURE_SCHEDULED_OCCURRENCE_UNRESOLVED": "OCCURRENCE_UNRESOLVED",
        "FUTURE_SCHEDULED_NATIVE_ALREADY_EXISTS": "NATIVE_ALREADY_EXISTS",
        "NATIVE_ALREADY_EXISTS": "NATIVE_ALREADY_EXISTS",
        "UNSUPPORTED": "UNSUPPORTED_IMPORT_TYPE",
    }.get(classification, "OCCURRENCE_UNRESOLVED")


def _proven_history_links(imported_rows, occurrences, template_family_counts, today):
    """Prove an ordered full-prefix match; never infer identity from one date."""
    links = {}
    by_family = defaultdict(list)
    for source in imported_rows:
        review = _serialize_review(source, {})
        family = FAMILIES.get(str(review.get("type") or "").strip().casefold())
        if family:
            by_family[family].append((source, review))
    for family, rows in by_family.items():
        if template_family_counts.get(family) != 1:
            continue
        expected = sorted(
            (row for row in occurrences if row.get("reviewTypeCode") == family
             and row.get("occurrenceSource", "generated") == "generated"),
            key=lambda row: row["occurrenceNumber"],
        )
        past = [row for row in expected if row["targetDate"] < today]
        completed = sorted(
            ((source, _imported_date(source.get("planned_scheduled_date"), review.get("plannedDate")))
             for source, review in rows if review["status"] == "completed"),
            key=lambda pair: (pair[1] or "", pair[0]["id"]),
        )
        if not past or len(completed) != len(past) or any(
            planned != _iso(occurrence["targetDate"])
            for (_, planned), occurrence in zip(completed, past)
        ):
            continue
        # An open imported review among the historical slots makes the source
        # sequence incomplete even when Completed dates happen to line up.
        if any(review["status"] not in {"completed", "scheduled", "not-scheduled"} or
               review["status"] != "completed" and (
                   not _imported_date(source.get("planned_scheduled_date"), review.get("plannedDate"))
                   or _imported_date(source.get("planned_scheduled_date"), review.get("plannedDate")) < today.isoformat()
               ) for source, review in rows):
            continue
        family_links = {
            source["id"]: (occurrence["reviewTemplateId"], occurrence["occurrenceNumber"])
            for (source, _planned), occurrence in zip(completed, past)
        }
        future = [row for row in expected if row["targetDate"] >= today]
        future_imports = sorted(
            ((source, _imported_date(source.get("planned_scheduled_date"), review.get("plannedDate")))
             for source, review in rows if review["status"] in {"scheduled", "not-scheduled"}),
            key=lambda pair: (pair[1] or "", pair[0]["id"]),
        )
        if len(future_imports) <= len(future) and all(
            planned == _iso(occurrence["targetDate"])
            for (_, planned), occurrence in zip(future_imports, future)
        ):
            family_links.update({
                source["id"]: (occurrence["reviewTemplateId"], occurrence["occurrenceNumber"])
                for (source, _planned), occurrence in zip(future_imports, future)
            })
        links.update(family_links)
    return links


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value or None


def _imported_date(value, fallback):
    if isinstance(value, datetime):
        return _iso(value.astimezone(ZoneInfo(settings.TIME_ZONE)).date()) if value.tzinfo else _iso(value.date())
    return _iso(value) if isinstance(value, date) else fallback


def _booking_payload(row):
    """Existing booking identifiers and schedule, without changing the row."""
    if row is None:
        return None
    return {
        "calendarEventId": row.id,
        "eventKey": row.event_key,
        "reviewInstanceId": row.review_instance_id or None,
        "reviewTemplateId": row.review_template_id or None,
        "occurrenceNumber": row.occurrence_number,
        "operationId": str(row.operation_id) if row.operation_id else None,
        "idempotencyKey": row.idempotency_key or None,
        "graphEventId": row.graph_event_id or None,
        "graphOrganizerEmail": row.graph_organizer_email or None,
        "graphWebLink": row.graph_web_link or None,
        "meetingLink": row.meeting_link or None,
        "meetingProvider": row.meeting_provider or None,
        "scheduledDate": _iso(row.scheduled_date),
        "scheduledTime": _iso(row.scheduled_time),
        "durationMinutes": row.duration_minutes,
        "learnerId": row.learner_id,
        "coachEmail": row.owner_email,
        "syncState": row.sync_state,
    }


def _imported_booking_rows(review_row, family, calendar_rows, learner):
    aptem_id = str(review_row.get("aptem_review_id") or "").strip()
    imported_pk = str(review_row["id"])
    exact = []
    supporting = []
    metadata = review_row.get("review_data") or {}
    graph_id = str(metadata.get("graph_event_id") or "").strip() if isinstance(metadata, dict) else ""
    organizer = str(metadata.get("graph_organizer_email") or "").strip().casefold() if isinstance(metadata, dict) else ""
    for row in calendar_rows:
        key_parts = (row.idempotency_key or "").split(":")
        explicit_import = bool(
            len(key_parts) == 6 and key_parts[0] == "learner-book"
            and key_parts[1] == ("mcm" if family == MCM else "progress-review")
            and key_parts[5] == imported_pk
            and key_parts[3] == str(getattr(learner, "enrolment_id", ""))
        )
        graph_match = bool(
            graph_id and organizer and row.graph_event_id == graph_id
            and (row.graph_organizer_email or "").casefold() == organizer
        )
        if (aptem_id and row.event_key == f"imported-review:{aptem_id}") or explicit_import or graph_match:
            exact.append(row)
        elif (
            row.learner_id == learner.id
            and row.event_type == ("mcr" if family == MCM else "progress-review")
            and row.scheduled_date == review_row.get("planned_date")
        ):
            supporting.append(row)
    return exact, supporting


def _identity_for_booking(row, expected, native_by_id):
    """Resolve only native identifiers, never a calendar date or sequence."""
    identities = set()
    if row.review_template_id and row.occurrence_number is not None:
        identities.add((row.review_template_id, row.occurrence_number))
    if row.review_instance_id:
        instance = native_by_id.get(row.review_instance_id)
        if instance and instance.get("occurrence_number") is not None:
            identities.add((instance["review_template_id"], instance["occurrence_number"]))
        else:
            return None, "calendar_review_instance_missing_or_manual"
    if len(identities) > 1:
        return None, "calendar_native_identity_conflict"
    identity = next(iter(identities), None)
    if identity and identity not in expected and not (
        row.review_instance_id and row.review_instance_id in native_by_id
    ):
        return None, "calendar_occurrence_not_expected"
    return identity, None


def classify_aptem_review_preview(
    *, learner, imported_rows, occurrences, native_rows, calendar_rows,
    templates, programme_id, anchor, missing_reason=None, owner_email, today,
    template_family_counts=None, window_start=None,
):
    """Pure classification of previously read rows. No creation or sync calls."""
    expected = {
        (row["reviewTemplateId"], row["occurrenceNumber"]): row
        for row in occurrences
        if row.get("occurrenceSource", "generated") == "generated"
        and row.get("reviewTypeCode") in {PR, MCM}
        and row.get("occurrenceNumber") is not None
        and row["targetDate"] >= max(today, window_start or today)
    }
    if template_family_counts is None:
        template_family_counts = Counter()
        for family in (PR, MCM):
            template_family_counts[family] = len({
                row["reviewTemplateId"] for row in occurrences
                if row.get("reviewTypeCode") == family
            })
    # The builder supplies template counts. The occurrence fallback above only
    # serves pure classification callers with synthetic fixtures.
    proven_links = _proven_history_links(imported_rows, occurrences, template_family_counts, today)
    all_occurrences = {
        (row["reviewTemplateId"], row["occurrenceNumber"]): row
        for row in occurrences if row.get("occurrenceSource", "generated") == "generated"
        and row.get("occurrenceNumber") is not None
    }
    native = defaultdict(list)
    native_by_id = {}
    for row in native_rows:
        native_by_id[row["id"]] = row
        if row.get("occurrence_number") is not None:
            native[(row["review_template_id"], row["occurrence_number"])].append(row)
    template_by_id = {row["id"]: row for row in templates}
    expected_by_date = defaultdict(list)
    for identity, row in expected.items():
        expected_by_date[(row["reviewTypeCode"], _iso(row["targetDate"]))].append(identity)
    items = []
    exact_import_links = set()
    historical_future_dates = set()
    owner = owner_email.casefold()

    for source_row in imported_rows:
        review = _serialize_review(source_row, {})
        family = FAMILIES.get(str(review.get("type") or "").strip().casefold())
        status = review.get("status")
        planned = _imported_date(source_row.get("planned_scheduled_date"), review.get("plannedDate"))
        completed = _imported_date(source_row.get("completed_date"), review.get("completedDate"))
        review_date = date.fromisoformat(planned) if planned else None
        row = {**source_row, "planned_date": review_date}
        exact, supporting = _imported_booking_rows(row, family, calendar_rows, learner) if family else ([], [])
        candidates = expected_by_date.get((family, planned), []) if family else []
        exact_ids = set()
        link_error = None
        for booking in exact:
            identity, error = _identity_for_booking(booking, expected, native_by_id)
            if error:
                link_error = error
            if identity:
                exact_ids.add(identity)
            if booking.owner_email.casefold() != owner or booking.learner_id != learner.id:
                link_error = "booking_ownership_mismatch"
        identity = next(iter(exact_ids)) if len(exact_ids) == 1 else proven_links.get(source_row["id"])
        if len(exact_ids) > 1 or len(exact) > 1:
            link_error = "multiple_exact_bookings_or_occurrences"
        if exact_ids and proven_links.get(source_row["id"]) and identity != proven_links[source_row["id"]]:
            link_error = "calendar_native_identity_conflict"
        matched = expected.get(identity) if identity else None
        native_matches = native.get(identity, []) if identity else []
        if len(native_matches) > 1:
            link_error = "multiple_native_instances"
        if any(record.get("learner_id") != learner.id for record in native_matches):
            link_error = "booking_ownership_mismatch"
        if matched and native_matches and exact:
            linked = native_matches[0].get("calendar_event_id")
            if linked and linked != exact[0].id:
                link_error = "native_calendar_association_conflict"
        source_data = source_row.get("review_data") or {}
        source_aptem_id = source_data.get("aptem_learner_id") if isinstance(source_data, dict) else None
        if source_aptem_id and str(source_aptem_id) != str(getattr(learner, "effective_aptem_id", "")):
            link_error = "aptem_learner_identity_conflict"
        if status == "completed":
            classification = "HISTORICAL_COMPLETED"
            if family and planned and planned >= today.isoformat():
                historical_future_dates.add((family, planned))
        elif status == "awaiting-signature":
            classification = "PROTECTED_AWAITING_SIGNATURE"
        elif status == "in-progress":
            classification = "PROTECTED_IN_PROGRESS"
        elif link_error:
            classification = "CONFLICT"
        elif not family:
            classification = "UNSUPPORTED"
        elif native_matches and identity and (exact or source_row["id"] in proven_links):
            classification = (
                "FUTURE_SCHEDULED_NATIVE_ALREADY_EXISTS" if status == "scheduled"
                else "NATIVE_ALREADY_EXISTS"
            )
        elif status not in {"scheduled", "not-scheduled"} or not review_date or review_date < today:
            classification = "CONFLICT"
            link_error = "unsupported_status_or_past_open_review"
        elif template_family_counts.get(family, 0) > 1:
            classification = "CONFLICT"
            link_error = "multiple_applicable_native_templates"
        elif not template_family_counts.get(family, 0) and not missing_reason:
            classification = "MISSING_RECURRENCE_INPUT"
            link_error = "missing_family_template"
        elif not anchor or not programme_id or missing_reason:
            classification = "MISSING_RECURRENCE_INPUT"
        elif len(candidates) > 1 and not matched:
            classification = "CONFLICT"
            link_error = "multiple_candidate_occurrences"
        elif not matched:
            classification = (
                "FUTURE_SCHEDULED_OCCURRENCE_UNRESOLVED" if status == "scheduled" else "CONFLICT"
            )
            link_error = "no_durable_occurrence_mapping"
        elif status == "scheduled" and not exact:
            classification = "FUTURE_SCHEDULED_BOOKING_UNRESOLVED"
            link_error = "booking_not_identified"
        elif native_matches:
            classification = (
                "FUTURE_SCHEDULED_NATIVE_ALREADY_EXISTS" if status == "scheduled"
                else "NATIVE_ALREADY_EXISTS"
            )
        else:
            classification = (
                "FUTURE_SCHEDULED_READY_FOR_ADOPTION" if status == "scheduled"
                else "FUTURE_NOT_SCHEDULED_READY_FOR_NATIVE"
            )
        if identity and (exact or source_row["id"] in proven_links):
            exact_import_links.add(identity)
        candidate_ids = [native[key][0]["id"] for key in candidates if len(native.get(key, [])) == 1]
        chosen_booking = exact[0] if len(exact) == 1 else None
        template = template_by_id.get(identity[0]) if identity else None
        history_occurrence = all_occurrences.get(proven_links.get(source_row["id"]))
        reason = link_error or (missing_reason if classification == "MISSING_RECURRENCE_INPUT" else None)
        reason_code = _reason_code(classification, reason, candidate_count=len(candidates))
        items.append({
            "reviewFamily": family or "other",
            "classification": classification,
            "reason": reason,
            "reasonCode": None if classification in {"FUTURE_SCHEDULED_READY_FOR_ADOPTION", "FUTURE_NOT_SCHEDULED_READY_FOR_NATIVE"} else reason_code,
            "reasonDetail": None if classification in {"FUTURE_SCHEDULED_READY_FOR_ADOPTION", "FUTURE_NOT_SCHEDULED_READY_FOR_NATIVE"} else REASON_DETAILS[reason_code],
            "importedReviewId": source_row["id"],
            "aptemReviewId": source_row.get("aptem_review_id"),
            "importedStatus": source_row.get("status"),
            "normalizedImportedStatus": status,
            "plannedDate": planned,
            "completedDate": completed,
            "programmeId": programme_id,
            "reviewTypeId": matched.get("reviewTypeId") if matched else history_occurrence.get("reviewTypeId") if history_occurrence else None,
            "reviewTemplateId": identity[0] if identity else None,
            "templateName": template.get("name") if template else None,
            "occurrenceNumber": identity[1] if identity else None,
            "occurrenceRef": matched.get("occurrenceRef") if matched else f"generated:{identity[1]}" if identity else None,
            "targetDate": _iso(matched["targetDate"]) if matched else _iso(history_occurrence["targetDate"]) if history_occurrence else _iso(native_matches[0].get("target_date")) if len(native_matches) == 1 else None,
            "historicalFulfilment": {
                "reviewTemplateId": history_occurrence["reviewTemplateId"],
                "occurrenceNumber": history_occurrence["occurrenceNumber"],
                "targetDate": _iso(history_occurrence["targetDate"]),
                "basis": "complete_ordered_one_to_one_history",
            } if status == "completed" and history_occurrence else None,
            "nativeReviewInstanceId": native_matches[0]["id"] if len(native_matches) == 1 else None,
            "calendarEventId": chosen_booking.id if chosen_booking else None,
            "bookingMatch": "conflict" if exact and link_error else "exact" if exact else "supporting_only" if supporting else "none",
            "booking": _booking_payload(chosen_booking) if chosen_booking and chosen_booking.owner_email.casefold() == owner else None,
            "candidateOccurrences": [
                {"reviewTemplateId": key[0], "reviewTypeId": expected[key].get("reviewTypeId"),
                 "programmeId": programme_id,
                 "templateName": (template_by_id.get(key[0]) or {}).get("name"),
                 "occurrenceNumber": key[1], "occurrenceRef": expected[key].get("occurrenceRef"),
                 "targetDate": _iso(expected[key]["targetDate"])} for key in candidates
            ],
            "candidateNativeReviewInstanceIds": candidate_ids,
            "supportingBookingIds": [row.id for row in supporting if row.owner_email.casefold() == owner],
        })

    claims = defaultdict(list)
    for index, item in enumerate(items):
        if item["importedReviewId"] is not None and item["reviewTemplateId"] and item["occurrenceNumber"] is not None:
            claims[(item["reviewTemplateId"], item["occurrenceNumber"])].append(index)
    for indices in claims.values():
        if len(indices) < 2:
            continue
        for index in indices:
            item = items[index]
            if item["classification"] == "HISTORICAL_COMPLETED":
                continue
            item["classification"] = "CONFLICT"
            item["reason"] = "multiple_imported_reviews"
            item["reasonCode"] = "CONFLICT_MULTIPLE_IMPORTED_REVIEWS"
            item["reasonDetail"] = REASON_DETAILS[item["reasonCode"]]

    unresolved_families = {
        item["reviewFamily"] for item in items
        if item["importedStatus"] and (
            item["classification"] in {
                "CONFLICT", "FUTURE_SCHEDULED_BOOKING_UNRESOLVED",
                "FUTURE_SCHEDULED_OCCURRENCE_UNRESOLVED", "MISSING_RECURRENCE_INPUT"
            }
            or item["classification"] in {
                "PROTECTED_AWAITING_SIGNATURE", "PROTECTED_IN_PROGRESS"
            } and (not item["plannedDate"] or item["plannedDate"] >= today.isoformat())
            or item["classification"] == "HISTORICAL_COMPLETED"
            and item["reviewFamily"] in {PR, MCM}
            and item["historicalFulfilment"] is None
        )
    }
    for identity, occurrence in expected.items():
        if identity in exact_import_links or _iso(occurrence["targetDate"]) < today.isoformat():
            continue
        matches = native.get(identity, [])
        calendars = [row for row in calendar_rows if row.learner_id == learner.id and (
            row.review_instance_id in {record["id"] for record in matches}
            or row.id in {record.get("calendar_event_id") for record in matches}
            or (row.review_template_id, row.occurrence_number) == identity
            or row.event_key == review_instances.review_calendar_event_key(learner.id, *identity)
        )]
        conflict = len(matches) > 1 or len(calendars) > 1 or any(
            row.owner_email.casefold() != owner for row in calendars
        )
        if len(matches) == 1 and len(calendars) == 1:
            conflict = conflict or bool(
                matches[0].get("calendar_event_id")
                and matches[0]["calendar_event_id"] != calendars[0].id
                or calendars[0].review_instance_id
                and calendars[0].review_instance_id != matches[0]["id"]
            )
        historical_collision = (
            occurrence["reviewTypeCode"], _iso(occurrence["targetDate"])
        ) in historical_future_dates
        template = template_by_id.get(identity[0])
        booking = calendars[0] if len(calendars) == 1 and not conflict else None
        classification = (
            "CONFLICT" if conflict else "NATIVE_ALREADY_EXISTS" if matches
            else "CONFLICT" if template_family_counts.get(occurrence["reviewTypeCode"], 0) > 1
            or historical_collision or booking or occurrence["reviewTypeCode"] in unresolved_families
            else "FUTURE_NOT_SCHEDULED_READY_FOR_NATIVE"
        )
        reason = (
            "conflicting_native_or_calendar_rows" if conflict else
            "multiple_applicable_native_templates" if template_family_counts.get(occurrence["reviewTypeCode"], 0) > 1 and not matches else
            "historical_import_date_collision" if historical_collision and not matches else
            "unlinked_existing_booking" if booking and not matches else
            "unreconciled_imported_family" if occurrence["reviewTypeCode"] in unresolved_families and not matches else None
        )
        reason_code = _reason_code(classification, reason)
        items.append({
            "reviewFamily": occurrence["reviewTypeCode"],
            "classification": classification,
            "reason": reason,
            "reasonCode": reason_code if classification != "FUTURE_NOT_SCHEDULED_READY_FOR_NATIVE" else None,
            "reasonDetail": REASON_DETAILS[reason_code] if classification != "FUTURE_NOT_SCHEDULED_READY_FOR_NATIVE" else None,
            "importedReviewId": None, "aptemReviewId": None, "importedStatus": None,
            "normalizedImportedStatus": None,
            "plannedDate": None, "completedDate": None,
            "programmeId": programme_id,
            "reviewTypeId": occurrence.get("reviewTypeId"),
            "reviewTemplateId": identity[0],
            "templateName": template.get("name") if template else occurrence.get("reviewName"),
            "occurrenceNumber": identity[1],
            "occurrenceRef": occurrence.get("occurrenceRef"),
            "targetDate": _iso(occurrence["targetDate"]),
            "historicalFulfilment": None,
            "nativeReviewInstanceId": matches[0]["id"] if len(matches) == 1 else None,
            "calendarEventId": booking.id if booking else None,
            "bookingMatch": "conflict" if conflict else "exact" if booking else "none",
            "booking": _booking_payload(booking) if booking else None,
            "candidateOccurrences": [], "candidateNativeReviewInstanceIds": [],
            "supportingBookingIds": [],
        })

    summary = {key: 0 for key in SUMMARY_KEYS.values()}
    by_family = {family: dict(summary) for family in (PR, MCM, "other")}
    by_reason_code = Counter()
    conflicts_by_reason_code = Counter()
    missing_by_reason_code = Counter()
    scheduled = Counter()
    not_scheduled = Counter()
    scheduled_future = Counter()
    not_scheduled_future = Counter()
    for item in items:
        key = SUMMARY_KEYS[item["classification"]]
        summary[key] += 1
        by_family[item["reviewFamily"]][key] += 1
        if item["reasonCode"]:
            by_reason_code[item["reasonCode"]] += 1
            if item["classification"] == "CONFLICT":
                conflicts_by_reason_code[item["reasonCode"]] += 1
            elif item["classification"] == "MISSING_RECURRENCE_INPUT":
                missing_by_reason_code[item["reasonCode"]] += 1
        status = item["normalizedImportedStatus"]
        if status == "scheduled":
            scheduled[key] += 1
            if item["plannedDate"] and item["plannedDate"] >= today.isoformat():
                scheduled_future[key] += 1
        elif status == "not-scheduled":
            not_scheduled[key] += 1
            if item["plannedDate"] and item["plannedDate"] >= today.isoformat():
                not_scheduled_future[key] += 1
    return {
        "learnerId": learner.id,
        "learnerStartDate": _iso(anchor),
        "programmeId": programme_id,
        "asOfDate": today.isoformat(),
        "items": items,
        "summary": {**summary, "byFamily": by_family,
                    "byReasonCode": dict(sorted(by_reason_code.items())),
                    "conflictsByReasonCode": dict(sorted(conflicts_by_reason_code.items())),
                    "missingInputsByReasonCode": dict(sorted(missing_by_reason_code.items())),
                    "scheduled": dict(sorted(scheduled.items())),
                    "notScheduled": dict(sorted(not_scheduled.items())),
                    "scheduledFuture": dict(sorted(scheduled_future.items())),
                    "notScheduledFuture": dict(sorted(not_scheduled_future.items()))},
    }


def build_aptem_review_reconciliation_preview(learner, owner_email, *, today=None):
    """Read current sources for one already-authorized Aptem learner."""
    from . import views

    if schema_gate.runtime_bootstrap_allowed():
        raise RuntimeError("Read-only preview requires provisioned Curriculum schema.")
    today = today or datetime.now(ZoneInfo(settings.TIME_ZONE)).date()
    programme_id = views.resolve_curriculum_programme_id(
        getattr(learner, "programme_id", None) or getattr(learner, "programme", None)
    )
    commercial, enrolment = views.fetch_source_schedule_rows([learner])
    anchor, reason = views.resolve_review_anchor_date(learner.id, commercial, enrolment)
    window_start, window_end = views.resolve_schedule_window(learner.id, commercial, enrolment, learner)
    missing_reason = reason
    if not programme_id:
        missing_reason = missing_reason or "missing_curriculum_programme"
    if not window_start:
        missing_reason = missing_reason or "missing_window_start"
    elif not window_end:
        missing_reason = missing_reason or "missing_window_end"
    elif window_end <= window_start:
        missing_reason = missing_reason or "invalid_schedule_window"

    templates = []
    occurrences = []
    template_family_counts = Counter()
    if programme_id:
        all_templates = review_instances.list_enabled_review_templates(programme_id)
        type_index = review_types.review_type_index()
        scope = {"cohort_id": getattr(learner, "cohort_id", None),
                 "cohort": getattr(learner, "cohort", None),
                 "group_id": getattr(learner, "group_id", None),
                 "group": getattr(learner, "group_name", None)}
        status = getattr(learner, "programme_status", None) or getattr(learner, "status", None)
        templates = [row for row in all_templates if
                     (type_index.get(row.get("review_type_id")) or {}).get("code") in {PR, MCM}
                     and review_instances.review_applies_to_placement(row, scope)
                     and review_instances.learner_is_eligible(row, status)]
        template_family_counts.update(
            (type_index.get(row.get("review_type_id")) or {}).get("code") for row in templates
        )
        if not templates:
            missing_reason = missing_reason or "no_applicable_native_review_template"
        if anchor and window_start and window_end and window_end > window_start and templates:
            occurrences = review_instances.resolve_programme_review_occurrences(
                programme_id, learner.id, status, anchor,
                anchor, max(today, window_end),
                template_cache={programme_id: all_templates}, learner_scope=scope,
            )

    with connections[views.get_learner_db_alias()].cursor() as cursor:
        cursor.execute('''
            SELECT id, learner_id, aptem_review_id, review_name, review_type,
                   reviewer_name, learner_name, planned_scheduled_date,
                   completed_date, status, review_data, extraction_status, last_error
            FROM "Learner".reviews
            WHERE learner_id = %s AND NULLIF(BTRIM(aptem_review_id), '') IS NOT NULL
            ORDER BY COALESCE(completed_date, planned_scheduled_date), id
        ''', [learner.id])
        columns = [col[0] for col in cursor.description]
        imported_rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    native_rows = review_instances.list_review_instances_for_learner(learner.id)
    calendar_rows = list(CoachCalendarEvent.objects.filter(learner_id=learner.id))
    return classify_aptem_review_preview(
        learner=learner, imported_rows=imported_rows, occurrences=occurrences,
        native_rows=native_rows, calendar_rows=calendar_rows,
        templates=templates, programme_id=programme_id, anchor=anchor,
        missing_reason=missing_reason, owner_email=owner_email, today=today,
        template_family_counts=template_family_counts, window_start=window_start,
    )
