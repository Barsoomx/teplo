import threading
from collections.abc import Callable
from pathlib import Path

from django.conf import settings

from apps.core.services.secrets import DeviceSecret, SecretsFile
from apps.devices.domain.capabilities import (
    FloorThermostat,
    MeteredPlug,
    PhaseMeter,
    SwitchablePlug,
    TemperatureSensor,
)
from apps.devices.domain.errors import DriverNotImplementedError, PlugNotSwitchableError
from apps.devices.domain.kinds import DeviceDriver
from apps.devices.drivers.esphome_phase_meter import EsphomePhaseMeterDriver
from apps.devices.drivers.esphome_temperature import EsphomeTemperatureDriver
from apps.devices.drivers.tuya_plug import TuyaPlugDriver, TuyaSwitchablePlugDriver
from apps.devices.drivers.tuya_thermostat import TuyaThermostatDriver
from apps.devices.models import Device
from apps.devices.repositories.device import DeviceRepository

ThermostatFactory = Callable[[DeviceSecret], FloorThermostat]
PlugFactory = Callable[[DeviceSecret], MeteredPlug]
SwitchablePlugFactory = Callable[[DeviceSecret], SwitchablePlug]
TemperatureSensorFactory = Callable[[str], TemperatureSensor]
PhaseMeterFactory = Callable[[str], PhaseMeter]


class DriverRegistry:
    def __init__(
        self,
        devices: DeviceRepository,
        secrets: SecretsFile,
        thermostat_factories: dict[DeviceDriver, ThermostatFactory],
        plug_factories: dict[DeviceDriver, PlugFactory],
        switchable_plug_factories: dict[DeviceDriver, SwitchablePlugFactory],
        temperature_sensor_factories: dict[DeviceDriver, TemperatureSensorFactory],
        phase_meter_factories: dict[DeviceDriver, PhaseMeterFactory],
    ) -> None:
        self._devices = devices
        self._secrets = secrets
        self._thermostat_factories = thermostat_factories
        self._plug_factories = plug_factories
        self._switchable_plug_factories = switchable_plug_factories
        self._temperature_sensor_factories = temperature_sensor_factories
        self._phase_meter_factories = phase_meter_factories
        self._thermostats: dict[str, FloorThermostat] = {}
        self._plugs: dict[str, MeteredPlug] = {}
        self._switchable_plugs: dict[str, SwitchablePlug] = {}
        self._temperature_sensors: dict[str, TemperatureSensor] = {}
        self._phase_meters: dict[str, PhaseMeter] = {}
        self._plug_lock = threading.Lock()

    @classmethod
    def init(cls) -> 'DriverRegistry':
        return cls(
            devices=DeviceRepository(),
            secrets=SecretsFile.init(Path(settings.TEPLO_SECRETS_FILE)),
            thermostat_factories={DeviceDriver.TUYA_THERMOSTAT: TuyaThermostatDriver.init},
            plug_factories={DeviceDriver.TUYA_PLUG: TuyaPlugDriver.init},
            switchable_plug_factories={DeviceDriver.TUYA_SWITCHABLE_PLUG: TuyaSwitchablePlugDriver.init},
            temperature_sensor_factories={DeviceDriver.ESPHOME_TEMPERATURE: EsphomeTemperatureDriver.init},
            phase_meter_factories={DeviceDriver.ESPHOME_PHASE_METER: EsphomePhaseMeterDriver.init},
        )

    def thermostat(self, slug: str) -> FloorThermostat:
        if slug not in self._thermostats:
            self._thermostats[slug] = self._factory(slug, self._thermostat_factories)(self._secrets.device(slug))

        return self._thermostats[slug]

    def plug(self, slug: str) -> MeteredPlug:
        with self._plug_lock:
            if slug not in self._plugs:
                self._plugs[slug] = self._new_plug(self._devices.get(slug))

            return self._plugs[slug]

    def switchable_plug(self, slug: str) -> SwitchablePlug:
        device = self._devices.get(slug)
        if device.monitor_only:
            raise PlugNotSwitchableError(slug, 'it is monitor-only')

        factory = self._switchable_plug_factories.get(DeviceDriver(device.driver))
        if factory is None:
            raise PlugNotSwitchableError(slug, f'driver {device.driver} only reads')

        with self._plug_lock:
            plug = self._switchable(slug, factory)
            self._plugs[slug] = plug
            return plug

    def temperature_sensor(self, slug: str) -> TemperatureSensor:
        if slug not in self._temperature_sensors:
            self._temperature_sensors[slug] = self._factory(slug, self._temperature_sensor_factories)(slug)

        return self._temperature_sensors[slug]

    def phase_meter(self, slug: str) -> PhaseMeter:
        if slug not in self._phase_meters:
            self._phase_meters[slug] = self._factory(slug, self._phase_meter_factories)(slug)

        return self._phase_meters[slug]

    def _new_plug(self, device: Device) -> MeteredPlug:
        switchable = self._switchable_plug_factories.get(DeviceDriver(device.driver))
        if switchable is not None:
            return self._switchable(device.slug, switchable)

        return self._factory_of(device, self._plug_factories)(self._secrets.device(device.slug))

    def _switchable(self, slug: str, factory: SwitchablePlugFactory) -> SwitchablePlug:
        if slug not in self._switchable_plugs:
            self._switchable_plugs[slug] = factory(self._secrets.device(slug))

        return self._switchable_plugs[slug]

    def _factory[TFactory](self, slug: str, factories: dict[DeviceDriver, TFactory]) -> TFactory:
        return self._factory_of(self._devices.get(slug), factories)

    @staticmethod
    def _factory_of[TFactory](device: Device, factories: dict[DeviceDriver, TFactory]) -> TFactory:
        factory = factories.get(DeviceDriver(device.driver))
        if factory is None:
            raise DriverNotImplementedError(device.driver)

        return factory


driver_registry = DriverRegistry.init()
