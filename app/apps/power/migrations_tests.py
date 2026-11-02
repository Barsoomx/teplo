from django.apps import apps
from django.db.migrations.autodetector import MigrationAutodetector
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.state import ProjectState


def test_power_migrations_describe_the_models() -> None:
    loader = MigrationLoader(None, ignore_no_migrations=True)
    autodetector = MigrationAutodetector(loader.project_state(), ProjectState.from_apps(apps))

    assert autodetector.changes(graph=loader.graph, trim_to_apps={'power'}) == {}
