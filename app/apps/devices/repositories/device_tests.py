import pytest
from django.db import IntegrityError

from apps.devices.domain.device import DeviceSettings
from apps.devices.domain.errors import DeviceNotFoundError
from apps.devices.domain.kinds import DeviceDriver, DeviceKind
from apps.devices.factories import DeviceFactory
from apps.devices.models import Device
from apps.devices.repositories.device import DeviceRepository
from apps.house.factories import RoomFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def f_devices() -> list[Device]:
    return [
        DeviceFactory.create(slug='thermostat-kitchen', name='Кухня'),
        DeviceFactory.create(slug='thermostat-hallway', name='Теплый пол прихожая'),
        DeviceFactory.create(
            slug='plug-boiler-2f',
            name='Бойлер 2 этаж',
            kind=DeviceKind.PLUG,
            driver=DeviceDriver.TUYA_PLUG,
        ),
        DeviceFactory.create(
            slug='plug-boiler-1f',
            name='Бойлер 1 этаж',
            kind=DeviceKind.PLUG,
            driver=DeviceDriver.TUYA_PLUG,
        ),
    ]


@pytest.fixture
def f_sensors() -> list[Device]:
    return [
        DeviceFactory.create(
            slug='outdoor',
            kind=DeviceKind.TEMPERATURE_SENSOR,
            driver=DeviceDriver.ESPHOME_TEMPERATURE,
        ),
        DeviceFactory.create(
            slug='phase-meter',
            kind=DeviceKind.PHASE_METER,
            driver=DeviceDriver.ESPHOME_PHASE_METER,
        ),
        DeviceFactory.create(
            slug='air-kitchen',
            kind=DeviceKind.TEMPERATURE_SENSOR,
            driver=DeviceDriver.ESPHOME_TEMPERATURE,
        ),
        DeviceFactory.create(slug='thermostat-kitchen'),
    ]


@pytest.fixture
def placed_devices() -> None:
    open_space = RoomFactory.create(slug='open-space')
    DeviceFactory.create(slug='thermostat-kitchen', room=open_space)
    DeviceFactory.create(
        slug='air-kitchen',
        kind=DeviceKind.TEMPERATURE_SENSOR,
        driver=DeviceDriver.ESPHOME_TEMPERATURE,
        room=open_space,
    )
    DeviceFactory.create(slug='outdoor', kind=DeviceKind.TEMPERATURE_SENSOR, driver=DeviceDriver.ESPHOME_TEMPERATURE)


def test_thermostat_slugs_lists_only_thermostats_in_order(f_devices: list[Device]) -> None:
    assert DeviceRepository.thermostat_slugs() == [f_devices[1].slug, f_devices[0].slug]


def test_plug_slugs_lists_only_plugs_in_order(f_devices: list[Device]) -> None:
    assert DeviceRepository.plug_slugs() == [f_devices[3].slug, f_devices[2].slug]


def test_slugs_of_kind_lists_only_that_kind_in_order(f_sensors: list[Device]) -> None:
    assert (
        DeviceRepository.slugs_of_kind(DeviceKind.TEMPERATURE_SENSOR),
        DeviceRepository.slugs_of_kind(DeviceKind.PHASE_METER),
        DeviceRepository.slugs_of_kind(DeviceKind.PLUG),
    ) == ([f_sensors[2].slug, f_sensors[0].slug], [f_sensors[1].slug], [])


def test_slugs_with_driver_lists_only_that_driver_in_order(f_sensors: list[Device]) -> None:
    assert (
        DeviceRepository.slugs_with_driver(DeviceDriver.ESPHOME_TEMPERATURE),
        DeviceRepository.slugs_with_driver(DeviceDriver.ESPHOME_PHASE_METER),
    ) == ([f_sensors[2].slug, f_sensors[0].slug], [f_sensors[1].slug])


def test_all_settings_describes_every_device_in_slug_order(f_devices: list[Device]) -> None:
    assert DeviceRepository.all_settings() == [
        DeviceSettings(slug=device.slug, name=device.name, kind=DeviceKind(device.kind))
        for device in (f_devices[3], f_devices[2], f_devices[1], f_devices[0])
    ]


@pytest.mark.usefixtures('placed_devices')
def test_all_settings_name_the_room_of_each_device() -> None:
    assert [(device.slug, device.room_slug) for device in DeviceRepository.all_settings()] == [
        ('air-kitchen', 'open-space'),
        ('outdoor', None),
        ('thermostat-kitchen', 'open-space'),
    ]


def test_get_returns_device_by_slug(f_devices: list[Device]) -> None:
    assert DeviceRepository.get('plug-boiler-2f') == f_devices[2]


def test_get_raises_for_unknown_slug() -> None:
    with pytest.raises(DeviceNotFoundError):
        DeviceRepository.get('thermostat-attic')


@pytest.mark.parametrize(
    ('kind', 'driver'),
    [(DeviceKind.PLUG, DeviceDriver.TUYA_SWITCHABLE_PLUG), (DeviceKind.CLIMATE_UNIT, DeviceDriver.TUYA_HEATER)],
    ids=['switchable_plug', 'heater'],
)
def test_database_refuses_a_monitor_only_device_with_a_commanding_driver(
    kind: DeviceKind, driver: DeviceDriver
) -> None:
    with pytest.raises(IntegrityError):
        DeviceFactory.create(kind=kind, driver=driver, monitor_only=True)


@pytest.mark.parametrize(
    ('driver', 'monitor_only'),
    [(DeviceDriver.TUYA_PLUG, True), (DeviceDriver.TUYA_SWITCHABLE_PLUG, False)],
    ids=['monitor_only_and_read_only', 'switchable'],
)
def test_database_stores_a_plug_whose_driver_suits_it(driver: DeviceDriver, monitor_only: bool) -> None:
    DeviceFactory.create(slug='plug-boiler-1f', kind=DeviceKind.PLUG, driver=driver, monitor_only=monitor_only)

    assert DeviceRepository.plug_slugs() == ['plug-boiler-1f']
