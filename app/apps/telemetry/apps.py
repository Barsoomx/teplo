from django.apps import AppConfig


class TelemetryConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.telemetry'

    def ready(self) -> None:
        from apps.telemetry import event_handlers  # noqa: F401
