"""Explicit learner Sign + Next state for the apprenticeship ILR step."""
from django.db import DatabaseError, connections

from .models import ExtendedIlr, WizardIlrLearnerDetails, WizardPersonalDetails
from .ilr_health import validate_ilr_additions
from .wizard_layout import read_current

SCOPE = 'apprenticeship'
DEFAULT_CERTIFICATION = 'I certify that the information contained on this form is correct'
PAPERWORK_DECLARATION = 'By signing, I confirm the ILR accuracy declaration and authorise this signature to be used on my enrolment paperwork.'


def finalisation(learner_id):
    return WizardIlrLearnerDetails.objects.filter(
        learner_kind=SCOPE, learner_id=learner_id,
    ).only('ilr_finalised_at', 'ilr_finalised_by', 'ilr_signed_snapshot').first()


def signed_snapshot(learner_id):
    row = finalisation(learner_id)
    if not row or not row.ilr_finalised_at:
        return None
    if not isinstance(row.ilr_signed_snapshot, dict) or not all(
        key in row.ilr_signed_snapshot for key in ('personalDetails', 'ilrDetails', 'finalisedAt')
    ):
        raise DatabaseError('Signed ILR snapshot is unavailable.')
    return row.ilr_signed_snapshot


def legacy_review_required(learner, learner_id):
    """Quarantine ambiguous *submitted wizard* signatures, not reusable samples.

    A historic signature plus a submitted/completed journey is not proof of the
    new Next event, so it must never set ilr_finalised_at or be re-signed for
    the learner. It is nonetheless an existing compliance record: keep learner
    writes away from its ILR sections until a separately approved audit path.
    """
    if str(getattr(learner, 'onboarding_status', '') or '').strip().casefold() not in {'submitted', 'completed'}:
        return False
    if WizardPersonalDetails.objects.filter(learner_kind=SCOPE, learner_id=learner_id,
                                            signature__isnull=False).exclude(signature='').exists():
        return True
    row = ExtendedIlr.objects.filter(learner_kind=SCOPE, learner_id=learner_id).only('wizard_draft').first()
    return bool(((row.wizard_draft or {}).get('personalDetails') or {}).get('signature')) if row else False


def ilr_custom_keys(layout):
    return {item['key'] for step in layout.get('steps') or []
            if step.get('slug') == 'ilr-details'
            for item in step.get('items') or []
            if isinstance(item, dict) and not item.get('builtin') and item.get('key')}


def published_ilr_custom_keys():
    with connections['enrolment'].cursor() as cursor:
        current = read_current(cursor)
    return ilr_custom_keys(current[1]) if current else set()


def validate_finalisation(draft, learner):
    """Recheck the material ILR requirements server-side before recording the event.

    The builder may hide optional questions, but never weakens the mandatory
    identity, status and explicit declaration checks below.
    """
    personal = draft.get('personalDetails') or {}
    details = draft.get('ilrDetails') or {}
    if not isinstance(personal, dict) or not isinstance(details, dict):
        raise ValueError('Complete the ILR before signing and continuing.')
    validate_ilr_additions(details, finalise=True)
    required = {
        'Title': personal.get('title') or learner.title,
        'Current postcode': personal.get('currentPostcode') or learner.current_postcode,
        'Legal sex': details.get('legalSex'),
        'Ethnicity': details.get('ethnicity'),
        'Highest qualification': details.get('highestQualification'),
        'Employment status': details.get('employmentStatus'),
    }
    missing = [name for name, value in required.items() if not str(value or '').strip()]
    if not isinstance(details.get('longTermDisability'), bool):
        missing.append('Long-term disability')
    if not isinstance(details.get('fullTimeEducation'), bool):
        missing.append('Full-time education or training')
    if details.get('employmentStatus') == 'In paid employment' and not details.get('employmentStartDate'):
        missing.append('Start date with employer')
    if missing:
        raise ValueError('Complete these ILR fields before signing: ' + ', '.join(missing) + '.')

    # Reusable signature samples and legacy signature dates are not evidence of
    # assent. The declaration is tied to the wording shown at signing.
    with connections['enrolment'].cursor() as cursor:
        current = read_current(cursor)
    layout = current[1] if current else {}
    texts = layout.get('texts') or {}
    for step in layout.get('steps') or []:
        if step.get('slug') != 'ilr-details' or step.get('hidden'):
            continue
        for item in step.get('items') or []:
            if (item.get('builtin') or item.get('hidden') or not item.get('required')):
                continue
            answer = (draft.get('custom') or {}).get(item.get('key'))
            if answer is None or answer == '' or answer == []:
                raise ValueError(f"Complete the required ILR field: {item.get('label') or 'additional question'}.")
    certification = texts.get('ild.signature.certify') or DEFAULT_CERTIFICATION
    declaration = personal.get('signatureDeclaration') or {}
    expected = f'{certification}\n{PAPERWORK_DECLARATION}'
    if not (isinstance(personal.get('signature'), str) and personal['signature'].strip()
            and isinstance(declaration, dict) and declaration.get('text') == expected
            and isinstance(declaration.get('confirmedAt'), str) and declaration['confirmedAt'].strip()):
        raise ValueError('Sign and confirm the ILR declaration before continuing.')
