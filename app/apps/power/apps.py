from django.apps import AppConfig


class PowerConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.power'

    def ready(self) -> None:
        from apps.power import event_handlers  # noqa: F401
