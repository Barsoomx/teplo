from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from django.apps import apps
from django.db import connection
from django.db.migrations.autodetector import MigrationAutodetector
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.state import ProjectState

BEFORE_BOOT_IDS = [('power', '0001_initial')]
WITH_BOOT_IDS = [('power', '0002_outage_boot_id')]
EVENING = datetime(2026, 10, 31, 17, 24, tzinfo=UTC)
NIGHT = EVENING + timedelta(hours=5)
MARKED_AT = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)


@pytest.fixture
def outages_before_boot_ids() -> Iterator[None]:
    executor = MigrationExecutor(connection)
    executor.migrate(BEFORE_BOOT_IDS)
    outages = executor.loader.project_state(BEFORE_BOOT_IDS).apps.get_model('power', 'Outage')._default_manager
    outages.create(began_at=EVENING, ended_at=EVENING + timedelta(minutes=9), cause='grid', cause_marked_at=MARKED_AT)
    outages.create(began_at=NIGHT, ended_at=NIGHT + timedelta(minutes=2))
    yield
    executor = MigrationExecutor(connection)
    executor.migrate(executor.loader.graph.leaf_nodes())


def test_power_migrations_describe_the_models() -> None:
    loader = MigrationLoader(None, ignore_no_migrations=True)
    autodetector = MigrationAutodetector(loader.project_state(), ProjectState.from_apps(apps))

    assert autodetector.changes(graph=loader.graph, trim_to_apps={'power'}) == {}


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures('outages_before_boot_ids')
def test_outages_recorded_before_boot_ids_keep_their_data_and_have_no_boot_id() -> None:
    executor = MigrationExecutor(connection)
    executor.migrate(WITH_BOOT_IDS)

    outages = executor.loader.project_state(WITH_BOOT_IDS).apps.get_model('power', 'Outage')._default_manager
    assert list(outages.order_by('pk').values_list('began_at', 'ended_at', 'cause', 'cause_marked_at', 'boot_id')) == [
        (EVENING, EVENING + timedelta(minutes=9), 'grid', MARKED_AT, None),
        (NIGHT, NIGHT + timedelta(minutes=2), 'unmarked', None, None),
    ]
