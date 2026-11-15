"""Read-only sources for future document derivation; never rewrites issued rows."""
import re
from django.db import connection
from learner_api.mappers import _s


def document_dates(learner):
    """The learner's own period only; programme/cohort dates are not substitutes."""
    from learner_api.apprenticeship_agreement import _to_date
    supplied = [_s(getattr(learner, key, '')) for key in ('learner_start_date', 'learner_end_date')]
    start, end = [_to_date(raw) for raw in supplied]
    return start, end, 'learner-specific'


def date_warnings(learner):
    from learner_api.apprenticeship_agreement import _to_date
    start, end, _ = document_dates(learner)
    warnings = []
    for key, resolved in (('learner_start_date', start), ('learner_end_date', end)):
        raw = _s(getattr(learner, key, ''))
        if raw and _to_date(raw) is None:
            warnings.append({'field': key, 'code': 'invalid_date', 'message': 'The saved learner date is invalid; it was not replaced with a cohort date.'})
        elif resolved is None:
            warnings.append({'field': key, 'code': 'missing_date', 'message': 'The learner-specific date is missing. Enter it on the learner record; programme and cohort dates are not used.'})
    if start and end and start > end:
        warnings.append({'field': 'enrolment.period', 'code': 'conflicting_dates', 'message': 'The apprenticeship start date is after its end date. Correct the learner record before issuance.'})
    return warnings


def programme_level(learner):
    # Use the officially assigned catalogue programme, not assessment scores or
    # the recommendation (which can differ from the confirmed selection).
    from learner_api.placement_links import placement_ids
    identifier = placement_ids(learner)['programme_id']
    with connection.cursor() as cursor:
        cursor.execute('SELECT level FROM curriculum.programmes WHERE ' +
                       ('programme_id = %s' if identifier else 'lower(trim(name)) = lower(%s)'),
                       [identifier or _s(learner.programme)])
        rows = cursor.fetchall()
    if len(rows) != 1:
        return ''
    match = re.fullmatch(r'(?:level|lvl)?[-\s]*(\d+)', _s(rows[0][0]), re.I)
    return match[1] if match else ''


def plan_modules(learner):
    from learner_api import learning_plan as source
    catalogue = {m['moduleId']: m for m in source.learner_programme_modules(learner)}
    # Reuse the existing assignment rules: an explicitly emptied plan stays
    # empty, while non-explicit saved plans inherit newly assigned group modules.
    entries = source.effective_training_plan(learner)
    saved = source._saved_modules(learner)
    if not any(m.get('assignmentMode') == 'explicit' for m in (saved or [])):
        represented = {_s(m.get('moduleId')) for m in entries}
        entries += [m for key, m in source._aptem_subject_modules(learner).items() if key not in represented]
    extra = source._modules_by_id(_s(m.get('moduleId')) for m in entries if _s(m.get('moduleId')) not in catalogue)
    return [catalogue.get(_s(m.get('moduleId'))) or extra.get(_s(m.get('moduleId'))) or source._orphan_module(m)
            for m in entries if _s(m.get('moduleId'))]


def employment_details(learner):
    from learner_api.ilr_document import _wizard_ilr_details
    return _wizard_ilr_details(learner.pk)


def employer_postcode(learner, employer_answers):
    # The contact's personal address is not the employing company's address.
    from enrolment_api.ilr_employer_details import organisation_for_learner

    organisation = organisation_for_learner(learner)
    if organisation is not None:
        return _s(organisation.post_code)
    return _s(employer_answers.get('postcode'))
