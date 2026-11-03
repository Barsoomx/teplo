from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from django.db import OperationalError
from structlog.testing import capture_logs

from apps.climate.domain.write_guard import WriteReservation, WriteSlot
from apps.climate.factories import ZoneFactory
from apps.climate.models import Zone
from apps.climate.repositories.zone import ZoneRepository
from apps.climate.services.write_gate import ZoneWriteGate
from apps.core.stubs import MonotonicClockStub, WallClockStub
from apps.devices.domain.errors import DeviceUnavailableError, DriverNotImplementedError
from apps.devices.stubs import FloorThermostatStub, ThermostatProviderStub
from apps.power.services.zone_switch import ThermostatZoneSwitch
from apps.power.stubs import ZoneWriteStampsStub

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 11, 2, 18, 40, tzinfo=UTC)
WRITTEN_AT = NOW - timedelta(minutes=40)
HEATING_WRITE_AT = NOW - timedelta(minutes=5)
START = 1000.0


def _switch_off(switch: ThermostatZoneSwitch) -> object:
    return switch.switch_off('kitchen', 'thermostat-kitchen', NOW)


def _switch_on(switch: ThermostatZoneSwitch) -> object:
    return switch.switch_on('kitchen', 'thermostat-kitchen', NOW, stamped_at=WRITTEN_AT)


@pytest.fixture
def f_kitchen() -> Zone:
    return ZoneFactory.create(slug='kitchen', thermostat__slug='thermostat-kitchen', last_command_at=WRITTEN_AT)


@pytest.fixture
def f_thermostat() -> FloorThermostatStub:
    return FloorThermostatStub(state=None)


@pytest.fixture
def unreachable_thermostat() -> FloorThermostatStub:
    return FloorThermostatStub(state=None, error=DeviceUnavailableError('timed out'))


@pytest.fixture
def clock() -> MonotonicClockStub:
    return MonotonicClockStub(now=START)


@pytest.fixture
def f_writes(clock: MonotonicClockStub) -> ZoneWriteGate:
    return ZoneWriteGate(clock=clock, wall_clock=WallClockStub(now=NOW))


@pytest.fixture
def f_kitchen_written_by_the_heating_cycle(f_kitchen: Zone, f_writes: ZoneWriteGate) -> Zone:
    f_writes.reserve('kitchen', HEATING_WRITE_AT, stamped_at=WRITTEN_AT)
    Zone.objects.filter(pk=f_kitchen.pk).update(last_command_at=HEATING_WRITE_AT)
    return f_kitchen


@pytest.fixture
def f_switch(f_thermostat: FloorThermostatStub, f_writes: ZoneWriteGate) -> ThermostatZoneSwitch:
    return ThermostatZoneSwitch(
        stamps=ZoneRepository(),
        thermostats=ThermostatProviderStub(thermostats={'thermostat-kitchen': f_thermostat}),
        writes=f_writes,
    )


@pytest.fixture
def f_unreachable_switch(
    unreachable_thermostat: FloorThermostatStub,
    f_writes: ZoneWriteGate,
) -> ThermostatZoneSwitch:
    return ThermostatZoneSwitch(
        stamps=ZoneRepository(),
        thermostats=ThermostatProviderStub(thermostats={'thermostat-kitchen': unreachable_thermostat}),
        writes=f_writes,
    )


@pytest.fixture
def f_switch_without_the_thermostat(f_writes: ZoneWriteGate) -> ThermostatZoneSwitch:
    return ThermostatZoneSwitch(
        stamps=ZoneRepository(),
        thermostats=ThermostatProviderStub(thermostats={}),
        writes=f_writes,
    )


@pytest.fixture
def f_switch_without_a_database(f_thermostat: FloorThermostatStub, f_writes: ZoneWriteGate) -> ThermostatZoneSwitch:
    return ThermostatZoneSwitch(
        stamps=ZoneWriteStampsStub(error=OperationalError('database is locked')),
        thermostats=ThermostatProviderStub(thermostats={'thermostat-kitchen': f_thermostat}),
        writes=f_writes,
    )


@pytest.fixture
def f_unreachable_switch_without_a_database(
    unreachable_thermostat: FloorThermostatStub,
    f_writes: ZoneWriteGate,
) -> ThermostatZoneSwitch:
    return ThermostatZoneSwitch(
        stamps=ZoneWriteStampsStub(error=OperationalError('database is locked')),
        thermostats=ThermostatProviderStub(thermostats={'thermostat-kitchen': unreachable_thermostat}),
        writes=f_writes,
    )


def test_switch_off_powers_the_thermostat_off_stamps_the_write_and_says_it_did(
    f_switch: ThermostatZoneSwitch,
    f_kitchen: Zone,
    f_thermostat: FloorThermostatStub,
) -> None:
    stamped = f_switch.switch_off('kitchen', 'thermostat-kitchen', NOW)

    f_kitchen.refresh_from_db()
    assert (stamped, f_thermostat.commands, f_kitchen.last_command_at) == (True, [('power', False)], NOW)


def test_switch_on_powers_the_thermostat_on_and_stamps_the_write(
    f_switch: ThermostatZoneSwitch,
    f_kitchen: Zone,
    f_thermostat: FloorThermostatStub,
) -> None:
    switched = f_switch.switch_on('kitchen', 'thermostat-kitchen', NOW, stamped_at=WRITTEN_AT)

    f_kitchen.refresh_from_db()
    assert (switched, f_thermostat.commands, f_kitchen.last_command_at) == (True, [('power', True)], NOW)


@pytest.mark.usefixtures('f_kitchen_written_by_the_heating_cycle')
def test_switch_off_takes_the_zones_write_slot_within_its_budget_and_holds_the_next_write(
    f_switch: ThermostatZoneSwitch,
    f_writes: ZoneWriteGate,
    f_thermostat: FloorThermostatStub,
) -> None:
    f_switch.switch_off('kitchen', 'thermostat-kitchen', NOW)

    assert (
        f_thermostat.commands,
        f_writes.reserve('kitchen', NOW + timedelta(minutes=1), stamped_at=HEATING_WRITE_AT),
    ) == ([('power', False)], None)


def test_switch_on_within_the_zones_write_budget_writes_and_stamps_nothing(
    f_switch: ThermostatZoneSwitch,
    f_kitchen_written_by_the_heating_cycle: Zone,
    f_thermostat: FloorThermostatStub,
) -> None:
    switched = f_switch.switch_on('kitchen', 'thermostat-kitchen', NOW, stamped_at=WRITTEN_AT)

    f_kitchen_written_by_the_heating_cycle.refresh_from_db()
    assert (switched, f_thermostat.commands, f_kitchen_written_by_the_heating_cycle.last_command_at) == (
        False,
        [],
        HEATING_WRITE_AT,
    )


@pytest.mark.parametrize('switch', [_switch_off, _switch_on], ids=['off', 'on'])
def test_failed_switch_still_spends_the_write_budget(
    f_unreachable_switch: ThermostatZoneSwitch,
    f_kitchen: Zone,
    switch: Callable[[ThermostatZoneSwitch], object],
) -> None:
    with pytest.raises(DeviceUnavailableError):
        switch(f_unreachable_switch)

    f_kitchen.refresh_from_db()
    assert f_kitchen.last_command_at == NOW


@pytest.mark.parametrize('switch', [_switch_off, _switch_on], ids=['off', 'on'])
def test_switch_that_cannot_find_its_thermostat_writes_nothing_and_spends_no_write_budget(
    f_switch_without_the_thermostat: ThermostatZoneSwitch,
    f_writes: ZoneWriteGate,
    f_kitchen: Zone,
    switch: Callable[[ThermostatZoneSwitch], object],
) -> None:
    with pytest.raises(DriverNotImplementedError):
        switch(f_switch_without_the_thermostat)

    f_kitchen.refresh_from_db()
    assert (f_kitchen.last_command_at, f_writes.reserve('kitchen', NOW, stamped_at=WRITTEN_AT)) == (
        WRITTEN_AT,
        WriteReservation(zone='kitchen', slot=WriteSlot(at=NOW, moment=START), previous=None),
    )


def test_switch_off_reaches_the_thermostat_and_says_so_when_the_database_cannot_stamp_the_write(
    f_switch_without_a_database: ThermostatZoneSwitch,
    f_thermostat: FloorThermostatStub,
) -> None:
    stamped = f_switch_without_a_database.switch_off('kitchen', 'thermostat-kitchen', NOW)

    assert (stamped, f_thermostat.commands) == (False, [('power', False)])


def test_switch_on_writes_nothing_and_gives_the_slot_back_when_the_database_cannot_stamp_the_write(
    f_switch_without_a_database: ThermostatZoneSwitch,
    f_writes: ZoneWriteGate,
    f_thermostat: FloorThermostatStub,
) -> None:
    with pytest.raises(OperationalError):
        f_switch_without_a_database.switch_on('kitchen', 'thermostat-kitchen', NOW, stamped_at=WRITTEN_AT)

    assert (f_thermostat.commands, f_writes.reserve('kitchen', NOW, stamped_at=WRITTEN_AT)) == (
        [],
        WriteReservation(zone='kitchen', slot=WriteSlot(at=NOW, moment=START), previous=None),
    )


def test_failed_switch_off_whose_stamp_the_database_refuses_raises_the_device_error_and_logs_the_lost_stamp(
    f_unreachable_switch_without_a_database: ThermostatZoneSwitch,
) -> None:
    with capture_logs() as logs, pytest.raises(DeviceUnavailableError):
        f_unreachable_switch_without_a_database.switch_off('kitchen', 'thermostat-kitchen', NOW)

    assert logs == [
        {
            'event': 'zone_write_stamp_failed',
            'log_level': 'warning',
            'zone': 'kitchen',
            'at': NOW.isoformat(),
            'error': 'database is locked',
        },
    ]
