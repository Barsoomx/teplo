from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

from apps.climate.factories import ZoneFactory
from apps.climate.models import Zone
from apps.climate.repositories.zone import ZoneRepository
from apps.devices.domain.errors import DeviceUnavailableError
from apps.devices.stubs import FloorThermostatStub, ThermostatProviderStub
from apps.power.services.zone_switch import ThermostatZoneSwitch

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
        zones=ZoneRepository(),
        thermostats=ThermostatProviderStub(thermostats={'thermostat-kitchen': f_thermostat}),
    )


@pytest.fixture
def f_unreachable_switch(unreachable_thermostat: FloorThermostatStub) -> ThermostatZoneSwitch:
    return ThermostatZoneSwitch(
        zones=ZoneRepository(),
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
    switch: Callable[[ThermostatZoneSwitch, str, str, datetime], None],
    commands: list[tuple[str, object]],
) -> None:
    switch(f_switch, 'kitchen', 'thermostat-kitchen', NOW)

    f_kitchen.refresh_from_db()
    assert (f_thermostat.commands, f_kitchen.last_command_at) == (commands, NOW)


def test_failed_switch_still_spends_the_write_budget(
    f_unreachable_switch: ThermostatZoneSwitch,
    f_kitchen: Zone,
) -> None:
    with pytest.raises(DeviceUnavailableError):
        f_unreachable_switch.switch_off('kitchen', 'thermostat-kitchen', NOW)

    f_kitchen.refresh_from_db()
    assert f_kitchen.last_command_at == NOW
