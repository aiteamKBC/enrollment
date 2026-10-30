from django.apps import AppConfig


class LoginConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'login'
    # Django's own auth app is 'auth'; this app's label must not collide with it
    # or with the "auth" PostgreSQL schema its tables live in.
    label = 'login'
    verbose_name = 'Platform login'
