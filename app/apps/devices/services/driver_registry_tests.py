from datetime import timedelta

import pytest

from apps.core.domain.errors import SecretMissingError
from apps.core.services.secrets import DeviceSecret, SecretsFile
from apps.devices.domain.capabilities import (
    ClimateUnit,
    ControllableClimateUnit,
    FloorThermostat,
    MeteredPlug,
    PhaseMeter,
    PresenceSensor,
    SwitchablePlug,
    TemperatureSensor,
)
from apps.devices.domain.errors import (
    ClimateUnitNotControllableError,
    DriverNotImplementedError,
    PlugNotSwitchableError,
)
from apps.devices.domain.kinds import DeviceDriver, DeviceKind
from apps.devices.drivers.esphome_phase_meter import EsphomePhaseMeterDriver
from apps.devices.drivers.esphome_presence import EsphomePresenceDriver
from apps.devices.drivers.esphome_temperature import EsphomeTemperatureDriver
from apps.devices.factories import DeviceFactory
from apps.devices.models import Device
from apps.devices.repositories.device import DeviceRepository
from apps.devices.services.driver_registry import DriverRegistry
from apps.devices.services.phase_meter_store import PhaseMeterStore
from apps.devices.services.presence_store import PresenceStore
from apps.devices.services.state_store import TemperatureStateStore
from apps.devices.stubs import (
    ClimateUnitStub,
    ControllableClimateUnitStub,
    FloorThermostatStub,
    MeteredPlugStub,
    SwitchablePlugStub,
)

pytestmark = pytest.mark.django_db

SECRET = DeviceSecret(address='192.0.2.10', device_id='device-id', local_key='local-key', protocol='3.3')


def _stub_factory(_secret: DeviceSecret) -> FloorThermostat:
    return FloorThermostatStub(state=None)


def _stub_plug_factory(_secret: DeviceSecret) -> MeteredPlug:
    return MeteredPlugStub(state=None)


def _stub_switchable_plug_factory(_secret: DeviceSecret) -> SwitchablePlug:
    return SwitchablePlugStub(state=None)


def _stub_climate_unit_factory(_secret: DeviceSecret) -> ClimateUnit:
    return ClimateUnitStub(state=None)


def _stub_controllable_climate_unit_factory(_secret: DeviceSecret) -> ControllableClimateUnit:
    return ControllableClimateUnitStub(state=None)


def _temperature_sensor_factory(slug: str) -> TemperatureSensor:
    return EsphomeTemperatureDriver(slug=slug, store=TemperatureStateStore(unavailable_after=timedelta(minutes=10)))


def _phase_meter_factory(slug: str) -> PhaseMeter:
    store = PhaseMeterStore(stale_after=timedelta(seconds=10), unavailable_after=timedelta(minutes=10))
    return EsphomePhaseMeterDriver(slug=slug, store=store)


def _presence_sensor_factory(slug: str) -> PresenceSensor:
    return EsphomePresenceDriver(slug=slug, store=PresenceStore(unavailable_after=timedelta(minutes=10)))


@pytest.fixture
def f_registry() -> DriverRegistry:
    DeviceFactory(slug='thermostat-kitchen')
    DeviceFactory(slug='thermostat-without-secret')
    DeviceFactory(slug='climate-bedroom', kind=DeviceKind.CLIMATE_UNIT, driver=DeviceDriver.UNSUPPORTED)
    DeviceFactory(slug='heater-office', kind=DeviceKind.CLIMATE_UNIT, driver=DeviceDriver.TUYA_HEATER)
    DeviceFactory(slug='heater-hall', kind=DeviceKind.CLIMATE_UNIT, driver=DeviceDriver.ESPHOME_TEMPERATURE)
    DeviceFactory(slug='plug-boiler-1f', kind=DeviceKind.PLUG, driver=DeviceDriver.TUYA_SWITCHABLE_PLUG)
    DeviceFactory(slug='plug-boiler-2f', kind=DeviceKind.PLUG, driver=DeviceDriver.TUYA_PLUG)
    DeviceFactory(slug='plug-washer', kind=DeviceKind.PLUG, driver=DeviceDriver.TUYA_PLUG, monitor_only=True)
    DeviceFactory(slug='air-kitchen', kind=DeviceKind.TEMPERATURE_SENSOR, driver=DeviceDriver.ESPHOME_TEMPERATURE)
    DeviceFactory(slug='air-attic', kind=DeviceKind.TEMPERATURE_SENSOR, driver=DeviceDriver.UNSUPPORTED)
    DeviceFactory(slug='phase-meter', kind=DeviceKind.PHASE_METER, driver=DeviceDriver.ESPHOME_PHASE_METER)
    DeviceFactory(slug='pc-office', kind=DeviceKind.PRESENCE_SENSOR, driver=DeviceDriver.ESPHOME_PRESENCE)
    secrets = SecretsFile(
        devices={
            'thermostat-kitchen': SECRET,
            'climate-bedroom': SECRET,
            'heater-office': SECRET,
            'heater-hall': SECRET,
            'plug-boiler-1f': SECRET,
            'plug-boiler-2f': SECRET,
            'plug-washer': SECRET,
        },
        panel_password=None,
    )
    return DriverRegistry(
        devices=DeviceRepository(),
        secrets=secrets,
        thermostat_factories={DeviceDriver.TUYA_THERMOSTAT: _stub_factory},
        plug_factories={DeviceDriver.TUYA_PLUG: _stub_plug_factory},
        switchable_plug_factories={DeviceDriver.TUYA_SWITCHABLE_PLUG: _stub_switchable_plug_factory},
        temperature_sensor_factories={DeviceDriver.ESPHOME_TEMPERATURE: _temperature_sensor_factory},
        phase_meter_factories={DeviceDriver.ESPHOME_PHASE_METER: _phase_meter_factory},
        presence_sensor_factories={DeviceDriver.ESPHOME_PRESENCE: _presence_sensor_factory},
        climate_unit_factories={DeviceDriver.ESPHOME_TEMPERATURE: _stub_climate_unit_factory},
        controllable_climate_unit_factories={DeviceDriver.TUYA_HEATER: _stub_controllable_climate_unit_factory},
    )


@pytest.fixture
def f_plug_read_before_it_became_switchable(f_registry: DriverRegistry) -> MeteredPlug:
    plug = f_registry.plug('plug-boiler-2f')
    Device.objects.filter(slug='plug-boiler-2f').update(driver=DeviceDriver.TUYA_SWITCHABLE_PLUG)
    return plug


@pytest.fixture
def f_registry_with_a_monitor_only_heater(f_registry: DriverRegistry) -> DriverRegistry:
    Device.objects.filter(slug='heater-hall').update(monitor_only=True)
    return f_registry


def test_thermostat_is_built_once_per_slug(f_registry: DriverRegistry) -> None:
    first = f_registry.thermostat('thermostat-kitchen')
    second = f_registry.thermostat('thermostat-kitchen')

    assert first is second


@pytest.mark.parametrize('slug', ['plug-boiler-1f', 'plug-boiler-2f'], ids=['switchable', 'read_only'])
def test_plug_is_built_once_per_slug(f_registry: DriverRegistry, slug: str) -> None:
    first = f_registry.plug(slug)
    second = f_registry.plug(slug)

    assert first is second


@pytest.mark.parametrize(
    ('slug', 'driver'),
    [('plug-boiler-1f', SwitchablePlugStub), ('plug-boiler-2f', MeteredPlugStub), ('plug-washer', MeteredPlugStub)],
    ids=['switchable', 'read_only', 'monitor_only'],
)
def test_plug_is_read_through_the_driver_its_device_declares(
    f_registry: DriverRegistry,
    slug: str,
    driver: type[MeteredPlugStub],
) -> None:
    assert type(f_registry.plug(slug)) is driver


def test_switchable_plug_is_the_driver_the_poll_reads(f_registry: DriverRegistry) -> None:
    read = f_registry.plug('plug-boiler-1f')
    switched = f_registry.switchable_plug('plug-boiler-1f')

    assert (switched is read, f_registry.switchable_plug('plug-boiler-1f') is read) == (True, True)


def test_poll_reads_the_switchable_plug_built_for_switching(f_registry: DriverRegistry) -> None:
    switched = f_registry.switchable_plug('plug-boiler-1f')

    assert f_registry.plug('plug-boiler-1f') is switched


def test_plug_read_before_it_became_switchable_is_read_through_its_switching_driver(
    f_registry: DriverRegistry,
    f_plug_read_before_it_became_switchable: MeteredPlug,
) -> None:
    switched = f_registry.switchable_plug('plug-boiler-2f')

    assert (
        type(switched),
        switched is f_plug_read_before_it_became_switchable,
        f_registry.plug('plug-boiler-2f') is switched,
    ) == (SwitchablePlugStub, False, True)


@pytest.mark.parametrize(
    ('slug', 'detail'),
    [
        ('plug-washer', 'plug plug-washer cannot be switched: it is monitor-only'),
        ('plug-boiler-2f', 'plug plug-boiler-2f cannot be switched: driver tuya_plug only reads'),
        ('thermostat-kitchen', 'plug thermostat-kitchen cannot be switched: driver tuya_thermostat only reads'),
    ],
    ids=['monitor_only', 'read_only_driver', 'not_a_plug'],
)
def test_switchable_plug_refuses_a_device_not_declared_switchable(
    f_registry: DriverRegistry,
    slug: str,
    detail: str,
) -> None:
    with pytest.raises(PlugNotSwitchableError) as raised:
        f_registry.switchable_plug(slug)

    assert (raised.value.error_code, raised.value.detail) == ('plug_not_switchable', detail)


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


def test_presence_sensor_is_built_once_per_slug_without_a_secret(f_registry: DriverRegistry) -> None:
    first = f_registry.presence_sensor('pc-office')
    second = f_registry.presence_sensor('pc-office')

    assert (type(first), first is second) == (EsphomePresenceDriver, True)


@pytest.mark.parametrize('slug', ['air-kitchen', 'phase-meter', 'thermostat-kitchen'])
def test_presence_sensor_without_the_presence_driver_raises(f_registry: DriverRegistry, slug: str) -> None:
    with pytest.raises(DriverNotImplementedError):
        f_registry.presence_sensor(slug)


@pytest.mark.parametrize('slug', ['heater-office', 'heater-hall'], ids=['controllable', 'read_only'])
def test_climate_unit_is_built_once_per_slug(f_registry: DriverRegistry, slug: str) -> None:
    first = f_registry.climate_unit(slug)
    second = f_registry.climate_unit(slug)

    assert first is second


@pytest.mark.parametrize(
    ('slug', 'driver'),
    [('heater-office', ControllableClimateUnitStub), ('heater-hall', ClimateUnitStub)],
    ids=['controllable', 'read_only'],
)
def test_climate_unit_is_read_through_the_driver_its_device_declares(
    f_registry: DriverRegistry,
    slug: str,
    driver: type[ClimateUnitStub],
) -> None:
    assert type(f_registry.climate_unit(slug)) is driver


def test_controllable_climate_unit_is_the_driver_the_poll_reads(f_registry: DriverRegistry) -> None:
    read = f_registry.climate_unit('heater-office')
    commanded = f_registry.controllable_climate_unit('heater-office')

    assert (commanded is read, f_registry.climate_unit('heater-office') is commanded) == (True, True)


def test_poll_reads_the_climate_unit_built_for_commands(f_registry: DriverRegistry) -> None:
    commanded = f_registry.controllable_climate_unit('heater-office')

    assert f_registry.climate_unit('heater-office') is commanded


@pytest.mark.parametrize(
    ('slug', 'detail'),
    [
        ('heater-hall', 'climate unit heater-hall cannot be controlled: driver esphome_temperature only reads'),
        ('climate-bedroom', 'climate unit climate-bedroom cannot be controlled: driver unsupported only reads'),
        (
            'thermostat-kitchen',
            'climate unit thermostat-kitchen cannot be controlled: driver tuya_thermostat only reads',
        ),
    ],
    ids=['read_only_driver', 'unsupported_driver', 'not_a_climate_unit'],
)
def test_controllable_climate_unit_refuses_a_device_whose_driver_only_reads(
    f_registry: DriverRegistry,
    slug: str,
    detail: str,
) -> None:
    with pytest.raises(ClimateUnitNotControllableError) as raised:
        f_registry.controllable_climate_unit(slug)

    assert (raised.value.error_code, raised.value.detail) == ('climate_unit_not_controllable', detail)


def test_controllable_climate_unit_refuses_a_monitor_only_device(
    f_registry_with_a_monitor_only_heater: DriverRegistry,
) -> None:
    with pytest.raises(ClimateUnitNotControllableError) as raised:
        f_registry_with_a_monitor_only_heater.controllable_climate_unit('heater-hall')

    assert raised.value.detail == 'climate unit heater-hall cannot be controlled: it is monitor-only'


def test_climate_unit_with_an_unsupported_driver_raises(f_registry: DriverRegistry) -> None:
    with pytest.raises(DriverNotImplementedError):
        f_registry.climate_unit('climate-bedroom')
