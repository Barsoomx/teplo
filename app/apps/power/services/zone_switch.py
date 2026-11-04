from datetime import datetime
from functools import partial

import structlog
from django.db import OperationalError

from apps.climate.domain.ports import WriteGate
from apps.climate.domain.write_guard import WriteReservation
from apps.climate.repositories.zone import ZoneRepository
from apps.climate.services.write_gate import zone_write_gate
from apps.devices.domain.capabilities import ThermostatProvider
from apps.devices.services.driver_registry import driver_registry
from apps.power.domain.ports import ZoneWriteStamps

logger = structlog.get_logger(__name__)


class ThermostatZoneSwitch:
    def __init__(self, stamps: ZoneWriteStamps, thermostats: ThermostatProvider, writes: WriteGate) -> None:
        self._stamps = stamps
        self._thermostats = thermostats
        self._writes = writes

    @classmethod
    def init(cls) -> 'ThermostatZoneSwitch':
        return cls(stamps=ZoneRepository(), thermostats=driver_registry, writes=zone_write_gate)

    def switch_off(self, zone: str, thermostat: str, at: datetime) -> bool:
        device = self._thermostats.thermostat(thermostat)
        shed = self._writes.override(zone, at)
        try:
            self._writes.send(shed, partial(device.set_power, False))
        finally:
            stamped = self._stamped(shed, at)
        return stamped

    def switch_on(
        self,
        zone: str,
        thermostat: str,
        at: datetime,
        *,
        stamped_at: datetime | None,
        read_at: float,
    ) -> bool:
        device = self._thermostats.thermostat(thermostat)
        reservation = self._writes.reserve(zone, at, stamped_at=stamped_at, read_at=read_at)
        if reservation is None:
            return False

        with self._writes.stamping(reservation):
            self._stamps.mark_commanded(zone, at)
        return self._writes.write(reservation, partial(device.set_power, True))

    def _stamped(self, shed: WriteReservation, at: datetime) -> bool:
        try:
            self._stamps.mark_shed(shed.key, at)
        except OperationalError as error:
            logger.warning('zone_write_stamp_failed', zone=shed.key, at=at.isoformat(), error=str(error))
            return False

        self._writes.stored(shed)
        return True
