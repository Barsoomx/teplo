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
        mode=_mode_change(before, after),
        setpoint=None if before.target == after.target else (before.target, after.target),
    )
    return None if change.is_empty else change


def teplo_window[TState](pair: ReadingPair[TState], checked_at: float) -> tuple[float, float]:
    return pair.before_moment - WRITE_SETTLE.total_seconds(), max(pair.after_moment, checked_at)


def _consecutive[TState](pair: ReadingPair[TState]) -> bool:
    return pair.after_moment - pair.before_moment <= MANUAL_PAIR_SPAN.total_seconds()


def _mode_change(before: ClimateUnitState, after: ClimateUnitState) -> tuple[str, str] | None:
    modes = _unit_mode(before), _unit_mode(after)
    if not (before.on and after.on) or modes[0] == modes[1]:
        return None

    return modes


def _unit_mode(state: ClimateUnitState) -> str:
    return state.mode.value if state.mode_word is None else state.mode_word.value
