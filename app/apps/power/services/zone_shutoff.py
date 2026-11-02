from datetime import datetime

from apps.climate.repositories.zone import ZoneRepository
from apps.devices.domain.capabilities import ThermostatProvider
from apps.devices.services.driver_registry import driver_registry


class ThermostatZoneShutoff:
    def __init__(self, zones: ZoneRepository, thermostats: ThermostatProvider) -> None:
        self._zones = zones
        self._thermostats = thermostats

    @classmethod
    def init(cls) -> 'ThermostatZoneShutoff':
        return cls(zones=ZoneRepository(), thermostats=driver_registry)

    def switch_off(self, zone: str, thermostat: str, at: datetime) -> None:
        self._zones.mark_commanded(zone, at)
        self._thermostats.thermostat(thermostat).set_power(False)
