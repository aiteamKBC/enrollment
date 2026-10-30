"""Shared read model for the heavy Learner Home aggregation."""

from __future__ import annotations

from django.conf import settings

from read_models.outbox import enqueue_read_model_event
from read_models.registry import ReadModelSpec, register
from read_models.repository import get_read_model


MODEL_KEY = 'learner.home'
SCOPE_TYPE = 'learner'
SCHEMA_VERSION = 1
EVENT_REFRESH_REQUESTED = 'learner.home.refresh_requested'
KINDS = frozenset({'apprenticeship', 'commercial'})


def learner_scope_id(kind: str, learner_id) -> str:
    kind = str(kind or '').strip().casefold()
    if kind not in KINDS:
        raise ValueError('Unknown learner kind.')
    try:
        learner_id = int(learner_id)
    except (TypeError, ValueError) as exc:
        raise ValueError('Invalid learner id.') from exc
    if learner_id <= 0:
        raise ValueError('Invalid learner id.')
    return f'{kind}:{learner_id}'


def parse_learner_scope_id(scope_id: str) -> tuple[str, int]:
    kind, separator, raw_id = str(scope_id or '').partition(':')
    if not separator:
        raise ValueError('Invalid learner scope.')
    canonical = learner_scope_id(kind, raw_id)
    return canonical.split(':', 1)[0], int(raw_id)


def learner_home_scope_ids(limit: int):
    from .models import EnrolmentUser

    rows = EnrolmentUser.all_learners.order_by('id').values_list('id', 'learner_type')[:max(int(limit), 1)]
    return [
        learner_scope_id(
            'commercial' if str(learner_type or '').casefold() == 'commercial' else 'apprenticeship',
            learner_id,
        )
        for learner_id, learner_type in rows
    ]


def build_learner_home(scope_id: str) -> dict:
    from .learner_detail import SOURCE_MODELS
    from .overview_week import HOME_SOURCE_FIELDS, read_week

    kind, learner_id = parse_learner_scope_id(scope_id)
    model = SOURCE_MODELS[kind]
    source = model.all_learners.only(*HOME_SOURCE_FIELDS).get(pk=learner_id)
    return read_week(source, home_kind=kind)


def register_learner_home_read_model() -> None:
    register(ReadModelSpec(
        model_key=MODEL_KEY,
        scope_type=SCOPE_TYPE,
        schema_version=SCHEMA_VERSION,
        ttl_seconds=max(int(getattr(settings, 'LEARNER_HOME_READ_MODEL_TTL_SECONDS', 30)), 1),
        builder=build_learner_home,
        scope_source=learner_home_scope_ids,
    ))


def get_learner_home(kind: str, learner_id):
    scope_id = learner_scope_id(kind, learner_id)
    return get_read_model(
        MODEL_KEY,
        SCOPE_TYPE,
        scope_id,
        schema_version=SCHEMA_VERSION,
        cache_ttl=max(int(getattr(settings, 'LEARNER_HOME_READ_MODEL_TTL_SECONDS', 30)), 1),
    )


def enqueue_learner_home_refresh(
    kind: str, learner_id, *, reason: str, using: str = 'default',
) -> bool:
    try:
        scope_id = learner_scope_id(kind, learner_id)
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
