"""Append-only LMS decisions on historical Aptem evidence.

The Aptem evidence and its original feedback remain authoritative source records.
This table records subsequent Advanced Admin reviews without editing that source.
"""
from django.db import connections


DECISIONS = frozenset({'accepted', 'partial', 'referred', 'escalated', 'rejected'})


def reviews_for_evidence(aptem_id, evidence_ids):
    ids = sorted({int(value) for value in evidence_ids})
    if not ids:
        return {}
    with connections['enrolment'].cursor() as cursor:
        cursor.execute('''select evidence_id, decision, feedback, reviewed_by, reviewed_at
            from login."Advanced_admin_legacy_marks"
            where aptem_id=%s and evidence_id=any(%s::bigint[])
            order by reviewed_at, id''', [str(aptem_id), ids])
        rows = cursor.fetchall()
    result = {}
    for evidence_id, decision, feedback, reviewed_by, reviewed_at in rows:
        result.setdefault(int(evidence_id), []).append({
            'decision': decision, 'feedback': feedback, 'reviewedBy': reviewed_by,
            'reviewedAt': reviewed_at.isoformat(),
        })
    return result
