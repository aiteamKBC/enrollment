"""Synthetic FK parents for integration tests; call only on a test-owned database.

Stable IDs are intentional where an existing fixture asserts wire identities.
They are inserted, never merely supplied by an ORM/query mock.
"""
import json
import uuid

from django.db import connection
from django.utils import timezone


def fixed_identity(model, **values):
    """Persist a named fixture ID even on GENERATED ALWAYS identity columns.

    This is PostgreSQL's supported explicit-identity INSERT, not a constraint
    bypass. Defaults and field serialization still come from the real model.
    """
    instance = model(**values)
    fields = [field for field in model._meta.concrete_fields if not field.generated]
    params = [field.get_db_prep_save(field.pre_save(instance, True), connection) for field in fields]
    columns = ','.join(connection.ops.quote_name(field.column) for field in fields)
    override = ' OVERRIDING SYSTEM VALUE' if connection.vendor == 'postgresql' else ''
    with connection.cursor() as cursor:
        cursor.execute(f'INSERT INTO {connection.ops.quote_name(model._meta.db_table)} ({columns})'
                       f'{override} VALUES ({",".join(["%s"] * len(fields))})', params)
    instance._state.adding = False
    instance._state.db = 'default'
    return instance


def review_identity(*, source_ids, aptem_id=6301, enrolment_id=121,
                    learner_kind="commercial", programme_id="P1", account_id=121):
    from learner_api.models import EnrolmentUser, LearnerProfile
    from login.models import LoginAccount

    enrolment = fixed_identity(EnrolmentUser,
        id=enrolment_id, username="Synthetic learner", email="learner@example.invalid",
        learner_type=learner_kind, aptem_id=str(aptem_id), coach_email="coach@example.invalid",
        programme_status="Active", learner_start_date="2026-10-01",
    )
    profile = fixed_identity(LearnerProfile,
        id=21, enrolment_id=enrolment.pk, full_name="Synthetic learner",
        email="learner@example.invalid", learner_type=learner_kind, aptem_id=aptem_id,
        programme_id=programme_id, programme="Synthetic programme", programme_status="Active",
        lifecycle_status="active", coach_email="coach@example.invalid",
    )
    account = fixed_identity(LoginAccount,
        id=account_id, subject_type="learner", subject_id=enrolment.pk,
        role="learner", email="learner@example.invalid", is_active=True,
    )
    with connection.cursor() as cursor:
        for source_id, aptem_review_id, family in source_ids:
            cursor.execute(
                'INSERT INTO "Learner".reviews '
                '(id, learner_id, aptem_review_id, review_name, review_type, status, review_data, extraction_status) '
                + ('OVERRIDING SYSTEM VALUE ' if connection.vendor == 'postgresql' else '')
                + 'VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
                [source_id, profile.pk, aptem_review_id, family, family, "Not Scheduled",
                 json.dumps({"aptem_learner_id": str(aptem_id)}), "partial"],
            )
    return profile, enrolment, account


def programme(programme_id):
    from curriculum_api import views
    # The production schema and the lightweight test schema have different
    # optional columns; both require a real programme parent.
    table = views.authoring_table_name("programmes")
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT * FROM {table} LIMIT 0")
        columns = {column[0] for column in cursor.description}
        values = {"id": programme_id, "programme_id": programme_id, "name": "Synthetic programme",
                  "color": "#123456", "created_at": timezone.now(), "updated_at": timezone.now()}
        values = {key: value for key, value in values.items() if key in columns}
        names = ",".join(connection.ops.quote_name(key) for key in values)
        cursor.execute(f"INSERT INTO {table} ({names}) VALUES ({','.join(['%s'] * len(values))})", list(values.values()))


def curriculum_session(test, client):
    """Use the real account/session contract; endpoint auth is never patched."""
    from learner_api.models import StaffUser
    from login.identity import ensure_account
    from login.models import LoginAccount, LoginSession
    from login.sessions import COOKIE_NAME, issue_session

    staff = StaffUser.objects.using("default").create(
        username="Synthetic curriculum admin", email=f"curriculum-{uuid.uuid4().hex}@example.invalid",
        access="super-admin", position="Caseowner", type="Admin", status="FullUser",
    )
    account, _ = ensure_account("staff", staff.pk, subject=staff)
    token, _, _ = issue_session(account)
    client.cookies[COOKIE_NAME] = token

    def cleanup():
        LoginSession.objects.filter(account_id=account.pk).delete()
        LoginAccount.objects.filter(pk=account.pk).delete()
        StaffUser.objects.filter(pk=staff.pk).delete()
    test.addCleanup(cleanup)
    return account
