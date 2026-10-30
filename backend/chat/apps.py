from django.apps import AppConfig


class ChatConfig(AppConfig):
    """Application configuration for the private chat domain."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "chat"

    def ready(self):
        # Records conversations, messages and per-participant hides in the
        # Audit Trail. Here rather than in system_audit because the models
        # have to be loaded before their signals can be connected. Never fails
        # a start-up: it logs and moves on.
        from system_audit.records import register_chat_records
        register_chat_records()
