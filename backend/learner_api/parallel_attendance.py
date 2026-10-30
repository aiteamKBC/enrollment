"""Credit an equivalent group delivery without changing either attendance record."""
from collections import defaultdict
from datetime import date

from django.utils import timezone

from .alternative_recovery import completed_alternatives_by_occurrence
from .attendance_rules import INVALID, attendance_outcome
from .attendance import _live_register_rows
from .attendance_assignments import assigned_module_ids
from .teams_attendance import fetch_verified_teams_attendance_rows


def apply_parallel_attendance(rows, source, *, records=(), alternatives=None, evidence=None, now=None):
    now = now or timezone.now()
    email = str(getattr(source, 'email', '') or '').strip().casefold()
    candidates = [
        row for row in rows
        if row.get('source') == 'microsoft-teams'
        and row.get('module_catalogue_id') and row.get('session_number') is not None
        and row.get('attendance_status') in {'absent', 'unmarked'}
        and attendance_outcome(row, now)[0] in {'absent', 'unmarked'}
        and not any(str(row.get(key) or '').strip().lower() in INVALID
                    for key in ('status', 'session_status', 'occurrence_status'))
    ]
    if not candidates or not email:
        return rows
    originals = {str(row.get('occurrence_id') or row['session_id']) for row in candidates}
    if alternatives is None:
        alternatives = completed_alternatives_by_occurrence(originals, at=now)
    # Selected-session projections do not carry the rest of the learner's
    # schedule. Check assignment as well so a second required delivery cannot
    # be reused as recovery just because it is outside that selected view.
    assigned = set(map(str, assigned_module_ids(source))) if any(
        item.get('moduleCatalogueId')
        for original in originals for item in alternatives.get(original, [])
    ) else set()
    owners = defaultdict(set)
    explanations = {}
    scheduled_ids = {str(row.get('occurrence_id') or row['session_id'])
                     for row in rows if row.get('source') == 'microsoft-teams'}
    for original in originals:
        for alternative in alternatives.get(original, []):
            target = str(alternative['id'])
            if target not in scheduled_ids and alternative.get('moduleCatalogueId') not in assigned:
                owners[target].add(original)
                group = str(alternative.get('group') or '').strip()
                explanations[target] = f'Attended with group {group}' if group else 'Attended with another group'
                # dateIso already uses the alternative session's business timezone.
                if alternative.get('dateIso'):
                    attended_date = date.fromisoformat(alternative['dateIso']).strftime('%d %b %Y')
                    explanations[target] += f' on {attended_date}'
    # Never spend one attendance record on multiple required lectures.
    targets = {target: next(iter(ids)) for target, ids in owners.items() if len(ids) == 1}
    if not targets:
        return rows
    if evidence is None:
        evidence = fetch_verified_teams_attendance_rows(
            learner_emails=[email], occurrence_ids=sorted(targets),
            parallel_delivery_guests={target: {email} for target in targets},
        )
    by_target = {}
    for row in evidence:
        target = str(row.get('occurrence_id') or row.get('session_id'))
        if (target in targets and str(row.get('learner_email') or '').strip().casefold() == email
                and str(row.get('enrolment_id')) == str(source.id)
                and row.get('attendance_report_id') and row.get('attendance_status') == 'present'):
            by_target[target] = row
    # A saved register decision has the same precedence as on the original
    # lecture. Explicit absence must not be undone by raw Teams evidence.
    saved_rows = [row for row in records if row.get('source') == 'live-register']
    missing = set(targets) - {str(row.get('occurrence_id') or row.get('session_id')) for row in saved_rows}
    if missing:
        saved_rows += _live_register_rows(source, occurrence_ids=missing)
    for row in saved_rows:
        target = str(row.get('occurrence_id') or row.get('session_id'))
        if (row.get('source') == 'live-register' and target in targets
                and str(row.get('learner_email') or '').strip().casefold() == email):
            if row.get('attendance_status') == 'present':
                by_target[target] = row
            else:
                by_target.pop(target, None)
    recovered = {}
    for target in sorted(by_target):
        recovered.setdefault(targets[target], target)
    candidate_ids = {id(row) for row in candidates}
    return [
        {**row, 'catchup_completed': True, 'excused': True,
         'effective_attendance': 1, 'effective_attendance_status': 'made_up',
         'final_outcome': 'made_up', 'attendance_basis': 'parallel_delivery',
         'absence_reason': '; '.join(filter(None, [
             str(row.get('absence_reason') or '').strip(),
             explanations[recovered[str(row.get('occurrence_id') or row['session_id'])]],
         ])),
         'recovery_occurrence_id': recovered[str(row.get('occurrence_id') or row['session_id'])]}
        if id(row) in candidate_ids and str(row.get('occurrence_id') or row['session_id']) in recovered
        else row
        for row in rows
    ]
