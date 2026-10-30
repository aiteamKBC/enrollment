"""Read the current Aptem withdrawal state without rewriting local lifecycle.

The local Delivery/Active release process remains independent of Aptem Active.
An explicit current Withdrawn status takes precedence wherever we display it.
"""
from django.db import connections
from django.db.models import TextField
from django.db.models.expressions import RawSQL


def with_aptem_status(queryset):
    # This projection keeps the directory to one query, without loading plans
    # or fetching an external status separately for each learner.
    return queryset.annotate(_aptem_programme_status=RawSQL('''(
        SELECT a."Program-Status" FROM enrollment_reference.aptem_status a
        WHERE a."ID"::numeric = CASE
            WHEN btrim("enrolment"."Created_users".aptem_id::text) ~ '^[0-9]{1,19}$'
            THEN btrim("enrolment"."Created_users".aptem_id::text)::numeric
            ELSE NULL END
        LIMIT 1
    )''', [], output_field=TextField()))


def programme_status(source):
    """Resolve once per loaded record; never save or activate the learner."""
    local = str(getattr(source, 'programme_status', '') or '').strip()
    values = vars(source)
    if '_aptem_programme_status' not in values:
        # Don't trigger a deferred field fetch from a small directory row.
        # The real directory carries the annotation above instead.
        ident = str(values.get('aptem_id') or '').strip()
        status = None
        if ident.isascii() and ident.isdecimal() and 0 < int(ident) <= 9223372036854775807:
            with connections['enrolment'].cursor() as cursor:
                cursor.execute('SELECT "Program-Status" FROM enrollment_reference.aptem_status WHERE "ID"=%s', [int(ident)])
                row = cursor.fetchone()
            status = row[0] if row else None
        source._aptem_programme_status = status
    return 'Withdrawn' if str(source._aptem_programme_status or '').strip().casefold() == 'withdrawn' else local
