"""Startup checks for the two-database assumption (ENROLMENT_GAP_ANALYSIS.md 7.2).

EnrolmentRouter sends every learner_api/enrolment_api model read and write to the
`enrolment` alias. But three call sites deliberately read through `default`,
because they query schemas the router does not govern:

  * learning_plan._rows                    -> curriculum.modules, curriculum.groups
  * training_plan_document._module_breakdown -> curriculum.weeks, curriculum.components
  * absence_reports                        -> "Learner".learner_attendance_details

`curriculum` is owned by curriculum_api, whose 11 migrations run on `default`, so
those reads belong there. That is only coherent while both aliases resolve to the
same Neon instance — which is what .env does today by setting a single
Database_url that both resolution chains fall back to.

Point ENROLMENT_DATABASE_URL somewhere else and the arrangement breaks silently:
the ORM reads enrolment.* from one database while raw SQL reads curriculum.* and
"Learner".* from another. Nothing in the code would notice. This check makes it
loud instead.

It warns rather than errors: a split may be a deliberate, planned migration, and
a hard error would make the app unstartable at exactly the moment someone is
working on it. Silence is the failure mode worth removing, not startup.
"""
from django.core.checks import Warning as CheckWarning
from django.core.checks import register

# Raw SQL on `default` that reads schemas outside the router's control. Kept here
# so the check's message can name them, and so this list is the one place to
# update if a call site moves.
CROSS_SCHEMA_READERS = (
    "learning_plan._rows (curriculum.modules, curriculum.groups)",
    "training_plan_document._module_breakdown (curriculum.weeks, curriculum.components)",
    'absence_reports (\"Learner\".learner_attendance_details)',
)


#: Postgres' default port. The two aliases are built by different code paths --
#: `default` is assembled inline and normalises a missing port to "5432", while
#: `enrolment` goes through database_from_url, which leaves it empty. Comparing
#: the raw values reports every normal single-database setup as a split, so the
#: port is normalised before comparison. A check that fires on every startup
#: teaches people to ignore it, which is worse than not having it.
_DEFAULT_PG_PORT = "5432"


def _endpoint(config):
    """The (host, port, name) a database alias resolves to, normalised."""
    return (
        (config.get("HOST") or "").strip().lower(),
        str(config.get("PORT") or "").strip() or _DEFAULT_PG_PORT,
        (config.get("NAME") or "").strip(),
    )


@register()
def check_enrolment_alias_matches_default(app_configs, **kwargs):
    from django.conf import settings

    databases = getattr(settings, "DATABASES", {}) or {}

    # Test runs force both aliases to SQLite and drop `enrolment` entirely; the
    # invariant does not apply and warning on every test run would train people
    # to ignore it.
    if getattr(settings, "USE_SQLITE_FOR_TESTS", False):
        return []

    default = databases.get("default")
    enrolment = databases.get("enrolment")

    if not default:
        return []

    if not enrolment:
        return [
            CheckWarning(
                "The `enrolment` database alias is not configured.",
                hint=(
                    "EnrolmentRouter routes every learner_api/enrolment_api model "
                    "to `enrolment`, so without it those queries raise "
                    "ConnectionDoesNotExist. Set ENROLMENT_DATABASE_URL, or "
                    "Database_url/DATABASE_URL which it falls back to."
                ),
                id="learner_api.W001",
            )
        ]

    if _endpoint(default) == _endpoint(enrolment):
        return []

    readers = "\n  - ".join(CROSS_SCHEMA_READERS)
    return [
        CheckWarning(
            "`default` and `enrolment` point at different databases, but raw SQL "
            "still reads non-enrolment schemas through `default`.",
            hint=(
                f"default={_endpoint(default)} enrolment={_endpoint(enrolment)}.\n"
                "These read curriculum.* / \"Learner\".* on `default` while the "
                f"ORM reads enrolment.* on `enrolment`:\n  - {readers}\n"
                "If the split is intended, move those schemas onto the same "
                "instance as `enrolment`, or give each reader an explicit alias. "
                "See ENROLMENT_GAP_ANALYSIS.md 7.2."
            ),
            id="learner_api.W002",
        )
    ]


@register()
def check_sqlite_mode_is_not_used_for_ddl(app_configs, **kwargs):
    """DJANGO_USE_SQLITE cannot run the apply_* commands.

    Every apply_* command starts with CREATE SCHEMA IF NOT EXISTS, which SQLite
    rejects outright, and the enrolment tables use jsonb/timestamptz/bigserial.
    Flagging it here means the failure is explained up front rather than as a
    bare syntax error partway through a deployment.
    """
    import sys

    from django.conf import settings

    if not getattr(settings, "USE_SQLITE_FOR_TESTS", False):
        return []

    argv = " ".join(sys.argv)
    if "apply_" not in argv and "create_created_users_table" not in argv:
        return []

    return [
        CheckWarning(
            "DJANGO_USE_SQLITE is set, but the schema commands need PostgreSQL.",
            hint=(
                "CREATE SCHEMA is not valid SQLite, and these tables use "
                "jsonb/timestamptz/bigserial. Unset DJANGO_USE_SQLITE and point "
                "Database_url at the Neon instance."
            ),
            id="learner_api.W003",
        )
    ]
