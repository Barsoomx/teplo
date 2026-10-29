import pytest

from apps.core.domain.errors import SecretMissingError
from apps.core.services.secrets import DeviceSecret, SecretsFile
from apps.devices.domain.capabilities import FloorThermostat
from apps.devices.domain.errors import DriverNotImplementedError
from apps.devices.domain.kinds import DeviceDriver, DeviceKind
from apps.devices.factories import DeviceFactory
from apps.devices.repositories.device import DeviceRepository
from apps.devices.services.driver_registry import DriverRegistry
from apps.devices.stubs import FloorThermostatStub

pytestmark = pytest.mark.django_db

SECRET = DeviceSecret(address='192.0.2.10', device_id='device-id', local_key='local-key', protocol='3.3')


def _stub_factory(_secret: DeviceSecret) -> FloorThermostat:
    return FloorThermostatStub(state=None)


@pytest.fixture
def f_registry() -> DriverRegistry:
    DeviceFactory(slug='thermostat-kitchen')
    DeviceFactory(slug='thermostat-without-secret')
    DeviceFactory(slug='climate-bedroom', kind=DeviceKind.CLIMATE_UNIT, driver=DeviceDriver.UNSUPPORTED)
    secrets = SecretsFile(devices={'thermostat-kitchen': SECRET, 'climate-bedroom': SECRET}, panel_password=None)
    return DriverRegistry(
        devices=DeviceRepository(),
        secrets=secrets,
        thermostat_factories={DeviceDriver.TUYA_THERMOSTAT: _stub_factory},
    )


def test_thermostat_is_built_once_per_slug(f_registry: DriverRegistry) -> None:
    first = f_registry.thermostat('thermostat-kitchen')
    second = f_registry.thermostat('thermostat-kitchen')

    assert first is second


def test_unsupported_driver_raises(f_registry: DriverRegistry) -> None:
    with pytest.raises(DriverNotImplementedError):
        f_registry.thermostat('climate-bedroom')


def test_missing_secret_raises(f_registry: DriverRegistry) -> None:
    with pytest.raises(SecretMissingError):
        f_registry.thermostat('thermostat-without-secret')
