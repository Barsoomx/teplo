import pytest

from apps.devices.domain.errors import DeviceNotFoundError
from apps.devices.domain.kinds import DeviceDriver, DeviceKind
from apps.devices.factories import DeviceFactory
from apps.devices.models import Device
from apps.devices.repositories.device import DeviceRepository

pytestmark = pytest.mark.django_db


@pytest.fixture
def f_devices() -> list[Device]:
    return [
        DeviceFactory.create(slug='thermostat-kitchen'),
        DeviceFactory.create(slug='thermostat-hallway'),
        DeviceFactory.create(slug='plug-boiler', kind=DeviceKind.PLUG, driver=DeviceDriver.TUYA_PLUG),
    ]


def test_thermostat_slugs_lists_only_thermostats_in_order(f_devices: list[Device]) -> None:
    assert DeviceRepository.thermostat_slugs() == [f_devices[1].slug, f_devices[0].slug]


def test_get_returns_device_by_slug(f_devices: list[Device]) -> None:
    assert DeviceRepository.get('plug-boiler') == f_devices[2]


def test_get_raises_for_unknown_slug() -> None:
    with pytest.raises(DeviceNotFoundError):
        DeviceRepository.get('thermostat-attic')
