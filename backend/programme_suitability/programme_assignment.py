"""Apply a confirmed selection to the existing enrolment/profile relationship."""
import re
from datetime import date

from django.db import connections, router
from django.conf import settings
from django.utils import timezone
from .intake_calendar import resolve_intake, DEFAULT_INTAKES

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


def automatic_cohort_window(completed_on, entries=None):
    """Return the annual intake label for a completion date."""
    return resolve_intake(completed_on, entries or DEFAULT_INTAKES)


def published_intake_calendar(database):
    """Read the effective calendar from the current immutable Wizard Layout."""
    from enrolment_api.wizard_layout import CONN, read_current
    with connections[CONN].cursor() as cursor:
        current = read_current(cursor)
    calendar = (current[1] or {}).get('intakeCalendar') if current else None
    return calendar if isinstance(calendar, list) else DEFAULT_INTAKES


def resolve_automatic_cohort(database, programme_id, completed_on, calendar=None):
    """Resolve target intake or the latest eligible earlier intake."""
    from enrolment_api.wizard_layout import CONN, read_current
    from .intake_calendar import cohort_assignment_window
    from .programme_structure import validate_structure
    with connections[CONN].cursor() as cursor:
        published = read_current(cursor)
    layout = published[1] if published else {}
    snapshot = layout.get('programmeStructure') or {}
    definitions = (layout.get('programmeSuitability') or {}).get('programmes', [])
    programme = next((p for p in snapshot.get('programmes', [])
        if p.get('id') == programme_id or p.get('curriculumProgrammeId') == programme_id
        or any(d.get('id') == p.get('id') and d.get('catalogueId') == programme_id for d in definitions)), None)
    if programme and any(c.get('assignmentPeriod') for c in programme.get('cohorts', [])):
        try:
            validate_structure(snapshot)
        except (ValueError, TypeError):
            return None
        with connections[database].cursor() as cursor:
            cursor.execute("SELECT cohort_id,cohort_name FROM curriculum.cohorts WHERE programme_id=%s AND lower(coalesce(status,'active')) NOT IN ('archived','deleted','inactive')",[programme_id])
            real = dict(cursor.fetchall())
        matching, previous = [], []
        for cohort in programme.get('cohorts', []):
            # The published structure governs new assignments. Curriculum's
            # status can change while an Admin is editing an unpublished draft;
            # that must not change assignment eligibility before publication.
            # Keep the real-row check above to reject missing/archived records.
            if (cohort.get('id') not in real or
                    str(cohort.get('status', 'active')).lower() not in {'planned', 'active'}):
                continue
            # A published empty editor configuration is not usable for learners.
            configured = any(m.get('active',True) for m in cohort.get('modules',[]))
            if programme.get('id') == 'PCP_L6':
                configured = bool(cohort.get('routes')) and all(r.get('modules') for r in cohort['routes'])
            window = cohort_assignment_window(cohort)
            if not configured or window is None:
                continue
            candidate = (window[1], cohort['id'], real[cohort['id']])
            if window[0] <= completed_on <= window[1]:
                matching.append(candidate)
            elif window[1] < completed_on:
                previous.append(candidate)
        if len(matching) > 1:
            return None
        candidates = matching or previous
        if candidates:
            _, cid, name = max(candidates)
            return cid, name, not bool(matching)
        return None
    if getattr(settings, 'PROGRAMME_STRUCTURE_COHORT_LEARNER_ENABLED', False):
        # New cohort-aware assignment cannot substitute a legacy name match.
        return None
    intake, year = automatic_cohort_window(completed_on, calendar)
    with connections[database].cursor() as cursor:
        cursor.execute(
            "SELECT cohort_id, cohort_name FROM curriculum.cohorts "
            "WHERE programme_id = %s AND lower(coalesce(status, 'active')) IN ('planned', 'active')",
            [programme_id],
        )
        rows = cursor.fetchall()
    token = re.compile(rf"(?:^|[-_\\s]){intake}(?:[-_\\s])?{year}(?:$|[-_\\s])", re.I)
    matches = [row for row in rows if token.search(str(row[0] or "")) or token.search(str(row[1] or ""))]
    if len(matches) == 1:
        return (*matches[0], False)
    # Parse known intake identities and compare chronology rather than row order.
    candidates = []
    for row in rows:
        text = f"{row[0] or ''} {row[1] or ''}"
        found = re.search(r"(?:^|[-_\s])(Feb|Jun|Oct)[-_\s]?(\d{4})(?:$|[-_\s])", text, re.I)
        if not found:
            continue
        intake_name, intake_year = found.group(1).title(), int(found.group(2))
        month = {'Feb': 2, 'Jun': 6, 'Oct': 10}[intake_name]
        chronology = (intake_year, month)
        target_month = {'Feb': 2, 'Jun': 6, 'Oct': 10}[intake]
        if chronology <= (year, target_month):
            candidates.append((chronology, row))
    if candidates:
        candidates.sort(key=lambda item: item[0], reverse=True)
        row = candidates[0][1]
        return (*row, True)
    return None


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
    automatic_cohort = None
    if profile and not profile.cohort_id and getattr(settings, 'PROGRAMME_STRUCTURE_AUTOMATIC_COHORT_ASSIGNMENT_ENABLED', False):
        completed_on = timezone.localtime(row.selection_confirmed_at or row.confirmed_at).date()
        automatic_cohort = resolve_automatic_cohort(database, identifier, completed_on, published_intake_calendar(database))
        if automatic_cohort is None:
            # Programme assignment is still valid, but cohort assignment needs
            # an explicit admissions decision when the curriculum is missing or
            # ambiguous. Never guess from a display name or default intake.
            automatic_cohort = {"pending": True, "intake": automatic_cohort_window(completed_on)[0], "year": automatic_cohort_window(completed_on)[1]}
    outcome = {"status": "WOULD_APPLY" if dry_run else "APPLIED", **assignment,
               "previousProgramme": old_name, "profileId": profile.pk if profile else None,
               "automaticCohort": automatic_cohort,
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
        if isinstance(automatic_cohort, tuple):
            cohort_id, cohort_name = automatic_cohort[:2]
            profile.cohort_id = str(cohort_id)
            profile.cohort = str(cohort_name or cohort_id)
            changes.extend(["cohort_id", "cohort"])
        if changes:
            profile.save(using=database, update_fields=[*changes, "updated_at"])
    row.review = {**row.review, "programmeAssignment": outcome}
    row.history = [*row.history, {"action": "PROGRAMME_ASSIGNMENT_SYNCED", **outcome, "revision": row.revision}]
    row.save(using=database, update_fields=["review", "history", "updated_at"])
    return outcome
