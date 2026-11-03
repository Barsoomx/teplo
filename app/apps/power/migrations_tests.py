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
WITH_LOADS = [('power', '0003_load_management')]
WITH_SWITCHABLE_PLUGS = [('power', '0004_load_switchable_plug')]
WITH_SHED_DEVICES = [('power', '0005_load_shed_device')]
EVENING = datetime(2026, 10, 31, 17, 24, tzinfo=UTC)
NIGHT = EVENING + timedelta(hours=5)
MARKED_AT = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
BOOT_ID = '5d1c2a7e-0b4f-4c8a-9e3d-6f2b1a0c9d8e'


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


@pytest.fixture
def outages_before_loads() -> Iterator[None]:
    executor = MigrationExecutor(connection)
    executor.migrate(WITH_BOOT_IDS)
    outages = executor.loader.project_state(WITH_BOOT_IDS).apps.get_model('power', 'Outage')._default_manager
    outages.create(
        began_at=EVENING,
        ended_at=EVENING + timedelta(minutes=9),
        cause='overload',
        cause_marked_at=MARKED_AT,
        boot_id=BOOT_ID,
    )
    yield
    executor = MigrationExecutor(connection)
    executor.migrate(executor.loader.graph.leaf_nodes())


@pytest.fixture
def loads_shed_before_shed_devices() -> Iterator[None]:
    executor = MigrationExecutor(connection)
    executor.migrate(WITH_SWITCHABLE_PLUGS)
    models = executor.loader.project_state(WITH_SWITCHABLE_PLUGS).apps
    devices = models.get_model('devices', 'Device')._default_manager
    loads = models.get_model('power', 'Load')._default_manager
    kitchen = models.get_model('climate', 'Zone')._default_manager.create(
        slug='kitchen',
        name='Кухня',
        kind='air',
        thermostat=devices.create(slug='thermostat-kitchen', name='Кухня', kind='thermostat', driver='tuya_thermostat'),
    )
    boiler_1f = devices.create(slug='plug-boiler-1f', name='Бойлер 1 этаж', kind='plug', driver='tuya_switchable_plug')
    boiler_2f = devices.create(slug='plug-boiler-2f', name='Бойлер 2 этаж', kind='plug', driver='tuya_switchable_plug')
    loads.create(
        slug='kitchen',
        name='Кухня',
        kind='zone',
        zone=kitchen,
        priority=5,
        shed_at=EVENING,
        shed_reason='overload',
        shed_phase='l2',
    )
    loads.create(
        slug='boiler-1f',
        name='Бойлер 1 этаж',
        kind='plug',
        device=boiler_1f,
        priority=1,
        shed_at=EVENING,
        shed_reason='overload',
        shed_phase='l2',
        shed_was_on=True,
    )
    loads.create(slug='boiler-2f', name='Бойлер 2 этаж', kind='plug', device=boiler_2f, priority=2)
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


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures('outages_before_loads')
def test_outages_keep_their_data_when_loads_arrive_and_no_load_is_made() -> None:
    executor = MigrationExecutor(connection)
    executor.migrate(WITH_LOADS)

    models = executor.loader.project_state(WITH_LOADS).apps
    outages = models.get_model('power', 'Outage')._default_manager
    assert (
        list(outages.values_list('began_at', 'ended_at', 'cause', 'cause_marked_at', 'boot_id')),
        models.get_model('power', 'Load')._default_manager.count(),
        models.get_model('power', 'LoadSettings')._default_manager.count(),
    ) == ([(EVENING, EVENING + timedelta(minutes=9), 'overload', MARKED_AT, BOOT_ID)], 0, 0)


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures('loads_shed_before_shed_devices')
def test_live_shed_stored_before_sheds_named_their_device_names_the_device_its_load_controls() -> None:
    executor = MigrationExecutor(connection)
    executor.migrate(WITH_SHED_DEVICES)

    loads = executor.loader.project_state(WITH_SHED_DEVICES).apps.get_model('power', 'Load')._default_manager
    assert list(loads.order_by('priority').values_list('slug', 'shed_at', 'shed_device')) == [
        ('boiler-1f', EVENING, 'plug-boiler-1f'),
        ('boiler-2f', None, ''),
        ('kitchen', EVENING, 'thermostat-kitchen'),
    ]
