"""Provider screening setting. This never records or approves an RPL decision."""

from django.db import DatabaseError, IntegrityError, transaction
from django.utils import timezone

from programme_suitability.models import SkillsRadarScreeningSetting


KEY = 'potential_prior_learning'
DEFAULT_THRESHOLD = 50


def _missing_table(exc):
    cause = getattr(exc, '__cause__', None)
    return ((getattr(cause, 'sqlstate', None) or getattr(cause, 'pgcode', None)) == '42P01'
            or 'no such table: programme_skills_radar_screening_settings' in str(exc).lower())


def read_setting():
    try:
        row = SkillsRadarScreeningSetting.objects.filter(pk=KEY).first()
    except DatabaseError as exc:
        if not _missing_table(exc):
            raise
        return {'thresholdPercent': DEFAULT_THRESHOLD, 'revision': 0, 'schemaReady': False}
    return {'thresholdPercent': row.threshold_percent if row else DEFAULT_THRESHOLD,
            'revision': row.revision if row else 0, 'schemaReady': True}


def save_setting(threshold, revision, actor):
    if type(threshold) is not int or not 1 <= threshold <= 100:
        raise ValueError('Enter one whole-number threshold from 1 to 100%.')
    if type(revision) is not int or revision < 0:
        raise ValueError('Reload the setting before saving.')
    try:
        with transaction.atomic():
            # Lock the singleton key even when the row does not yet exist.
            # The first insert is protected by its primary-key constraint.
            row = SkillsRadarScreeningSetting.objects.select_for_update().filter(pk=KEY).first()
            current_revision = row.revision if row else 0
            if current_revision != revision:
                return None
            if row is None:
                row = SkillsRadarScreeningSetting(key=KEY)
            row.history = [*row.history, {'from': row.threshold_percent if current_revision else DEFAULT_THRESHOLD,
                                          'to': threshold, 'actor': actor, 'at': timezone.now().isoformat()}]
            row.threshold_percent = threshold
            row.revision = current_revision + 1
            row.updated_by = actor
            row.save()
            return {'thresholdPercent': threshold, 'revision': row.revision, 'schemaReady': True}
    except IntegrityError:
        # Two first-time editors may both read the absent row. Its primary key
        # decides the winner, and the other editor must reload.
        return None
    except DatabaseError as exc:
        if _missing_table(exc):
            return 'setup_required'
        raise
