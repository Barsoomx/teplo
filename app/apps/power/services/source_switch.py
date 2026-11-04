from datetime import datetime, timedelta
from functools import partial

import structlog
from django.db import OperationalError

from apps.climate.domain.ports import SourceWrites
from apps.climate.domain.write_guard import WriteReservation
from apps.climate.repositories.heat_source import HeatSourceRepository
from apps.climate.services.source_write_gate import source_write_gate
from apps.devices.domain.capabilities import ControllableClimateUnitProvider
from apps.devices.services.driver_registry import driver_registry
from apps.power.domain.ports import SourceWriteStamps

logger = structlog.get_logger(__name__)


class ClimateUnitSourceSwitch:
    def __init__(
        self,
        stamps: SourceWriteStamps,
        units: ControllableClimateUnitProvider,
        writes: SourceWrites,
    ) -> None:
        self._stamps = stamps
        self._units = units
        self._writes = writes

    @classmethod
    def init(cls) -> 'ClimateUnitSourceSwitch':
        return cls(stamps=HeatSourceRepository(), units=driver_registry, writes=source_write_gate)

    def switch_off(self, source: str, unit: str, at: datetime) -> bool:
        device = self._units.controllable_climate_unit(unit)
        shed = self._writes.override(source, at)
        try:
            self._writes.send(shed, partial(device.set_power, False))
        finally:
            stamped = self._stamped(shed, at)
        return stamped

    def switch_on(
        self,
        source: str,
        unit: str,
        at: datetime,
        *,
        stamped_at: datetime | None,
        read_at: float,
        interval: timedelta,
    ) -> bool:
        device = self._units.controllable_climate_unit(unit)
        reservation = self._writes.reserve(source, at, stamped_at=stamped_at, read_at=read_at, interval=interval)
        if reservation is None:
            return False

        with self._writes.stamping(reservation):
            self._stamps.mark_commanded(source, at, power=True, setpoint=None)
        return self._writes.write(reservation, partial(device.set_power, True))

    def _stamped(self, shed: WriteReservation, at: datetime) -> bool:
        try:
            self._stamps.mark_shed(shed.key, at)
        except OperationalError as error:
            logger.warning('source_write_stamp_failed', source=shed.key, at=at.isoformat(), error=str(error))
            return False

        self._writes.stored(shed)
        return True
