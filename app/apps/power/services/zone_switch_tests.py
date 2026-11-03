from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from django.db import OperationalError
from structlog.testing import capture_logs

from apps.climate.factories import ZoneFactory
from apps.climate.models import Zone
from apps.climate.repositories.zone import ZoneRepository
from apps.devices.domain.errors import DeviceUnavailableError, DriverNotImplementedError
from apps.devices.stubs import FloorThermostatStub, ThermostatProviderStub
from apps.power.services.zone_switch import ThermostatZoneSwitch
from apps.power.stubs import ZoneWriteStampsStub

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 11, 2, 18, 40, tzinfo=UTC)


@pytest.fixture
def f_kitchen() -> Zone:
    return ZoneFactory.create(
        slug='kitchen',
        thermostat__slug='thermostat-kitchen',
        last_command_at=NOW - timedelta(minutes=5),
    )


@pytest.fixture
def f_thermostat() -> FloorThermostatStub:
    return FloorThermostatStub(state=None)


@pytest.fixture
def unreachable_thermostat() -> FloorThermostatStub:
    return FloorThermostatStub(state=None, error=DeviceUnavailableError('timed out'))


@pytest.fixture
def f_switch(f_thermostat: FloorThermostatStub) -> ThermostatZoneSwitch:
    return ThermostatZoneSwitch(
        stamps=ZoneRepository(),
        thermostats=ThermostatProviderStub(thermostats={'thermostat-kitchen': f_thermostat}),
    )


@pytest.fixture
def f_unreachable_switch(unreachable_thermostat: FloorThermostatStub) -> ThermostatZoneSwitch:
    return ThermostatZoneSwitch(
        stamps=ZoneRepository(),
        thermostats=ThermostatProviderStub(thermostats={'thermostat-kitchen': unreachable_thermostat}),
    )


@pytest.fixture
def f_switch_without_the_thermostat() -> ThermostatZoneSwitch:
    return ThermostatZoneSwitch(stamps=ZoneRepository(), thermostats=ThermostatProviderStub(thermostats={}))


@pytest.fixture
def f_switch_without_a_database(f_thermostat: FloorThermostatStub) -> ThermostatZoneSwitch:
    return ThermostatZoneSwitch(
        stamps=ZoneWriteStampsStub(error=OperationalError('database is locked')),
        thermostats=ThermostatProviderStub(thermostats={'thermostat-kitchen': f_thermostat}),
    )


@pytest.fixture
def f_unreachable_switch_without_a_database(unreachable_thermostat: FloorThermostatStub) -> ThermostatZoneSwitch:
    return ThermostatZoneSwitch(
        stamps=ZoneWriteStampsStub(error=OperationalError('database is locked')),
        thermostats=ThermostatProviderStub(thermostats={'thermostat-kitchen': unreachable_thermostat}),
    )


@pytest.mark.parametrize(
    ('switch', 'commands'),
    [(ThermostatZoneSwitch.switch_off, [('power', False)]), (ThermostatZoneSwitch.switch_on, [('power', True)])],
    ids=['off', 'on'],
)
def test_switch_powers_the_thermostat_within_the_write_budget_and_stamps_the_write(
    f_switch: ThermostatZoneSwitch,
    f_kitchen: Zone,
    f_thermostat: FloorThermostatStub,
    switch: Callable[[ThermostatZoneSwitch, str, str, datetime], bool],
    commands: list[tuple[str, object]],
) -> None:
    stamped = switch(f_switch, 'kitchen', 'thermostat-kitchen', NOW)

    f_kitchen.refresh_from_db()
    assert (stamped, f_thermostat.commands, f_kitchen.last_command_at) == (True, commands, NOW)


def test_failed_switch_still_spends_the_write_budget(
    f_unreachable_switch: ThermostatZoneSwitch,
    f_kitchen: Zone,
) -> None:
    with pytest.raises(DeviceUnavailableError):
        f_unreachable_switch.switch_off('kitchen', 'thermostat-kitchen', NOW)

    f_kitchen.refresh_from_db()
    assert f_kitchen.last_command_at == NOW


def test_switch_that_cannot_find_its_thermostat_writes_nothing_and_spends_no_write_budget(
    f_switch_without_the_thermostat: ThermostatZoneSwitch,
    f_kitchen: Zone,
) -> None:
    with pytest.raises(DriverNotImplementedError):
        f_switch_without_the_thermostat.switch_off('kitchen', 'thermostat-kitchen', NOW)

    f_kitchen.refresh_from_db()
    assert f_kitchen.last_command_at == NOW - timedelta(minutes=5)


def test_switch_reaches_the_thermostat_and_says_so_when_the_database_cannot_stamp_the_write(
    f_switch_without_a_database: ThermostatZoneSwitch,
    f_thermostat: FloorThermostatStub,
) -> None:
    stamped = f_switch_without_a_database.switch_off('kitchen', 'thermostat-kitchen', NOW)

    assert (stamped, f_thermostat.commands) == (False, [('power', False)])


def test_failed_switch_whose_stamp_the_database_refuses_raises_the_device_error_and_logs_the_lost_stamp(
    f_unreachable_switch_without_a_database: ThermostatZoneSwitch,
) -> None:
    with capture_logs() as logs, pytest.raises(DeviceUnavailableError):
        f_unreachable_switch_without_a_database.switch_on('kitchen', 'thermostat-kitchen', NOW)

    assert logs == [
        {
            'event': 'zone_write_stamp_failed',
            'log_level': 'warning',
            'zone': 'kitchen',
            'at': NOW.isoformat(),
            'error': 'database is locked',
        },
    ]
