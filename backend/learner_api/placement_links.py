"""A learner's placement by reference: programme_id, cohort_id and group_id.

The enrolment row ("Created_users") stores a placement as three names. The
learner profile ("Learner".learners) carries the ids those names resolved to
when the placement was written (see active_users.mirror_learner_placement), and
renaming a programme, cohort or group never changes an id. So a read that asks
"which cohort is this learner in" follows the id; a name is used only for a
learner whose profile has none.
"""
from .models import EnrolmentUser, LearnerProfile

FIELDS = ('programme_id', 'cohort_id', 'group_id')

# Cached on the learner object for the request, keyed by the placement names it
# held when resolved, so a placement edited on the same object is read again.
_CACHE = '_placement_ids'
_NAME_FIELDS = ('programme', 'cohort', 'group', 'group_name')


def _s(value):
    return str(value or '').strip()


def _id(value):
    # The id columns are text; anything else (None, a test double) is no id.
    return value.strip() if isinstance(value, str) else ''


def placement_ids(source):
    """``{'programme_id', 'cohort_id', 'group_id'}`` for one learner record.

    A profile, or anything else carrying the ids, answers for itself. An
    enrolment row reaches its profile through ``enrolment_id`` -- the explicit
    bridge -- and never by matching names, which is exactly what a rename
    breaks. An id is taken only when every profile on that enrolment agrees on
    it; otherwise it stays '' and the caller falls back to the name.
    """
    state = getattr(source, '__dict__', None)
    if state is None:
        state = {}
    # Read straight from __dict__: a deferred model field would otherwise cost
    # a query just to build the cache key.
    key = tuple(_s(state.get(name)) for name in _NAME_FIELDS)
    cached = state.get(_CACHE)
    if cached and cached[0] == key:
        return dict(cached[1])
    ids = {field: _id(getattr(source, field, '')) for field in FIELDS}
    enrolment_id = source.pk if isinstance(source, EnrolmentUser) else None
    if enrolment_id and not all(ids.values()):
        rows = list(
            LearnerProfile.objects.using('default')
            .filter(enrolment_id=enrolment_id)
            .values_list(*FIELDS)[:5]
        )
        for index, field in enumerate(FIELDS):
            values = {_id(row[index]) for row in rows} - {''}
            if not ids[field] and len(values) == 1:
                ids[field] = values.pop()
    try:
        state[_CACHE] = (key, dict(ids))
    except TypeError:
        pass
    return ids
