from django.db import DEFAULT_DB_ALIAS, connections, router
from django.db.migrations.recorder import MigrationRecorder

from .catalog_models import CatalogProgramme


REQUIRED_MIGRATIONS = (
    '0013_programme_catalog', '0014_catalog_revisions', '0015_cohort_training_duration', '0016_cohort_date_overrides',
    '0017_catalog_programme_optional_duration',
    '0018_catalog_delivery_end_date',
)


def catalogue_schema_ready():
    alias = router.db_for_write(CatalogProgramme) or DEFAULT_DB_ALIAS
    recorder = MigrationRecorder(connections[alias])
    if not recorder.has_table():
        return False
    return recorder.migration_qs.filter(
        app='programme_suitability', name__in=REQUIRED_MIGRATIONS,
    ).values('name').distinct().count() == len(REQUIRED_MIGRATIONS)
