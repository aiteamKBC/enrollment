"""Shared read model for the heavy Curriculum Home aggregation."""

from __future__ import annotations

from django.conf import settings

from read_models.outbox import enqueue_read_model_event
from read_models.registry import ReadModelSpec, register
from read_models.repository import get_read_model


MODEL_KEY = 'curriculum.home'
SCOPE_TYPE = 'curriculum'
SCHEMA_VERSION = 1
EVENT_REFRESH_REQUESTED = 'curriculum.home.refresh_requested'
VISIBILITIES = ('operational', 'all')


def curriculum_scope_id(visibility: str) -> str:
    value = str(visibility or '').strip().casefold()
    if value not in VISIBILITIES:
        raise ValueError('Unknown Curriculum visibility.')
    return value


def curriculum_home_scope_ids(limit: int):
    return list(VISIBILITIES[:max(min(int(limit), len(VISIBILITIES)), 0)])


def build_curriculum_home(scope_id: str) -> dict:
    """Build both initial Curriculum Home responses from one source snapshot."""
    from . import views

    visibility = curriculum_scope_id(scope_id)
    with views.curriculum_read_scope():
        overview = views.build_curriculum_payload(
            visibility, compact=True, force=True,
        )
        overview_response = {
            **overview,
            'modules': views.compact_module_rows(overview['modules']),
        }
        enriched_modules = views.enrich_modules_with_authoring(
            overview['modules'], include_programme_deleted=visibility == 'all',
        )
        programmes = views.enrich_programmes_with_module_counts(
            overview['programmes'],
            enriched_modules,
            modules_enriched=True,
            include_archived=visibility == 'all',
        )
    return {
        'overview': overview_response,
        'programmes': programmes,
    }


def register_curriculum_home_read_model() -> None:
    register(ReadModelSpec(
        model_key=MODEL_KEY,
        scope_type=SCOPE_TYPE,
        schema_version=SCHEMA_VERSION,
        ttl_seconds=max(int(getattr(settings, 'CURRICULUM_HOME_READ_MODEL_TTL_SECONDS', 30)), 1),
        builder=build_curriculum_home,
        scope_source=curriculum_home_scope_ids,
    ))


def get_curriculum_home(visibility: str):
    scope_id = curriculum_scope_id(visibility)
    return get_read_model(
        MODEL_KEY,
        SCOPE_TYPE,
        scope_id,
        schema_version=SCHEMA_VERSION,
        cache_ttl=max(int(getattr(settings, 'CURRICULUM_HOME_READ_MODEL_TTL_SECONDS', 30)), 1),
    )


def enqueue_curriculum_home_refresh(
    visibility: str, *, reason: str, using: str = 'default',
) -> bool:
    try:
        scope_id = curriculum_scope_id(visibility)
    except ValueError:
        return False
    return enqueue_read_model_event(
        event_type=EVENT_REFRESH_REQUESTED,
        model_key=MODEL_KEY,
        scope_type=SCOPE_TYPE,
        scope_id=scope_id,
        payload={'reason': str(reason or 'change')[:120]},
        using=using,
    )


def enqueue_all_curriculum_home_refreshes(*, reason: str, using: str = 'default') -> bool:
    queued = False
    for visibility in VISIBILITIES:
        queued = enqueue_curriculum_home_refresh(
            visibility, reason=reason, using=using,
        ) or queued
    return queued
