from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from apps.climate.domain.change import ThermostatChange, plan_change
from apps.climate.domain.floor_policy import ThermostatTarget
from apps.climate.domain.modes import ZoneCycleOutcome
from apps.climate.domain.write_guard import next_write_at, write_allowed
from apps.core.domain.events import DomainEvent
from apps.devices.domain.readings import ThermostatState


class PendingKind(StrEnum):
    HOLDS = 'holds'
    NEXT_WRITE = 'next_write'
    HELD = 'held'
    HANDS_OFF = 'hands_off'
    WATCHING = 'watching'
    UNREACHED = 'unreached'


@dataclass(frozen=True, kw_only=True)
class PendingWrite:
    kind: PendingKind
    at: datetime | None


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
    hands_off: bool,
    slot_free: bool,
) -> ZoneDrive:
    if held:
        return _settled(ZoneCycleOutcome.SHED)

    if hands_off:
        return _settled(ZoneCycleOutcome.HANDS_OFF)

    if state is None:
        return _settled(ZoneCycleOutcome.DEVICE_UNAVAILABLE)

    change = plan_change(target, state)
    if change.is_empty:
        return _settled(ZoneCycleOutcome.UNCHANGED)

    if not slot_free or not write_allowed(last_write_at, now):
        return _settled(ZoneCycleOutcome.THROTTLED)

    return ZoneDrive(change=change, outcome=None, starts=_starts(change, state))


def pending_write(
    target: ThermostatTarget,
    state: ThermostatState | None,
    *,
    last_write_at: datetime | None,
    now: datetime,
    held: bool,
    writes_live: bool,
    hands_off_until: datetime | None,
) -> PendingWrite:
    if held:
        return PendingWrite(kind=PendingKind.HELD, at=None)

    if hands_off_until is not None:
        return PendingWrite(kind=PendingKind.HANDS_OFF, at=hands_off_until)

    if state is None:
        return PendingWrite(kind=PendingKind.UNREACHED, at=None)

    if plan_change(target, state).is_empty:
        return PendingWrite(kind=PendingKind.HOLDS, at=None)

    if not writes_live:
        return PendingWrite(kind=PendingKind.WATCHING, at=None)

    return PendingWrite(kind=PendingKind.NEXT_WRITE, at=next_write_at(last_write_at, now))


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
