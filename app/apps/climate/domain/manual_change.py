from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from apps.devices.domain.readings import ClimateUnitState, ReadingPair, ThermostatState

MANUAL_PAIR_SPAN = timedelta(seconds=90)
WRITE_SETTLE = timedelta(seconds=60)


@dataclass(frozen=True, kw_only=True)
class ManualChange:
    power: tuple[bool, bool] | None
    mode: tuple[str, str] | None
    setpoint: tuple[Decimal, Decimal] | None

    @property
    def is_empty(self) -> bool:
        return self.power is None and self.mode is None and self.setpoint is None


def thermostat_change(pair: ReadingPair[ThermostatState]) -> ManualChange | None:
    if not _consecutive(pair):
        return None

    before, after = pair.before, pair.after
    change = ManualChange(
        power=None if before.power == after.power else (before.power, after.power),
        mode=None if before.mode == after.mode else (before.mode.value, after.mode.value),
        setpoint=None if before.setpoint == after.setpoint else (before.setpoint, after.setpoint),
    )
    return None if change.is_empty else change


def source_change(pair: ReadingPair[ClimateUnitState]) -> ManualChange | None:
    if not _consecutive(pair):
        return None

    before, after = pair.before, pair.after
    change = ManualChange(
        power=None if before.on == after.on else (before.on, after.on),
        mode=None,
        setpoint=None if before.target == after.target else (before.target, after.target),
    )
    return None if change.is_empty else change


def teplo_window[TState](pair: ReadingPair[TState]) -> tuple[float, float]:
    return pair.before_moment - WRITE_SETTLE.total_seconds(), pair.after_moment


def _consecutive[TState](pair: ReadingPair[TState]) -> bool:
    return pair.after_moment - pair.before_moment <= MANUAL_PAIR_SPAN.total_seconds()
