from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from apps.climate.domain.drive import PendingKind, PendingWrite
from apps.climate.domain.heat_sources import HeatSourceKind, SourceSettings
from apps.climate.domain.modes import ZoneCycleOutcome
from apps.climate.domain.write_guard import next_write_at, write_allowed
from apps.devices.domain.readings import ClimateMode, ClimateUnitState

SOURCE_START_BELOW = Decimal('0.3')
SOURCE_STOP_ABOVE = Decimal('1.0')
SOURCE_SETPOINT_MIN = Decimal(5)
HEATER_STEP = Decimal(1)
SETPOINT_STEPS = {HeatSourceKind.HEATER: HEATER_STEP, HeatSourceKind.AIR_CONDITIONER: Decimal('0.5')}
TEPLO_MODES = frozenset({ClimateMode.OFF, ClimateMode.HEAT})


@dataclass(frozen=True, kw_only=True)
class SourceTarget:
    power: bool
    setpoint: Decimal | None


@dataclass(frozen=True, kw_only=True)
class SourceChange:
    power: bool | None = None
    setpoint: Decimal | None = None

    @property
    def is_empty(self) -> bool:
        return self.power is None and self.setpoint is None


@dataclass(frozen=True, kw_only=True)
class SourceStart:
    source: str
    writes_live: bool


@dataclass(frozen=True, kw_only=True)
class SourceDrive:
    change: SourceChange
    outcome: ZoneCycleOutcome | None
    starts: bool


def source_setpoint(target: Decimal, source: SourceSettings) -> Decimal:
    step = SETPOINT_STEPS.get(source.kind, HEATER_STEP)
    wanted = _on_step(target + source.setpoint_bias, step, ROUND_HALF_UP)
    highest = _on_step(source.max_setpoint, step, ROUND_DOWN)
    return max(SOURCE_SETPOINT_MIN, min(wanted, highest))


def decide_source_power(on: bool, air: Decimal | None, target: Decimal) -> bool:
    if air is None or air < target - SOURCE_START_BELOW:
        return True

    if air >= target + SOURCE_STOP_ABOVE:
        return False

    return on


def room_held_by_hand(sources: Sequence[SourceSettings], now: datetime) -> bool:
    return any(source.held_by_hand(now) for source in sources)


def room_targets(
    sources: Sequence[SourceSettings],
    states: Mapping[str, ClimateUnitState | None],
    *,
    shed: frozenset[str],
    air: Decimal | None,
    target: Decimal,
    outdoor: Decimal | None,
    now: datetime,
) -> dict[str, SourceTarget | None]:
    if room_held_by_hand(sources, now):
        return {source.slug: None for source in sources}

    ordered = sorted(sources, key=lambda source: source.priority)
    pick = next((source for source in ordered if _pickable(source, states.get(source.slug), shed, outdoor)), None)
    return {
        source.slug: _target(source, states.get(source.slug), pick=pick, shed=shed, air=air, target=target)
        for source in ordered
    }


def plan_source_change(target: SourceTarget, state: ClimateUnitState) -> SourceChange:
    if state.mode not in TEPLO_MODES:
        return SourceChange()

    if not target.power:
        return SourceChange(power=False) if state.on else SourceChange()

    setpoint = None if target.setpoint is None or target.setpoint == state.target else target.setpoint
    return SourceChange(power=None if state.on else True, setpoint=setpoint)


def source_starts(change: SourceChange, state: ClimateUnitState) -> bool:
    if change.power:
        return True

    if change.setpoint is None or state.heating:
        return False

    if state.room_temperature is None:
        return change.setpoint > state.target

    return change.setpoint > state.room_temperature


def plan_source_drive(
    target: SourceTarget | None,
    state: ClimateUnitState | None,
    *,
    last_write_at: datetime | None,
    interval: timedelta,
    now: datetime,
    shed: bool,
    hands_off: bool,
    slot_free: bool,
) -> SourceDrive:
    if shed:
        return _settled(ZoneCycleOutcome.SHED)

    if hands_off:
        return _settled(ZoneCycleOutcome.HANDS_OFF)

    if state is None:
        return _settled(ZoneCycleOutcome.DEVICE_UNAVAILABLE)

    change = SourceChange() if target is None else plan_source_change(target, state)
    if change.is_empty:
        return _settled(ZoneCycleOutcome.UNCHANGED)

    if not slot_free or not write_allowed(last_write_at, now, interval):
        return _settled(ZoneCycleOutcome.THROTTLED)

    return SourceDrive(change=change, outcome=None, starts=source_starts(change, state))


def pending_source_write(
    target: SourceTarget | None,
    state: ClimateUnitState | None,
    *,
    last_write_at: datetime | None,
    interval: timedelta,
    now: datetime,
    shed: bool,
    hands_off_until: datetime | None,
    writes_live: bool,
) -> PendingWrite | None:
    if shed:
        return PendingWrite(kind=PendingKind.HELD, at=None)

    if hands_off_until is not None:
        return PendingWrite(kind=PendingKind.HANDS_OFF, at=hands_off_until)

    if state is None:
        return PendingWrite(kind=PendingKind.UNREACHED, at=None)

    if target is None:
        return None

    if plan_source_change(target, state).is_empty:
        return PendingWrite(kind=PendingKind.HOLDS, at=None)

    if not writes_live:
        return PendingWrite(kind=PendingKind.WATCHING, at=None)

    return PendingWrite(kind=PendingKind.NEXT_WRITE, at=next_write_at(last_write_at, now, interval))


def _pickable(
    source: SourceSettings,
    state: ClimateUnitState | None,
    shed: frozenset[str],
    outdoor: Decimal | None,
) -> bool:
    if state is None or state.mode not in TEPLO_MODES or source.slug in shed:
        return False

    return source.min_outdoor is None or (outdoor is not None and outdoor >= source.min_outdoor)


def _target(
    source: SourceSettings,
    state: ClimateUnitState | None,
    *,
    pick: SourceSettings | None,
    shed: frozenset[str],
    air: Decimal | None,
    target: Decimal,
) -> SourceTarget | None:
    if state is None:
        return None

    if pick is not None and source.slug == pick.slug:
        power = decide_source_power(state.on, air, target)
        return SourceTarget(power=power, setpoint=source_setpoint(target, source) if power else None)

    if state.on and source.slug not in shed and source.commanded_power is True:
        return SourceTarget(power=False, setpoint=None)

    return None


def _on_step(value: Decimal, step: Decimal, rounding: str) -> Decimal:
    return (value / step).quantize(Decimal(1), rounding=rounding) * step


def _settled(outcome: ZoneCycleOutcome) -> SourceDrive:
    return SourceDrive(change=SourceChange(), outcome=outcome, starts=False)
