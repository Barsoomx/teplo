from datetime import UTC, datetime, timedelta

import pytest

from apps.climate.factories import ZoneFactory
from apps.climate.models import Zone
from apps.climate.repositories.zone import ZoneRepository
from apps.devices.domain.errors import DeviceUnavailableError
from apps.devices.stubs import FloorThermostatStub, ThermostatProviderStub
from apps.power.services.zone_shutoff import ThermostatZoneShutoff

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
def f_unreachable_thermostat() -> FloorThermostatStub:
    return FloorThermostatStub(state=None, error=DeviceUnavailableError('timed out'))


def test_shutoff_switches_the_thermostat_off_within_the_write_budget_and_stamps_the_write(
    f_kitchen: Zone,
    f_thermostat: FloorThermostatStub,
) -> None:
    shutoff = ThermostatZoneShutoff(
        zones=ZoneRepository(),
        thermostats=ThermostatProviderStub(thermostats={'thermostat-kitchen': f_thermostat}),
    )

    shutoff.switch_off('kitchen', 'thermostat-kitchen', NOW)

    f_kitchen.refresh_from_db()
    assert (f_thermostat.commands, f_kitchen.last_command_at) == ([('power', False)], NOW)


def test_failed_shutoff_still_spends_the_write_budget(
    f_kitchen: Zone,
    f_unreachable_thermostat: FloorThermostatStub,
) -> None:
    shutoff = ThermostatZoneShutoff(
        zones=ZoneRepository(),
        thermostats=ThermostatProviderStub(thermostats={'thermostat-kitchen': f_unreachable_thermostat}),
    )

    with pytest.raises(DeviceUnavailableError):
        shutoff.switch_off('kitchen', 'thermostat-kitchen', NOW)

    f_kitchen.refresh_from_db()
    assert f_kitchen.last_command_at == NOW
