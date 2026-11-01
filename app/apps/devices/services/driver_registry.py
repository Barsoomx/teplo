from collections.abc import Callable
from pathlib import Path

from django.conf import settings

from apps.core.services.secrets import DeviceSecret, SecretsFile
from apps.devices.domain.capabilities import FloorThermostat, MeteredPlug
from apps.devices.domain.errors import DriverNotImplementedError
from apps.devices.domain.kinds import DeviceDriver
from apps.devices.drivers.tuya_plug import TuyaPlugDriver
from apps.devices.drivers.tuya_thermostat import TuyaThermostatDriver
from apps.devices.repositories.device import DeviceRepository

ThermostatFactory = Callable[[DeviceSecret], FloorThermostat]
PlugFactory = Callable[[DeviceSecret], MeteredPlug]


class DriverRegistry:
    def __init__(
        self,
        devices: DeviceRepository,
        secrets: SecretsFile,
        thermostat_factories: dict[DeviceDriver, ThermostatFactory],
        plug_factories: dict[DeviceDriver, PlugFactory],
    ) -> None:
        self._devices = devices
        self._secrets = secrets
        self._thermostat_factories = thermostat_factories
        self._plug_factories = plug_factories
        self._thermostats: dict[str, FloorThermostat] = {}
        self._plugs: dict[str, MeteredPlug] = {}

    @classmethod
    def init(cls) -> 'DriverRegistry':
        return cls(
            devices=DeviceRepository(),
            secrets=SecretsFile.init(Path(settings.TEPLO_SECRETS_FILE)),
            thermostat_factories={DeviceDriver.TUYA_THERMOSTAT: TuyaThermostatDriver.init},
            plug_factories={DeviceDriver.TUYA_PLUG: TuyaPlugDriver.init},
        )

    def thermostat(self, slug: str) -> FloorThermostat:
        if slug not in self._thermostats:
            self._thermostats[slug] = self._build(slug, self._thermostat_factories)

        return self._thermostats[slug]

    def plug(self, slug: str) -> MeteredPlug:
        if slug not in self._plugs:
            self._plugs[slug] = self._build(slug, self._plug_factories)

        return self._plugs[slug]

    def _build[TDriver](self, slug: str, factories: dict[DeviceDriver, Callable[[DeviceSecret], TDriver]]) -> TDriver:
        device = self._devices.get(slug)
        factory = factories.get(DeviceDriver(device.driver))
        if factory is None:
            raise DriverNotImplementedError(device.driver)

        return factory(self._secrets.device(slug))


driver_registry = DriverRegistry.init()
