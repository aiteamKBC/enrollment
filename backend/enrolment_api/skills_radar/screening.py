"""Provider screening settings. These never record or approve an RPL decision."""

from django.db import DatabaseError, IntegrityError, transaction
from django.utils import timezone

from programme_suitability.models import SkillsRadarScreeningSetting


KEY = 'potential_prior_learning'
MAX_HARD_KSBS = 500  # Supported KSB catalogue limit per programme.
DEFAULT_RULE = {'minimumQualifyingRating': 5, 'maximumQualifyingRating': 9,
                'minimumHighRatedHardKsbs': 2}
NEW_COLUMNS = ('minimum_high_rated_hard_ksbs', 'minimum_qualifying_rating', 'maximum_qualifying_rating')


def _missing_schema(exc):
    cause = getattr(exc, '__cause__', None)
    state = getattr(cause, 'sqlstate', None) or getattr(cause, 'pgcode', None)
    message = str(exc).lower()
    return (state == '42P01' or (state == '42703' and any(name in message for name in NEW_COLUMNS)) or
            'no such table: programme_skills_radar_screening_settings' in message or
            ('no such column:' in message and any(name in message for name in NEW_COLUMNS)))


def _response(row=None, *, schema_ready=True, count=None, revision=None):
    return {
        'minimumQualifyingRating': row.minimum_qualifying_rating if row and schema_ready else DEFAULT_RULE['minimumQualifyingRating'],
        'maximumQualifyingRating': row.maximum_qualifying_rating if row and schema_ready else DEFAULT_RULE['maximumQualifyingRating'],
        'minimumHighRatedHardKsbs': (row.minimum_high_rated_hard_ksbs if row else
                                       count if count is not None else DEFAULT_RULE['minimumHighRatedHardKsbs']),
        'revision': row.revision if row else revision if revision is not None else 0,
        'schemaReady': schema_ready,
    }


def read_setting():
    try:
        row = SkillsRadarScreeningSetting.objects.filter(pk=KEY).first()
    except DatabaseError as exc:
        if not _missing_schema(exc):
            raise
        # 0021 may contain a customised count even when 0022 is still pending.
        try:
            legacy = SkillsRadarScreeningSetting.objects.filter(pk=KEY).values(
                'minimum_high_rated_hard_ksbs', 'revision').first()
        except DatabaseError as prior_exc:
            if not _missing_schema(prior_exc):
                raise
            return _response(schema_ready=False)
        return _response(schema_ready=False, count=legacy['minimum_high_rated_hard_ksbs'] if legacy else None,
                         revision=legacy['revision'] if legacy else None)
    return _response(row)


def save_setting(data, actor):
    if not isinstance(data, dict):
        raise ValueError('Provide all three screening settings and the current revision.')
    low = data.get('minimumQualifyingRating')
    high = data.get('maximumQualifyingRating')
    count = data.get('minimumHighRatedHardKsbs')
    revision = data.get('revision')
    if type(low) is not int or not 1 <= low <= 9:
        raise ValueError('Minimum qualifying rating must be a whole number from 1 to 9.')
    if type(high) is not int or not 1 <= high <= 9:
        raise ValueError('Maximum qualifying rating must be a whole number from 1 to 9.')
    if low > high:
        raise ValueError('Minimum qualifying rating must not exceed maximum qualifying rating.')
    if type(count) is not int or not 1 <= count <= MAX_HARD_KSBS:
        raise ValueError(f'Minimum qualifying Hard KSBs must be a whole number from 1 to {MAX_HARD_KSBS}.')
    if type(revision) is not int or revision < 0:
        raise ValueError('Reload the setting before saving.')
    try:
        with transaction.atomic():
            row = SkillsRadarScreeningSetting.objects.select_for_update().filter(pk=KEY).first()
            current_revision = row.revision if row else 0
            if current_revision != revision:
                return None
            if row is None:
                row = SkillsRadarScreeningSetting(key=KEY)
            previous = {'minimumQualifyingRating': row.minimum_qualifying_rating,
                        'maximumQualifyingRating': row.maximum_qualifying_rating,
                        'minimumHighRatedHardKsbs': row.minimum_high_rated_hard_ksbs}
            row.history = [*row.history, {'unit': 'screening_rule', 'from': previous,
                                          'to': {'minimumQualifyingRating': low,
                                                 'maximumQualifyingRating': high,
                                                 'minimumHighRatedHardKsbs': count},
                                          'actor': actor, 'at': timezone.now().isoformat()}]
            row.minimum_qualifying_rating = low
            row.maximum_qualifying_rating = high
            row.minimum_high_rated_hard_ksbs = count
            row.revision = current_revision + 1
            row.updated_by = actor
            row.save()
            return _response(row)
    except IntegrityError:
        # Two first-time editors may both read the absent row. Its primary key
        # decides the winner, and the other editor must reload.
        return None
    except DatabaseError as exc:
        if _missing_schema(exc):
            return 'setup_required'
        raise
