"""Email a tutor about a module they are assigned to.

Sent on request only: staff press "Email tutor" on the module workspace
(``module_tutor_email`` below). Assigning a tutor no longer mails them -- see
``schedule_assignment_notifications``, kept as a no-op so the write paths that
call it need not change. The reconcile machinery described next is retained
for ``dispatch_assignment_notifications`` and the ledger, which still records
every manual send.

Why a reconcile pass instead of a hook per write
------------------------------------------------
A tutor becomes attached to a module through several unrelated paths: the
programme creation/edit wizard (``save_tree_group_modules``), the staffing
screen (``update_staffing_assignment``), a group PATCH that carries ``tutor``,
a staff-profile POST/PATCH that carries ``assignedModuleIds``, and the mirror
rebuild that derives ``tutors.assigned_module_ids`` from
``curriculum.modules.tutor_name``. Hooking a "send mail" call into each of them
would mean five chances to miss one, and each of those paths writes several
modules in a loop -- so per-write hooks also produce a mail per module rather
than one mail per save.

Instead this module reconciles. After a write commits it compares the set of
(tutor, module) pairs that currently exist against a ledger of pairs already
mailed, and sends one grouped message per tutor for whatever is new. The pass is
idempotent, so calling it from an extra place costs a few reads and nothing
else, and a path nobody remembered to wire up is still covered the next time any
curriculum write happens.

The ledger
----------
``curriculum.tutor_module_notifications`` -- one row per (tutor identity,
module) pair that has been mailed. Rows are claimed *before* the send so a crash
mid-send cannot produce a duplicate, and a failed send is left recorded as
``failed`` with an attempt count so it retries on later passes but cannot loop
forever against a permanently bad address.

Rows for pairs that no longer exist are deleted, which is deliberate: a tutor
taken off a module and put back on it later is told about it again.

Seeding
-------
Migration ``0049`` creates the table already populated with every assignment
that existed at the time. Without that, the first reconcile after deployment
would read the entire back catalogue as "new" and mail every tutor about every
module they have ever been given. The runtime bootstrap path below seeds the
same way for local/SQLite provisioning.

Failure policy
--------------
Nothing here may break a curriculum save. The whole pass runs after commit and
swallows its own exceptions -- an assignment that saved but did not mail is a
notification bug, not a data-loss bug, and is recoverable on the next pass.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime

from django.db import connection, transaction
from django.http import JsonResponse

from login.permissions import require_role

from login import email_azure

from . import schema_gate

logger = logging.getLogger(__name__)

#: Ledger of pairs already mailed. Lives in the curriculum schema alongside the
#: tutors and modules it references.
NOTIFICATION_TABLE = 'tutor_module_notifications'

#: A failed send is retried on later passes, but not forever: a mailbox that has
#: been decommissioned would otherwise be retried on every curriculum write for
#: the life of the deployment.
MAX_SEND_ATTEMPTS = 3

#: Collapses the many writes of one save into a single pass. See
#: ``schedule_assignment_notifications``.
_state = threading.local()

_TABLE_READY = False


def _views():
    """Imported lazily -- ``views`` imports this module at load time."""
    from . import views

    return views


def workspace_url():
    """Where the mail points. Same env var the invitation mails already use."""
    base = (os.environ.get('FRONTEND_URL') or 'http://localhost:3000').rstrip('/')
    return f'{base}/workspace/tutor'


def notifications_enabled():
    """``TUTOR_ASSIGNMENT_EMAILS=false`` turns the whole feature off.

    Worth having when a production database is restored into staging: the
    reconcile would otherwise find a ledger that does not match the restored
    assignments and mail real tutors about a test system.
    """
    value = (os.environ.get('TUTOR_ASSIGNMENT_EMAILS') or 'true').strip().lower()
    return value not in {'0', 'false', 'no', 'off'}


# ---------------------------------------------------------------------------
# Ledger schema
# ---------------------------------------------------------------------------

def ensure_notification_table():
    """Verify the ledger exists; provision it only outside production.

    Schema is migration-owned, so
    a request path that finds it missing gets a named error rather than silently
    issuing DDL.
    """
    global _TABLE_READY
    if _TABLE_READY:
        return
    if not schema_gate.runtime_bootstrap_allowed():
        schema_gate.require_tables(NOTIFICATION_TABLE)
        _TABLE_READY = True
        return
    provision_notification_table()


def provision_notification_table():
    """Create the ledger (and seed it) for the test runner / local bootstrap."""
    global _TABLE_READY
    views = _views()
    already_present = views.table_exists(NOTIFICATION_TABLE)
    with connection.cursor() as cursor:
        if connection.vendor == 'postgresql':
            cursor.execute(
                f'create schema if not exists {views.quote_ident(views.CURRICULUM_SCHEMA)}'
            )
        cursor.execute(
            'create table if not exists '
            + views.table_name(NOTIFICATION_TABLE)
            + """ (
                id varchar(160) primary key,
                tutor_key varchar(320) not null default '',
                tutor_id varchar(128) not null default '',
                tutor_name varchar(255) not null default '',
                tutor_email varchar(255) not null default '',
                module_catalogue_id varchar(128) not null default '',
                status varchar(32) not null default 'sent',
                attempts integer not null default 0,
                detail text not null default '',
                created_at timestamp not null default current_timestamp,
                updated_at timestamp not null default current_timestamp
            )"""
        )
        cursor.execute(
            'create index if not exists curriculum_tutor_notify_key_idx on '
            + views.table_name(NOTIFICATION_TABLE)
            + ' (tutor_key)'
        )
    views._TABLE_COLUMNS_CACHE.pop(f'{views.CURRICULUM_SCHEMA}.{NOTIFICATION_TABLE}', None)
    _TABLE_READY = True
    if not already_present:
        seed_existing_assignments()


def ledger_id(tutor_key, module_id):
    """Primary key for a pair. Deterministic, so a concurrent insert collides."""
    return f'{tutor_key}|{module_id}'[:160]


def seed_existing_assignments():
    """Record every current assignment as already-notified, sending nothing.

    Called once, when the ledger is first created. See the module docstring.
    """
    views = _views()
    try:
        assignments = current_assignments()
    except Exception:
        logger.warning('Could not seed the tutor assignment ledger.', exc_info=True)
        return
    seeded = 0
    for tutor_key, entry in assignments.items():
        for module_id in entry['modules']:
            try:
                views.insert_row(NOTIFICATION_TABLE, {
                    'id': ledger_id(tutor_key, module_id),
                    'tutor_key': tutor_key,
                    'tutor_id': entry['tutor']['id'],
                    'tutor_name': entry['tutor']['name'],
                    'tutor_email': entry['tutor']['email'],
                    'module_catalogue_id': module_id,
                    'status': 'seeded',
                    'attempts': 0,
                    'detail': 'Recorded when the notification ledger was created.',
                })
                seeded += 1
            except Exception:
                logger.debug('Could not seed ledger row for %s.', module_id, exc_info=True)
    logger.info('Seeded %s existing tutor module assignments (no mail sent).', seeded)


# ---------------------------------------------------------------------------
# Current state
# ---------------------------------------------------------------------------

def current_assignments():
    """Every live tutor-to-module pair, keyed by tutor identity.

    Returns ``{tutor_key: {'tutor': {...}, 'modules': {catalogue_id: detail}}}``.

    Only tutors with an email address appear: there is nowhere to send for the
    others, and leaving them out keeps the pair out of the ledger so they are
    notified once an address is filled in.

    The tutors come from the staff directory and the assignment from
    ``curriculum.modules.tutor_name`` -- the module row is the only thing that
    says who teaches it, so there is no second list here to disagree with it.
    """
    views = _views()
    tutor_rows = views.get_staff_profile_rows('tutor')
    module_rows = views.safe_authoring_module_rows()
    if not tutor_rows or not module_rows:
        return {}

    groups_by_id = {}
    cohorts_by_id = {}
    try:
        groups_by_id = {
            views.clean_str(row.get('group_id')): row
            for row in views.authoring_fetch_all(views.GROUPS_TABLE)
            if views.clean_str(row.get('group_id'))
        }
        cohorts_by_id = {
            views.clean_str(row.get('cohort_id')): row
            for row in views.authoring_fetch_all(views.COHORT_AUTHORING_DETAILS_TABLE)
            if views.clean_str(row.get('cohort_id'))
        }
    except Exception:
        # Coach name and cohort dates are enrichment. Losing them costs two rows
        # of the mail; losing the mail costs the tutor the assignment.
        logger.debug('Could not read groups/cohorts for assignment mail.', exc_info=True)

    # Indexed once so the tutor loop below is a name comparison rather than a
    # row read per pair. Descriptions are built lazily, because only the handful
    # of modules that actually match ever need one.
    live_modules = []
    for module_row in module_rows:
        module_id = views.clean_str(module_row.get('module_catalogue_id'))
        deleted = (
            views.truthy(module_row.get('is_programme_deleted'))
            or views.row_has_deleted_at(module_row)
        )
        if not module_id or deleted:
            continue
        live_modules.append((
            module_id,
            views.staff_assignment_key(module_row.get('tutor_name')),
            module_row,
        ))

    assignments = {}
    for tutor in tutor_rows:
        name = views.staff_profile_name(tutor)
        email = views.staff_profile_email(tutor)
        tutor_key = views.staff_assignment_key(name) or email.lower()
        if not tutor_key or tutor_key == 'unassigned' or not email:
            continue
        modules = {}
        for module_id, module_tutor_key, module_row in live_modules:
            if module_tutor_key != tutor_key:
                continue
            modules[module_id] = describe_module(
                module_row,
                groups_by_id.get(views.clean_str(module_row.get('group_id'))),
                cohorts_by_id.get(views.clean_str(module_row.get('cohort_id'))),
            )
        if modules:
            assignments[tutor_key] = {
                'tutor': {
                    'id': views.clean_str(tutor.get('id')),
                    'name': name,
                    'email': email,
                },
                'modules': modules,
            }
    return assignments


def float_or_zero(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def format_hours(value):
    """``24.00`` -> "24 hours", ``22.50`` -> "22.5 hours", ``0``/junk -> "".

    total_otjh is numeric(8,2), so the raw value reads as "24.00" — correct, and
    not how anyone writes a number of hours in a sentence.
    """
    hours = float_or_zero(value)
    if not hours:
        return ''
    text = f'{hours:.2f}'.rstrip('0').rstrip('.')
    return f'{text} hours'


def describe_module(module_row, group_row=None, cohort_row=None):
    """Flatten a module row into the labelled facts the mail prints."""
    views = _views()
    group_row = group_row or {}
    cohort_row = cohort_row or {}

    schedule = views.build_group_schedule(
        module_row.get('session_week_day') or group_row.get('session_week_day'),
        module_row.get('session_start_time') or group_row.get('session_start_time'),
        module_row.get('session_end_time') or group_row.get('session_end_time'),
    )
    start = views.format_date(module_row.get('start_date'))
    end = views.format_date(module_row.get('end_date'))
    if start and end:
        dates = f'{start} to {end}'
    else:
        dates = start or end or ''
        if not dates:
            cohort_start = views.format_date(cohort_row.get('start_date'))
            cohort_end = views.format_date(cohort_row.get('end_date'))
            # Labelled explicitly: a tutor reading "2026-09-01 to 2027-06-30"
            # must not take the cohort's span for their own module's dates.
            if cohort_start and cohort_end:
                dates = f'cohort runs {cohort_start} to {cohort_end}'

    sessions = views.clean_str(module_row.get('sessions_number'))
    otjh = views.clean_str(module_row.get('total_otjh'))
    return {
        'name': views.clean_str(module_row.get('title')),
        'code': views.clean_str(module_row.get('module_catalogue_id')),
        'programme': views.clean_str(module_row.get('programme_name')),
        'cohort': (
            views.clean_str(module_row.get('cohort_name'))
            or views.clean_str(cohort_row.get('cohort_name'))
        ),
        'group': (
            views.clean_str(module_row.get('group_name'))
            or views.clean_str(group_row.get('group_name'))
        ),
        'schedule': schedule,
        'dates': dates,
        'sessions': sessions if float_or_zero(sessions) else '',
        'otjh': format_hours(otjh),
        'coach': views.clean_str(group_row.get('coach_name')),
    }


# ---------------------------------------------------------------------------
# Reconcile pass
# ---------------------------------------------------------------------------

def ledger_rows():
    views = _views()
    return views.fetch_all(f'select * from {views.table_name(NOTIFICATION_TABLE)}')


def schedule_assignment_notifications():
    """Queue one reconcile pass for after the current transaction commits.

    Registered on the transaction rather than run inline for two reasons: a save
    that later rolls back must not have mailed anybody, and a wizard save that
    writes twenty modules should mail once, not twenty times. Only the
    last-registered callback of a transaction does the work -- earlier ones see
    a newer token and return.

    If that last callback is discarded by a nested rollback the pass is simply
    skipped; because it is a full diff rather than an increment, the next
    curriculum write picks up whatever it missed.
    """
    # Automatic assignment emails are switched off: staff send them from the
    # module workspace when they choose to. Every assignment write still calls
    # this, so it stays callable and does nothing.
    return
    token = getattr(_state, 'token', 0) + 1
    _state.token = token

    def run():
        if getattr(_state, 'token', 0) != token:
            return
        _state.token = 0
        dispatch_assignment_notifications()

    try:
        transaction.on_commit(run)
    except Exception:
        logger.debug('Could not schedule tutor assignment notifications.', exc_info=True)


def dispatch_assignment_notifications():
    """Mail every tutor about assignments the ledger has not recorded yet.

    Never raises: this runs after a save has already committed, and a mail
    transport problem must not surface as a failed curriculum write.
    """
    if not notifications_enabled():
        return []
    try:
        return _dispatch()
    except Exception:
        logger.warning(
            'Tutor assignment notifications could not be dispatched.', exc_info=True
        )
        return []


def _dispatch():
    views = _views()
    ensure_notification_table()
    assignments = current_assignments()
    ledger = {views.clean_str(row.get('id')): row for row in ledger_rows()}

    live_ids = set()
    batches = []

    for tutor_key, entry in assignments.items():
        pending = []
        for module_id, detail in entry['modules'].items():
            row_id = ledger_id(tutor_key, module_id)
            live_ids.add(row_id)
            existing = ledger.get(row_id)
            if existing is None:
                pending.append((row_id, module_id, detail, 0))
                continue
            status = views.clean_str(existing.get('status')).lower()
            attempts = int(existing.get('attempts') or 0)
            if status == 'failed' and attempts < MAX_SEND_ATTEMPTS:
                pending.append((row_id, module_id, detail, attempts))
        if pending:
            batches.append(_notify(entry, pending))

    _forget_stale_rows(live_ids, ledger)
    return batches


def _notify(entry, pending):
    """Claim the pending pairs in the ledger, then mail one message for them.

    The claim happens first and unconditionally. If the process dies between the
    claim and the send the tutor misses a mail; the other order would re-send
    the same mail on every later pass, which is the worse of the two failures.
    """
    tutor = entry['tutor']
    modules = [detail for _, _, detail, _ in pending]

    for row_id, module_id, _, attempts in pending:
        _upsert_ledger_row(row_id, tutor, module_id, 'pending', attempts, '')

    subject, html, text = email_azure.tutor_assignment_message(
        tutor_name=tutor['name'],
        modules=modules,
        workspace_url=workspace_url(),
    )
    sent, detail = email_azure.send_mail(
        to=tutor['email'], subject=subject, html_body=html, text_body=text
    )

    status = 'sent' if sent else 'failed'
    for row_id, module_id, _, attempts in pending:
        _upsert_ledger_row(
            row_id,
            tutor,
            module_id,
            status,
            attempts + 1,
            '' if sent else (detail or '')[:500],
        )
    if sent:
        logger.info(
            'Emailed %s about %s newly assigned module(s).', tutor['email'], len(pending)
        )
    else:
        logger.warning(
            'Could not email %s about %s newly assigned module(s): %s',
            tutor['email'], len(pending), detail,
        )
    return {
        'tutor': tutor['email'],
        'modules': [module_id for _, module_id, _, _ in pending],
        'sent': sent,
    }


def _upsert_ledger_row(row_id, tutor, module_id, status, attempts, detail):
    views = _views()
    payload = {
        'tutor_key': row_id.split('|', 1)[0],
        'tutor_id': tutor['id'],
        'tutor_name': tutor['name'],
        'tutor_email': tutor['email'],
        'module_catalogue_id': module_id,
        'status': status,
        'attempts': attempts,
        'detail': detail,
        'updated_at': datetime.utcnow(),
    }
    updated = views.update_rows(NOTIFICATION_TABLE, 'id = %s', [row_id], payload)
    if not updated:
        try:
            views.insert_row(NOTIFICATION_TABLE, {'id': row_id, **payload})
        except Exception:
            # A concurrent pass claimed the same deterministic key first. That is
            # the collision doing its job, not an error.
            logger.debug('Ledger row %s already claimed.', row_id, exc_info=True)


def _forget_stale_rows(live_ids, ledger):
    """Drop ledger rows whose assignment no longer exists.

    Keeps a re-assignment notifiable, and stops the ledger growing without bound
    as modules and tutors are deleted.
    """
    views = _views()
    stale = [row_id for row_id in ledger if row_id and row_id not in live_ids]
    for row_id in stale:
        try:
            views.delete_rows(NOTIFICATION_TABLE, 'id = %s', [row_id])
        except Exception:
            logger.debug('Could not drop stale ledger row %s.', row_id, exc_info=True)


# ---------------------------------------------------------------------------
# Manual send, from the module workspace
# ---------------------------------------------------------------------------

def _module_tutor(module_id):
    """``(module_row, tutor or None, tutor_key)`` for a live module, or ``(None, None, '')``."""
    views = _views()
    module_id = views.clean_str(module_id)
    module_row = next((
        row for row in views.safe_authoring_module_rows() or []
        if views.clean_str(row.get('module_catalogue_id')) == module_id
        and not views.truthy(row.get('is_programme_deleted')) and not views.row_has_deleted_at(row)
    ), None)
    if module_row is None:
        return None, None, ''
    tutor_key = views.staff_assignment_key(module_row.get('tutor_name'))
    if not tutor_key or tutor_key == 'unassigned':
        return module_row, None, ''
    for profile in views.get_staff_profile_rows('tutor') or []:
        name = views.staff_profile_name(profile)
        if views.staff_assignment_key(name) == tutor_key:
            return module_row, {
                'id': views.clean_str(profile.get('id')), 'name': name,
                'email': views.staff_profile_email(profile),
            }, tutor_key
    return module_row, {'id': '', 'name': views.clean_str(module_row.get('tutor_name')), 'email': ''}, tutor_key


def _last_send(tutor_key, module_id):
    views = _views()
    ensure_notification_table()
    row = next((r for r in ledger_rows() if views.clean_str(r.get('id')) == ledger_id(tutor_key, module_id)), None)
    if row is None:
        return None
    updated = row.get('updated_at')
    return {
        'status': views.clean_str(row.get('status')).lower() or None,
        'at': updated.isoformat() if hasattr(updated, 'isoformat') else (views.clean_str(updated) or None),
    }


def module_tutor_status(module_id):
    module_row, tutor, tutor_key = _module_tutor(module_id)
    if module_row is None:
        return None
    return {
        'moduleId': module_id,
        'tutor': {'name': tutor['name'], 'hasEmail': bool(tutor['email'])} if tutor else None,
        'lastSent': _last_send(tutor_key, module_id) if tutor else None,
    }


def _tutor_profile_by_key(tutor_key):
    """The staff directory row for a tutor key, or None."""
    views = _views()
    if not tutor_key or tutor_key == 'unassigned':
        return None
    for profile in views.get_staff_profile_rows('tutor') or []:
        name = views.staff_profile_name(profile)
        if views.staff_assignment_key(name) == tutor_key:
            return {
                'id': views.clean_str(profile.get('id')), 'name': name,
                'email': views.staff_profile_email(profile),
            }
    return None


def tutor_email_status(module_ids, tutor_name=''):
    """Which of these deliveries this tutor has already been emailed about.

    The ledger is the only record of that, and it is keyed on (tutor, module),
    so the answer is per tutor by construction: a module whose tutor changed
    reports nothing for the new name, and the row the previous tutor earned is
    never read for them. That is what keeps the drawer's tick from carrying one
    tutor's "already notified" over to the next.

    ``tutor_name`` evaluates the state against a name that is not stored yet.
    The module drawer asks while the author is still choosing, and the honest
    answer for a tutor they have only selected is that tutor's record, not the
    one still written on the module row.

    Only a ``sent`` row counts as emailed. A ``failed`` one is a record that the
    attempt happened, not that the tutor heard anything -- so a delivery whose
    send failed reads as not emailed, and the drawer offers to send it again.
    """
    views = _views()
    ordered = []
    for value in module_ids or []:
        module_id = views.clean_str(value)
        if module_id and module_id not in ordered:
            ordered.append(module_id)

    override_key = views.staff_assignment_key(tutor_name) if views.clean_str(tutor_name) else ''
    tutor = _tutor_profile_by_key(override_key) if override_key else None
    if override_key and tutor is None:
        # A name the directory does not hold is still the name this module
        # carries; it simply has no address to write to.
        tutor = {'id': '', 'name': views.clean_str(tutor_name), 'email': ''}

    deliveries = []
    for module_id in ordered:
        if override_key:
            tutor_key = override_key
        else:
            _row, stored_tutor, tutor_key = _module_tutor(module_id)
            if tutor is None and stored_tutor is not None:
                tutor = stored_tutor
        last = _last_send(tutor_key, module_id) if tutor_key else None
        deliveries.append({
            'moduleId': module_id,
            'emailed': bool(last and last.get('status') == 'sent'),
            'lastSent': last,
        })

    sent_at = [
        d['lastSent']['at'] for d in deliveries
        if d['emailed'] and (d.get('lastSent') or {}).get('at')
    ]
    return {
        'tutor': {'name': tutor['name'], 'hasEmail': bool(tutor['email'])} if tutor else None,
        'total': len(deliveries),
        'emailed': sum(1 for d in deliveries if d['emailed']),
        # The most recent one the tutor actually received, so a module whose
        # deliveries were notified on different days reports the latest.
        'lastSentAt': max(sent_at) if sent_at else None,
        'deliveries': deliveries,
    }


def send_tutor_email(module_ids):
    """Email one tutor about these modules now. Returns ``(payload, http_status)``.

    One mail covers the whole list, because that is what being put on three
    groups' deliveries of the same module in one save actually is -- see
    ``email_azure.tutor_assignment_message``, which has always taken a list.
    Three separate mails arriving in the same second would describe the same
    decision three times.

    Every module must belong to the same tutor. The two callers both satisfy
    that by construction (one module, or one drawer save that put one name on
    all of them), so a mixed list is a programming error rather than something
    to paper over by sending several mails.

    Re-sending is allowed -- the workspace button asks first, and the drawer
    only sends what a person ticked -- and each module gets its own ledger row,
    so the workspace can still say when that module last went out.
    """
    views = _views()
    ordered = []
    for value in module_ids or []:
        module_id = views.clean_str(value)
        if module_id and module_id not in ordered:
            ordered.append(module_id)
    if not ordered:
        return {'error': 'No module was named.'}, 400

    resolved = []
    for module_id in ordered:
        module_row, tutor, tutor_key = _module_tutor(module_id)
        if module_row is None:
            return {'error': 'Module not found.'}, 404
        if tutor is None:
            return {'error': 'Assign a tutor to this module before emailing them.'}, 409
        if not tutor['email']:
            return {'error': f"{tutor['name']} has no email address in the staff directory."}, 409
        resolved.append((module_id, module_row, tutor, tutor_key))

    first_key = resolved[0][3]
    if any(key != first_key for _id, _row, _tutor, key in resolved):
        return {'error': 'These modules do not all have the same tutor.'}, 409
    tutor = resolved[0][2]

    groups = {views.clean_str(r.get('group_id')): r for r in views.authoring_fetch_all(views.GROUPS_TABLE)}
    cohorts = {views.clean_str(r.get('cohort_id')): r for r in views.authoring_fetch_all(views.COHORT_AUTHORING_DETAILS_TABLE)}
    details = [
        describe_module(
            module_row,
            groups.get(views.clean_str(module_row.get('group_id'))),
            cohorts.get(views.clean_str(module_row.get('cohort_id'))),
        )
        for _id, module_row, _tutor, _key in resolved
    ]

    ensure_notification_table()
    rows = ledger_rows()
    attempts_by_row = {}
    for module_id, _row, _tutor, tutor_key in resolved:
        row_id = ledger_id(tutor_key, module_id)
        previous = next((r for r in rows if views.clean_str(r.get('id')) == row_id), None)
        attempts_by_row[row_id] = (module_id, int((previous or {}).get('attempts') or 0))

    subject, html, text = email_azure.tutor_assignment_message(
        tutor_name=tutor['name'], modules=details, workspace_url=workspace_url(),
    )
    sent, failure = email_azure.send_mail(to=tutor['email'], subject=subject, html_body=html, text_body=text)
    # One mail, so one verdict: every module in it is recorded the same way.
    for row_id, (module_id, attempts) in attempts_by_row.items():
        _upsert_ledger_row(row_id, tutor, module_id, 'sent' if sent else 'failed', attempts + 1,
                           '' if sent else (failure or '')[:500])
    if not sent:
        logger.warning('Could not email %s about modules %s: %s', tutor['email'], ', '.join(ordered), failure)
        return {'error': f'The email to {tutor["name"]} could not be sent. Please try again.', 'code': 'send_failed'}, 502
    logger.info('Emailed %s about %d module(s) on request.', tutor['email'], len(ordered))
    return {
        'sent': True,
        'tutor': tutor['name'],
        'modules': len(ordered),
        'lastSent': _last_send(first_key, ordered[0]),
    }, 200


def send_module_tutor_email(module_id):
    """Email this one module's tutor about it now. See ``send_tutor_email``."""
    return send_tutor_email([module_id])


@require_role('admin', 'staff')
def modules_tutor_email(request):
    """GET: whether this tutor has been emailed about these deliveries yet.
    POST: one mail to one tutor covering several modules (CSRF protected).

    The module drawer's "email the tutor" tick, both halves. The tick is not a
    form field the drawer remembers -- it reads its state from here every time
    it opens, because the ledger is the only place that knows whether the tutor
    was actually written to, and it has to still know next week.

    One drawer save can attach the module to several groups at once -- one
    delivery per group, all carrying the same tutor -- so both halves take a
    list: the tutor hears about that once, and the tick reports "2 of 3" rather
    than pretending the module is one thing.

    Deliberately a route of its own rather than a body on the per-module one:
    that endpoint's URL names the module it acts on, and the workspace button
    depends on it staying that way.
    """
    if request.method == 'GET':
        raw = request.GET.get('moduleIds') or ''
        module_ids = [part for part in raw.split(',') if part.strip()]
        try:
            return JsonResponse(tutor_email_status(module_ids, request.GET.get('tutor') or ''), status=200)
        except Exception:
            logger.exception('Could not read the tutor email status for modules %s', module_ids)
            return JsonResponse({'error': 'The tutor email status could not be loaded.'}, status=502)
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed.'}, status=405)
    body = {}
    try:
        body = json.loads((request.body or b'').decode('utf-8') or '{}')
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({'error': 'Invalid JSON body.'}, status=400)
    module_ids = body.get('moduleIds') if isinstance(body, dict) else None
    if not isinstance(module_ids, list):
        return JsonResponse({'error': 'moduleIds must be a list.'}, status=400)
    try:
        payload, status = send_tutor_email(module_ids)
    except Exception:
        logger.exception('Could not email the tutor of modules %s', module_ids)
        return JsonResponse({'error': 'The email could not be sent. Please try again.'}, status=502)
    return JsonResponse(payload, status=status)


@require_role('admin', 'staff')
def module_tutor_email(request, module_id):
    """GET: who the tutor is and when they were last emailed. POST: email them now (CSRF protected)."""
    if request.method == 'GET':
        try:
            status = module_tutor_status(module_id)
        except Exception:
            logger.exception('Could not read the tutor email status for module %s', module_id)
            return JsonResponse({'error': 'The tutor email status could not be loaded.'}, status=502)
        return JsonResponse(status, status=200) if status else JsonResponse({'error': 'Module not found.'}, status=404)
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed.'}, status=405)
    try:
        payload, status = send_module_tutor_email(module_id)
    except Exception:
        logger.exception('Could not email the tutor of module %s', module_id)
        return JsonResponse({'error': 'The email could not be sent. Please try again.'}, status=502)
    return JsonResponse(payload, status=status)
