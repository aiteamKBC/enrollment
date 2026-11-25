"""ULN storage switch for a schema-first, explicitly approved cutover.

Legacy is the default. Never map the optional column onto EnrolmentUser: that
would make unrelated enrolment queries depend on a migration being deployed.
"""
from django.conf import settings
from django.db import DatabaseError, connections, transaction
from .models import WizardPlr


def canonical_enabled():
    return getattr(settings, 'ENROLMENT_ULN_STORAGE', 'legacy') == 'created_users'


def read_uln(kind, learner_id):
    if not canonical_enabled():
        rows = WizardPlr.objects.filter(learner_id=learner_id)
        if kind is not None:
            rows = rows.filter(learner_kind=kind)
        return rows.values_list('uln', flat=True).first() or ''
    with connections['enrolment'].cursor() as cursor:
        cursor.execute('SELECT "ULN" FROM enrolment."Created_users" WHERE id = %s', [learner_id])
        row = cursor.fetchone()
    if row is None:
        raise DatabaseError('ULN owner is unavailable.')
    # NULL is authoritative after cutover, including a deliberate Admin clear.
    # No fallback to Wizard_Plr, draft JSON, or historical review values.
    return row[0] or ''


@transaction.atomic(using='enrolment')
def write_uln(kind, learner_id, value):
    from .plr_uln import reserve_uln, valid_uln
    if not valid_uln(value):
        raise ValueError('ULN must be empty or contain exactly ten ASCII digits.')
    if canonical_enabled():
        # Same ordering for every writer: assignment lock, then Created_users.
        reserve_uln(value, learner_id)
        with connections['enrolment'].cursor() as cursor:
            cursor.execute('UPDATE enrolment."Created_users" SET "ULN" = %s WHERE id = %s',
                           [value or None, learner_id])
            if cursor.rowcount != 1:
                raise DatabaseError('ULN owner is unavailable.')
        # Retained only for old readers and an application rollback. Never
        # create a mapping for an orphan, or use Created_users.learner_id.
        WizardPlr.objects.filter(learner_id=learner_id).update(uln=value or None)
    WizardPlr.objects.update_or_create(learner_kind=kind, learner_id=learner_id,
                                     defaults={'uln': (value or None) if canonical_enabled() else value})
