from django.apps import AppConfig


class EngagementApiConfig(AppConfig):
    name = 'engagement_api'

    def ready(self):
        # Records rewards, recognition, events, clubs, points and flash cards in
        # the Audit Trail. Here rather than in system_audit because the models
        # have to be loaded before their signals can be connected, and this is
        # the app that owns them. Never fails a start-up: it logs and moves on.
        from system_audit.records import register_engagement_records
        register_engagement_records()
