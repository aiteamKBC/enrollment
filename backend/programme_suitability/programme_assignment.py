"""Apply a confirmed selection to the existing enrolment/profile relationship."""
import re

from django.db import connections, router
from django.utils import timezone

from learner_api.models import EnrolmentUser, LearnerProfile
from .configuration import PROGRAMMES
from .selection import validate_selection

CATALOGUE_NAMES = {
    "ME_L4": "Marketing Executive Level 4",
    "MM_L6": "Marketing Manager Level 6",
    "APM_L4": "Associate Project Manager Level 4",
    "PCP_L6": "Project Controls Professional Level 6",
}


class ProgrammeAssignmentError(ValueError):
    """Confirmation needs staff review; the enclosing transaction must roll back."""


def assignment_database(row):
    database = row._state.db or "default"
    source_database = router.db_for_write(EnrolmentUser) or database
    # Use ONE connection/transaction even though legacy models normally use an alias.
    keys = ("ENGINE", "HOST", "PORT", "NAME", "USER")
    def value(alias, key):
        config = connections[alias].settings_dict
        default = "5432" if key == "PORT" and config["ENGINE"] == "django.db.backends.postgresql" else ""
        return str(config.get(key) or default)
    if any(value(database, k) != value(source_database, k) for k in keys):
        raise ProgrammeAssignmentError("Programme confirmation requires the enrolment and assessment records in the same database. Please contact admissions.")
    return database


def resolve_programme(row, database):
    definition = next((p for p in row.configuration["programmes"] if p["id"] == row.selected_programme), None)
    expected = PROGRAMMES.get(row.selected_programme)
    if not definition or not expected or (row.pathway, definition["level"]) != expected or definition["pathway"] != row.pathway:
        raise ProgrammeAssignmentError("The confirmed programme has no valid pathway mapping. Please contact admissions.")
    validate_selection(row.configuration, row.pathway, row.result, row.selected_programme)
    identifier = definition.get("catalogueId")
    with connections[database].cursor() as cursor:
        cursor.execute(
            'SELECT programme_id, name, level FROM curriculum.programmes '
            'WHERE ' + ('programme_id = %s' if identifier else 'lower(trim(name)) = lower(%s)') +
            ' AND coalesce(is_archived, false) = false AND coalesce(is_active, true) = true '
            "AND deleted_at IS NULL AND lower(coalesce(status, 'active')) NOT IN ('archived', 'deleted', 'inactive')",
            [identifier or CATALOGUE_NAMES[row.selected_programme]],
        )
        rows = cursor.fetchall()
    if len(rows) != 1:
        raise ProgrammeAssignmentError("The confirmed programme has a missing or ambiguous active catalogue mapping. Please contact admissions.")
    programme_id, name, level = rows[0]
    parsed_level = re.fullmatch(r"(?:level|lvl)?[-\s]*([46])", str(level or "").strip(), re.I)
    if (not programme_id or not parsed_level or int(parsed_level[1]) != expected[1]
            or str(name).strip().casefold() != CATALOGUE_NAMES[row.selected_programme].casefold()):
        raise ProgrammeAssignmentError("The catalogue programme does not match the confirmed pathway and level. Please contact admissions.")
    return {"programmeId": str(programme_id), "programmeName": name}


def _existing_profile(source, database):
    profiles = LearnerProfile.objects.using(database).select_for_update()
    matches = list(profiles.filter(enrolment_id=source.pk)[:2])
    if not matches and source.email:
        matches = list(profiles.filter(enrolment_id__isnull=True, email__iexact=source.email)[:2])
        if matches and EnrolmentUser.all_learners.using(database).filter(email__iexact=source.email).exclude(pk=source.pk).exists():
            raise ProgrammeAssignmentError("The legacy learner profile is ambiguous. Admissions must review its account link.")
    if len(matches) > 1:
        raise ProgrammeAssignmentError("More than one learner profile is linked to this application. Please contact admissions.")
    return matches[0] if matches else None


def _check_placement(database, assignment, cohort, group, cohort_id=None, group_id=None):
    if not cohort and not group and not cohort_id and not group_id:
        return
    with connections[database].cursor() as cursor:
        cursor.execute(
            'SELECT cohort_id FROM curriculum.cohorts WHERE (programme_id = %s OR programme_name = %s) '
            'AND ' + ('cohort_id = %s' if cohort_id else '(cohort_name = %s OR cohort_id = %s)'),
            [assignment["programmeId"], assignment["programmeName"], *([cohort_id] if cohort_id else [cohort, cohort])],
        )
        cohorts = cursor.fetchall()
        if len(cohorts) != 1:
            raise ProgrammeAssignmentError("Your existing cohort is not compatible with the selected programme. Admissions must review your placement before confirmation.")
        if group or group_id:
            cursor.execute(
                'SELECT group_id FROM curriculum.groups WHERE (programme_id = %s OR programme_name = %s) '
                'AND (cohort_id = %s OR cohort_name = %s) AND ' +
                ('group_id = %s' if group_id else '(group_name = %s OR group_id = %s)'),
                [assignment["programmeId"], assignment["programmeName"], cohorts[0][0], cohort,
                 *([group_id] if group_id else [group, group])],
            )
            if len(cursor.fetchall()) != 1:
                raise ProgrammeAssignmentError("Your existing group is not compatible with the selected programme. Admissions must review your placement before confirmation.")


def sync_confirmed_programme_to_learner(row, *, actor="system", reconcile=False, dry_run=False):
    """Called after row.save(), inside its transaction. Never creates accounts/profiles."""
    if not row.selection_confirmed_at or not row.confirmed_at:
        raise ProgrammeAssignmentError("Confirm the programme selection before assigning it to the learner.")
    if not row.selected_programme:
        return {"status": "NOT_APPLICABLE"}
    previous_sync = row.review.get("programmeAssignment")
    if previous_sync and previous_sync.get("status") == "APPLIED":
        # Do not undo a later authorised staff placement change on a network retry.
        return previous_sync
    database = assignment_database(row)
    if not connections[database].in_atomic_block:
        raise ProgrammeAssignmentError("Programme synchronization must run inside the confirmation transaction.")
    assignment = resolve_programme(row, database)
    source = EnrolmentUser.all_learners.using(database).select_for_update().filter(pk=row.learner_id).first()
    if source is None or (source.learner_type or "apprenticeship") != row.learner_kind:
        raise ProgrammeAssignmentError("The confirmed assessment no longer matches its learner account.")
    profile = _existing_profile(source, database)
    name, identifier = assignment["programmeName"], assignment["programmeId"]
    old_name = str(source.programme or "").strip()
    profile_change = bool(profile and ((profile.programme and profile.programme != name) or
                                      (profile.programme_id and profile.programme_id != identifier)))
    if old_name != name or profile_change:
        finalised = (str(source.onboarding_status or "").casefold() in {"submitted", "completed"}
                     or str(source.programme_status or "").casefold() not in {"", "fresh user", "onboarding"}
                     or (profile and (str(profile.lifecycle_status).casefold() == "active" or
                                      str(profile.programme_status).casefold() in {"active", "delivery", "ready to enrol"}))
                     or row.review_status == "REVIEW_COMPLETED")
        if finalised or (reconcile and old_name):
            raise ProgrammeAssignmentError("An existing or finalised programme assignment needs admissions review; it has not been overwritten.")
    if reconcile and profile and ((profile.programme and profile.programme != name) or (profile.programme_id and profile.programme_id != identifier)):
        raise ProgrammeAssignmentError("The existing profile programme needs admissions review; it has not been overwritten.")
    _check_placement(database, assignment, source.cohort, source.group)
    if profile:
        _check_placement(database, assignment, profile.cohort, profile.group_name, profile.cohort_id, profile.group_id)
    outcome = {"status": "WOULD_APPLY" if dry_run else "APPLIED", **assignment,
               "previousProgramme": old_name, "profileId": profile.pk if profile else None,
               "at": timezone.now().isoformat(), "actor": actor}
    if dry_run:
        return outcome
    if old_name != name:
        source.programme = name
        source.save(using=database, update_fields=["programme"])
    if profile:
        changes = []
        for field, value in {"programme": name, "programme_id": identifier, "enrolment_id": source.pk}.items():
            if getattr(profile, field) != value:
                setattr(profile, field, value)
                changes.append(field)
        if changes:
            profile.save(using=database, update_fields=[*changes, "updated_at"])
    row.review = {**row.review, "programmeAssignment": outcome}
    row.history = [*row.history, {"action": "PROGRAMME_ASSIGNMENT_SYNCED", **outcome, "revision": row.revision}]
    row.save(using=database, update_fields=["review", "history", "updated_at"])
    return outcome
