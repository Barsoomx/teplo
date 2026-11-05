import threading
from collections.abc import Callable
from functools import partial
from pathlib import Path

from django.conf import settings

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
from apps.devices.domain.kinds import DeviceDriver
from apps.devices.drivers.esphome_climate import EsphomeClimateDriver
from apps.devices.drivers.esphome_phase_meter import EsphomePhaseMeterDriver
from apps.devices.drivers.esphome_presence import EsphomePresenceDriver
from apps.devices.drivers.esphome_temperature import EsphomeTemperatureDriver
from apps.devices.drivers.tuya_heater import TuyaHeaterDriver
from apps.devices.drivers.tuya_plug import TuyaPlugDriver, TuyaSwitchablePlugDriver
from apps.devices.drivers.tuya_thermostat import TuyaThermostatDriver
from apps.devices.models import Device
from apps.devices.repositories.device import DeviceRepository

ThermostatFactory = Callable[[DeviceSecret], FloorThermostat]
PlugFactory = Callable[[DeviceSecret], MeteredPlug]
SwitchablePlugFactory = Callable[[DeviceSecret], SwitchablePlug]
TemperatureSensorFactory = Callable[[str], TemperatureSensor]
PhaseMeterFactory = Callable[[str], PhaseMeter]
PresenceSensorFactory = Callable[[str], PresenceSensor]
ClimateUnitFactory = Callable[[DeviceSecret], ClimateUnit]
ControllableClimateUnitFactory = Callable[[DeviceSecret], ControllableClimateUnit]
MqttClimateUnitFactory = Callable[[str, str], ControllableClimateUnit]


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
        presence_sensor_factories: dict[DeviceDriver, PresenceSensorFactory],
        climate_unit_factories: dict[DeviceDriver, ClimateUnitFactory],
        controllable_climate_unit_factories: dict[DeviceDriver, ControllableClimateUnitFactory],
        mqtt_climate_unit_factories: dict[DeviceDriver, MqttClimateUnitFactory],
    ) -> None:
        self._devices = devices
        self._secrets = secrets
        self._thermostat_factories = thermostat_factories
        self._plug_factories = plug_factories
        self._switchable_plug_factories = switchable_plug_factories
        self._temperature_sensor_factories = temperature_sensor_factories
        self._phase_meter_factories = phase_meter_factories
        self._presence_sensor_factories = presence_sensor_factories
        self._climate_unit_factories = climate_unit_factories
        self._controllable_climate_unit_factories = controllable_climate_unit_factories
        self._mqtt_climate_unit_factories = mqtt_climate_unit_factories
        self._thermostats: dict[str, FloorThermostat] = {}
        self._plugs: dict[str, MeteredPlug] = {}
        self._switchable_plugs: dict[str, SwitchablePlug] = {}
        self._temperature_sensors: dict[str, TemperatureSensor] = {}
        self._phase_meters: dict[str, PhaseMeter] = {}
        self._presence_sensors: dict[str, PresenceSensor] = {}
        self._climate_units: dict[str, ClimateUnit] = {}
        self._controllable_climate_units: dict[str, ControllableClimateUnit] = {}
        self._plug_lock = threading.Lock()
        self._climate_unit_lock = threading.Lock()

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
            presence_sensor_factories={DeviceDriver.ESPHOME_PRESENCE: EsphomePresenceDriver.init},
            climate_unit_factories={},
            controllable_climate_unit_factories={DeviceDriver.TUYA_HEATER: TuyaHeaterDriver.init},
            mqtt_climate_unit_factories={DeviceDriver.ESPHOME_CLIMATE: EsphomeClimateDriver.init},
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

    def presence_sensor(self, slug: str) -> PresenceSensor:
        if slug not in self._presence_sensors:
            self._presence_sensors[slug] = self._factory(slug, self._presence_sensor_factories)(slug)

        return self._presence_sensors[slug]

    def climate_unit(self, slug: str) -> ClimateUnit:
        with self._climate_unit_lock:
            if slug not in self._climate_units:
                self._climate_units[slug] = self._new_climate_unit(self._devices.get(slug))

            return self._climate_units[slug]

    def controllable_climate_unit(self, slug: str) -> ControllableClimateUnit:
        device = self._devices.get(slug)
        if device.monitor_only:
            raise ClimateUnitNotControllableError(slug, 'it is monitor-only')

        build = self._controllable_builder(device)
        if build is None:
            raise ClimateUnitNotControllableError(slug, f'driver {device.driver} only reads')

        with self._climate_unit_lock:
            unit = self._controllable(slug, build)
            self._climate_units[slug] = unit
            return unit

    def _new_climate_unit(self, device: Device) -> ClimateUnit:
        build = self._controllable_builder(device)
        if build is not None:
            return self._controllable(device.slug, build)

        return self._factory_of(device, self._climate_unit_factories)(self._secrets.device(device.slug))

    def _controllable_builder(self, device: Device) -> Callable[[], ControllableClimateUnit] | None:
        driver = DeviceDriver(device.driver)
        mqtt_factory = self._mqtt_climate_unit_factories.get(driver)
        if mqtt_factory is not None:
            return partial(mqtt_factory, device.slug, device.node or device.slug)

        factory = self._controllable_climate_unit_factories.get(driver)
        if factory is None:
            return None

        return partial(self._with_secret, factory, device.slug)

    def _with_secret(self, factory: ControllableClimateUnitFactory, slug: str) -> ControllableClimateUnit:
        return factory(self._secrets.device(slug))

    def _controllable(self, slug: str, build: Callable[[], ControllableClimateUnit]) -> ControllableClimateUnit:
        if slug not in self._controllable_climate_units:
            self._controllable_climate_units[slug] = build()

        return self._controllable_climate_units[slug]

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
