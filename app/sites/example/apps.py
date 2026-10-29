from django.apps import AppConfig


class ExampleSiteConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'sites.example'
    label = 'site_example'
