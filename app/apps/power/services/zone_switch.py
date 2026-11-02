from datetime import datetime

import structlog
from django.db import OperationalError

from apps.climate.repositories.zone import ZoneRepository
from apps.devices.domain.capabilities import ThermostatProvider
from apps.devices.services.driver_registry import driver_registry
from apps.power.domain.ports import ZoneWriteStamps

logger = structlog.get_logger(__name__)


class ThermostatZoneSwitch:
    def __init__(self, stamps: ZoneWriteStamps, thermostats: ThermostatProvider) -> None:
        self._stamps = stamps
        self._thermostats = thermostats

    @classmethod
    def init(cls) -> 'ThermostatZoneSwitch':
        return cls(stamps=ZoneRepository(), thermostats=driver_registry)

    def switch_off(self, zone: str, thermostat: str, at: datetime) -> bool:
        return self._switch(zone, thermostat, power=False, at=at)

    def switch_on(self, zone: str, thermostat: str, at: datetime) -> bool:
        return self._switch(zone, thermostat, power=True, at=at)

    def _switch(self, zone: str, thermostat: str, *, power: bool, at: datetime) -> bool:
        device = self._thermostats.thermostat(thermostat)
        try:
            device.set_power(power)
        finally:
            stamped = self._stamped(zone, at)
        return stamped

    def _stamped(self, zone: str, at: datetime) -> bool:
        try:
            self._stamps.mark_commanded(zone, at)
        except OperationalError as error:
            logger.warning('zone_write_stamp_failed', zone=zone, at=at.isoformat(), error=str(error))
            return False

        return True
