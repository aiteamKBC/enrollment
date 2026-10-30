from django.apps import AppConfig
from django.conf import settings
from django.core import checks


def _database_identity(config):
    engine = str(config.get("ENGINE") or "")
    host = str(config.get("HOST") or "").lower()
    # Neon pooled and direct endpoints point to the same branch even though the
    # pooled hostname carries the `-pooler` suffix.
    host = host.replace("-pooler.", ".")
    port = str(config.get("PORT") or "")
    if engine.endswith("postgresql") and not port:
        port = "5432"
    return (
        engine,
        str(config.get("NAME") or ""),
        str(config.get("USER") or ""),
        host,
        port,
    )


@checks.register()
def read_model_outbox_database_check(app_configs, **kwargs):
    """The worker can only consume aliases that share its physical database."""
    if not getattr(settings, "READ_MODEL_OUTBOX_ENABLED", False):
        return []
    databases = getattr(settings, "DATABASES", {})
    default = databases.get("default")
    enrolment = databases.get("enrolment")
    if default and enrolment and _database_identity(default) != _database_identity(enrolment):
        return [checks.Error(
            "The read-model outbox requires default and enrolment to use the same database branch.",
            hint=(
                "Point both aliases at the same Neon branch before enabling "
                "READ_MODEL_OUTBOX_ENABLED."
            ),
            id="read_models.E001",
        )]
    return []


class ReadModelsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "read_models"

