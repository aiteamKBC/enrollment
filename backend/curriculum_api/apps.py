import os
import sys

from django.apps import AppConfig
from django.conf import settings
from config.management_safety import reconciliation_command


class CurriculumApiConfig(AppConfig):
    name = 'curriculum_api'

    def ready(self):
        """Warm the curriculum payload caches for this worker, in the background.

        Without this the first request after a deploy or restart pays the whole
        multi-table rebuild while every other request on the page queues behind
        its build lock. The warm runs on a daemon thread, so a slow or failing
        database delays nothing and blocks no worker from accepting requests.

        Skipped for management commands that are not serving traffic. `migrate`
        in particular must not have a thread reading tables it is in the middle
        of altering, and `test` has its own reason (see CURRICULUM_WARM in
        config/settings.py).
        """
        from .read_model import register_curriculum_home_read_model
        register_curriculum_home_read_model()

        if reconciliation_command():
            return

        command = sys.argv[1] if len(sys.argv) > 1 else ''
        if command in {
            'migrate', 'makemigrations', 'test', 'collectstatic', 'shell',
            'dbshell', 'createsuperuser', 'showmigrations', 'sqlmigrate',
            'flush', 'loaddata', 'dumpdata', 'check', 'diffsettings',
            'rebuild_read_model', 'reconcile_read_models',
            'process_read_model_events',
            'import_global_migrated_review_template',
            'enable_personal_support_form',
        }:
            return
        # runserver spawns a reloader child that does the actual serving; the
        # parent would only warm a cache it never reads from.
        if command == 'runserver' and os.environ.get('RUN_MAIN') != 'true':
            return
        self.log_cache_backend()
        # Once reads have switched to the persistent projection, its worker is
        # the only component that should pay the expensive rebuild. The old
        # process-local warm remains the staged-rollout fallback while the flag
        # is off.
        if getattr(settings, 'CURRICULUM_HOME_SHARED_READ_MODEL_ENABLED', False):
            return
        from .views import schedule_curriculum_warm
        schedule_curriculum_warm()

    def log_cache_backend(self):
        """Say once, per worker, whether the payload cache is shared.

        `cached_curriculum_value` stores every built payload in Django's cache as
        well as in process memory, so that the other workers can read what one of
        them built. Without a `CACHE_URL` that cache is LocMemCache, which is
        private to the process -- so under nine two-worker services each of the
        eighteen builds its own copy and a reader keeps landing on a cold one.

        Nothing here changes that; it is a `.env` line and a Redis instance. What
        this does is make which one is running a fact in the log rather than an
        assumption, because the two are indistinguishable from the outside except
        by how often the page is slow.
        """
        import logging

        from django.conf import settings

        backend = (settings.CACHES.get('default') or {}).get('BACKEND', '')
        shared = 'redis' in backend.lower()
        logging.getLogger('curriculum_api.views').info(
            'Curriculum payload cache: %s (%s). %s',
            backend.rsplit('.', 1)[-1] or 'unknown',
            'shared between workers' if shared else 'process-local',
            '' if shared else 'Set CACHE_URL to share built payloads across workers.',
        )
