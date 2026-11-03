from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from apps.climate.domain.change import ThermostatChange, plan_change
from apps.climate.domain.floor_policy import ThermostatTarget
from apps.climate.domain.modes import ZoneCycleOutcome
from apps.climate.domain.write_guard import write_allowed
from apps.core.domain.events import DomainEvent
from apps.devices.domain.readings import ThermostatState


@dataclass(frozen=True, kw_only=True)
class ZoneDrive:
    change: ThermostatChange
    outcome: ZoneCycleOutcome | None
    starts: bool


@dataclass(frozen=True, kw_only=True)
class ZoneStart:
    zone: str
    writes_live: bool


@dataclass(frozen=True, kw_only=True)
class StartAnswer:
    granted: bool
    note: DomainEvent | None


@dataclass(frozen=True, kw_only=True)
class StartPermits:
    answers: Mapping[str, StartAnswer]
    gated: frozenset[str]

    def note(self, zone: str) -> DomainEvent | None:
        answer = self.answers.get(zone)
        return None if answer is None else answer.note

    def granted(self, zone: str, *, writes_live: bool) -> bool:
        answer = self.answers.get(zone)
        if answer is not None:
            return answer.granted

        return not writes_live or zone not in self.gated


def plan_drive(
    target: ThermostatTarget,
    state: ThermostatState | None,
    *,
    last_write_at: datetime | None,
    now: datetime,
    held: bool,
) -> ZoneDrive:
    if held:
        return _settled(ZoneCycleOutcome.SHED)

    if state is None:
        return _settled(ZoneCycleOutcome.DEVICE_UNAVAILABLE)

    change = plan_change(target, state)
    if change.is_empty:
        return _settled(ZoneCycleOutcome.UNCHANGED)

    if not write_allowed(last_write_at, now):
        return _settled(ZoneCycleOutcome.THROTTLED)

    return ZoneDrive(change=change, outcome=None, starts=_starts(change, state))


def _settled(outcome: ZoneCycleOutcome) -> ZoneDrive:
    return ZoneDrive(change=ThermostatChange(), outcome=outcome, starts=False)


def _starts(change: ThermostatChange, state: ThermostatState) -> bool:
    if change.power:
        return True

    return change.setpoint is not None and change.setpoint > state.floor_temperature and not _draws(state)


def _draws(state: ThermostatState) -> bool:
    if state.heating is None:
        return state.power and state.setpoint > state.floor_temperature

    return state.power and state.heating
