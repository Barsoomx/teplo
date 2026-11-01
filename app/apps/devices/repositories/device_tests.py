import pytest

from apps.devices.domain.device import DeviceSettings
from apps.devices.domain.errors import DeviceNotFoundError
from apps.devices.domain.kinds import DeviceDriver, DeviceKind
from apps.devices.factories import DeviceFactory
from apps.devices.models import Device
from apps.devices.repositories.device import DeviceRepository

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


def test_thermostat_slugs_lists_only_thermostats_in_order(f_devices: list[Device]) -> None:
    assert DeviceRepository.thermostat_slugs() == [f_devices[1].slug, f_devices[0].slug]


def test_plug_slugs_lists_only_plugs_in_order(f_devices: list[Device]) -> None:
    assert DeviceRepository.plug_slugs() == [f_devices[3].slug, f_devices[2].slug]


def test_all_settings_describes_every_device_in_slug_order(f_devices: list[Device]) -> None:
    assert DeviceRepository.all_settings() == [
        DeviceSettings(slug=device.slug, name=device.name, kind=DeviceKind(device.kind))
        for device in (f_devices[3], f_devices[2], f_devices[1], f_devices[0])
    ]


def test_get_returns_device_by_slug(f_devices: list[Device]) -> None:
    assert DeviceRepository.get('plug-boiler-2f') == f_devices[2]


def test_get_raises_for_unknown_slug() -> None:
    with pytest.raises(DeviceNotFoundError):
        DeviceRepository.get('thermostat-attic')
