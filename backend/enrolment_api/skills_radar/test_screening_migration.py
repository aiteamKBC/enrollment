"""Exercise forward compatibility on Django's isolated SQLite test database."""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class ScreeningMigrationTests(TransactionTestCase):
    def _fixture_teardown(self):
        # The isolated suitability runner creates only feature tables. Django's
        # global flush would also require auth/contenttypes tables it omits.
        # This test removes its own row and restores the latest schema below.
        pass

    def test_existing_percentage_row_gains_count_and_rating_defaults(self):
        old_target = [('programme_suitability', '0020_skills_radar_screening_setting')]
        count_target = [('programme_suitability', '0021_skills_radar_hard_ksb_screening_count')]
        new_target = [('programme_suitability', '0022_skills_radar_screening_rating_bounds')]
        executor = MigrationExecutor(connection)
        try:
            executor.migrate(old_target)
            old_apps = executor.loader.project_state(old_target).apps
            OldSetting = old_apps.get_model('programme_suitability', 'SkillsRadarScreeningSetting')
            original_history = [{'from': 50, 'to': 63, 'actor': 'old-admin'}]
            OldSetting.objects.create(key='potential_prior_learning', threshold_percent=63,
                                      revision=7, history=original_history, updated_by='old-admin')
            executor = MigrationExecutor(connection)
            executor.migrate(count_target)
            count_apps = executor.loader.project_state(count_target).apps
            CountSetting = count_apps.get_model('programme_suitability', 'SkillsRadarScreeningSetting')
            CountSetting.objects.filter(pk='potential_prior_learning').update(minimum_high_rated_hard_ksbs=4)
            executor = MigrationExecutor(connection)
            executor.migrate(new_target)
            new_apps = executor.loader.project_state(new_target).apps
            NewSetting = new_apps.get_model('programme_suitability', 'SkillsRadarScreeningSetting')
            row = NewSetting.objects.get(pk='potential_prior_learning')
            self.assertEqual((row.threshold_percent, row.revision, row.updated_by), (63, 7, 'old-admin'))
            self.assertEqual(row.history, original_history)
            self.assertEqual((row.minimum_high_rated_hard_ksbs, row.minimum_qualifying_rating,
                              row.maximum_qualifying_rating), (4, 5, 9))
        finally:
            MigrationExecutor(connection).migrate(new_target)
            from programme_suitability.models import SkillsRadarScreeningSetting
            SkillsRadarScreeningSetting.objects.filter(pk='potential_prior_learning').delete()
