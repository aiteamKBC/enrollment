"""psycopg3 ``json``/``jsonb`` read compatibility — see SECURITY_AUDIT.md A22.

psycopg 3 decodes BOTH ``json`` and ``jsonb`` columns into Python objects, but
Django's PostgreSQL backend registers a text loader for ``jsonb`` ONLY
(``psycopg_any.get_adapters_template`` -> ``register_loader("jsonb", TextLoader)``).
A ``json`` column therefore reaches Django's ``JSONField.from_db_value`` as an
already-parsed dict, which then calls ``json.loads(dict)`` and raises
``TypeError: the JSON object must be str, bytes or bytearray, not dict`` -> 500.

``enrolment."Created_users"`` has 23 ``json`` columns, so every read that loads a
full learner row (learner-detail, enrolment-users, employer-portal, reviews,
calendar, learning-plan) 500s. This registers a TEXT loader for BOTH ``json`` and
``jsonb`` on psycopg's GLOBAL adapters, which Django copies into the template it
hands to every connection — crucially including POOLED connections, which take
that template via the pool's ``kwargs`` (``base.py`` builds the pool with
``kwargs=self.get_connection_params()``, and those params carry ``context``).
JSONField/SafeJSONField then reliably receive a ``str`` and parse it themselves.

Side-effect only and idempotent; call once from ``AppConfig.ready()``.

LOAD-ORDER CAVEAT (see SECURITY_AUDIT.md A22): this registers on the GLOBAL
``psycopg.adapters`` and clears Django's cached template, so every connection
opened AFTER this ``ready()`` runs gets the loader — pooled or direct, on any
alias. It cannot retrofit a connection that was already OPEN before it ran. In
this codebase nothing opens a DB connection before ``learner_api.ready()``, so
the request path is safe; but that is a load-order property, not a guarantee. If
an app that queries the DB in its own ``ready()`` is ever placed before
``learner_api`` in ``INSTALLED_APPS``, that one early connection would keep the
pre-registration adapters until it is recycled. The bulletproof placement is at
settings-import time or a ``connection_created`` signal handler; it was left here
because the fix is proven working and moving it is a pre-M2 risk not worth taking.
"""
from __future__ import annotations


def register_json_text_loaders() -> None:
    from psycopg import adapters
    from psycopg.types.string import TextLoader

    # Return the raw text for both json types; Django's field parses it.
    adapters.register_loader("json", TextLoader)
    adapters.register_loader("jsonb", TextLoader)

    # Django's per-connection adapters template is lru_cached and copies the
    # global adapters the first time it is built. Clear it so the next
    # connection (pooled or direct) rebuilds the template with the json loader
    # included, independent of import/connection ordering.
    try:
        from django.db.backends.postgresql.psycopg_any import get_adapters_template

        get_adapters_template.cache_clear()
    except (ImportError, AttributeError):  # pragma: no cover - psycopg2 fallback
        pass
