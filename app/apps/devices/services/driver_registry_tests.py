from datetime import timedelta

import pytest

from apps.core.domain.errors import SecretMissingError
from apps.core.services.secrets import DeviceSecret, SecretsFile
from apps.devices.domain.capabilities import FloorThermostat, MeteredPlug, PhaseMeter, TemperatureSensor
from apps.devices.domain.errors import DriverNotImplementedError
from apps.devices.domain.kinds import DeviceDriver, DeviceKind
from apps.devices.drivers.esphome_phase_meter import EsphomePhaseMeterDriver
from apps.devices.drivers.esphome_temperature import EsphomeTemperatureDriver
from apps.devices.factories import DeviceFactory
from apps.devices.repositories.device import DeviceRepository
from apps.devices.services.driver_registry import DriverRegistry
from apps.devices.services.phase_meter_store import PhaseMeterStore
from apps.devices.services.state_store import TemperatureStateStore
from apps.devices.stubs import FloorThermostatStub, MeteredPlugStub

pytestmark = pytest.mark.django_db

SECRET = DeviceSecret(address='192.0.2.10', device_id='device-id', local_key='local-key', protocol='3.3')


def _stub_factory(_secret: DeviceSecret) -> FloorThermostat:
    return FloorThermostatStub(state=None)


def _stub_plug_factory(_secret: DeviceSecret) -> MeteredPlug:
    return MeteredPlugStub(state=None)


def _temperature_sensor_factory(slug: str) -> TemperatureSensor:
    return EsphomeTemperatureDriver(slug=slug, store=TemperatureStateStore(unavailable_after=timedelta(minutes=10)))


def _phase_meter_factory(slug: str) -> PhaseMeter:
    store = PhaseMeterStore(stale_after=timedelta(seconds=10), unavailable_after=timedelta(minutes=10))
    return EsphomePhaseMeterDriver(slug=slug, store=store)


@pytest.fixture
def f_registry() -> DriverRegistry:
    DeviceFactory(slug='thermostat-kitchen')
    DeviceFactory(slug='thermostat-without-secret')
    DeviceFactory(slug='climate-bedroom', kind=DeviceKind.CLIMATE_UNIT, driver=DeviceDriver.UNSUPPORTED)
    DeviceFactory(slug='plug-boiler-1f', kind=DeviceKind.PLUG, driver=DeviceDriver.TUYA_PLUG)
    DeviceFactory(slug='air-kitchen', kind=DeviceKind.TEMPERATURE_SENSOR, driver=DeviceDriver.ESPHOME_TEMPERATURE)
    DeviceFactory(slug='air-attic', kind=DeviceKind.TEMPERATURE_SENSOR, driver=DeviceDriver.UNSUPPORTED)
    DeviceFactory(slug='phase-meter', kind=DeviceKind.PHASE_METER, driver=DeviceDriver.ESPHOME_PHASE_METER)
    secrets = SecretsFile(
        devices={'thermostat-kitchen': SECRET, 'climate-bedroom': SECRET, 'plug-boiler-1f': SECRET},
        panel_password=None,
    )
    return DriverRegistry(
        devices=DeviceRepository(),
        secrets=secrets,
        thermostat_factories={DeviceDriver.TUYA_THERMOSTAT: _stub_factory},
        plug_factories={DeviceDriver.TUYA_PLUG: _stub_plug_factory},
        temperature_sensor_factories={DeviceDriver.ESPHOME_TEMPERATURE: _temperature_sensor_factory},
        phase_meter_factories={DeviceDriver.ESPHOME_PHASE_METER: _phase_meter_factory},
    )


def test_thermostat_is_built_once_per_slug(f_registry: DriverRegistry) -> None:
    first = f_registry.thermostat('thermostat-kitchen')
    second = f_registry.thermostat('thermostat-kitchen')

    assert first is second


def test_plug_is_built_once_per_slug(f_registry: DriverRegistry) -> None:
    first = f_registry.plug('plug-boiler-1f')
    second = f_registry.plug('plug-boiler-1f')

    assert first is second


def test_unsupported_driver_raises(f_registry: DriverRegistry) -> None:
    with pytest.raises(DriverNotImplementedError):
        f_registry.thermostat('climate-bedroom')


def test_plug_with_a_thermostat_driver_raises(f_registry: DriverRegistry) -> None:
    with pytest.raises(DriverNotImplementedError):
        f_registry.plug('thermostat-kitchen')


def test_missing_secret_raises(f_registry: DriverRegistry) -> None:
    with pytest.raises(SecretMissingError):
        f_registry.thermostat('thermostat-without-secret')


def test_esphome_drivers_are_built_once_per_slug_without_a_secret(f_registry: DriverRegistry) -> None:
    sensors = (f_registry.temperature_sensor('air-kitchen'), f_registry.temperature_sensor('air-kitchen'))
    meters = (f_registry.phase_meter('phase-meter'), f_registry.phase_meter('phase-meter'))

    assert (type(sensors[0]), sensors[0] is sensors[1], type(meters[0]), meters[0] is meters[1]) == (
        EsphomeTemperatureDriver,
        True,
        EsphomePhaseMeterDriver,
        True,
    )


@pytest.mark.parametrize('slug', ['air-attic', 'thermostat-kitchen'])
def test_temperature_sensor_without_an_esphome_driver_raises(f_registry: DriverRegistry, slug: str) -> None:
    with pytest.raises(DriverNotImplementedError):
        f_registry.temperature_sensor(slug)


def test_phase_meter_with_a_sensor_driver_raises(f_registry: DriverRegistry) -> None:
    with pytest.raises(DriverNotImplementedError):
        f_registry.phase_meter('air-kitchen')
