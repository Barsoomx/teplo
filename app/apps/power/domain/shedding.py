from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from apps.power.domain.control import PhaseThresholds
from apps.power.domain.headroom import Currents, overrun
from apps.power.domain.load_status import LoadReading, LoadStatus
from apps.power.domain.loads import MEASURED_PHASES, LiveShed, LoadKind, LoadPhase, LoadRecord, ShedReason
from apps.power.domain.moments import elapsed

SETTLE = timedelta(seconds=5)
SETTLED = float('-inf')


class HandBack(StrEnum):
    SWITCHED_ON = 'switched_on'
    UNTOUCHED = 'untouched'
    WAITING = 'waiting'


@dataclass(frozen=True, kw_only=True)
class Shed:
    load: str
    phase: LoadPhase
    amps: Decimal
    live: bool
    reason: ShedReason
    was_on: bool | None
    device: str
    at: float
    occurred_at: datetime
    settled_at: float
    restore_failed: bool = False
    unstamped_write_at: float | None = None


@dataclass(frozen=True, kw_only=True)
class ShedWriteHold:
    zone: str
    at: datetime
    elapsed: timedelta


@dataclass(frozen=True, kw_only=True)
class UnconfirmedShed:
    shed: Shed
    amps: Decimal | None


def new_shed(
    load: LoadStatus,
    *,
    phase: LoadPhase,
    live: bool,
    reason: ShedReason,
    was_on: bool | None,
    device: str,
    at: float,
    now: datetime,
) -> Shed:
    return Shed(
        load=load.slug,
        phase=phase,
        amps=load.amps,
        live=live,
        reason=reason,
        was_on=was_on,
        device=device,
        at=at,
        occurred_at=now,
        settled_at=at + SETTLE.total_seconds(),
    )


def unconfirmed_shed(
    load: LoadStatus,
    *,
    phase: LoadPhase,
    reason: ShedReason,
    amps: Decimal | None,
    device: str,
    at: float,
    now: datetime,
) -> UnconfirmedShed:
    return UnconfirmedShed(
        shed=Shed(
            load=load.slug,
            phase=phase,
            amps=load.amps,
            live=True,
            reason=reason,
            was_on=True,
            device=device,
            at=at,
            occurred_at=now,
            settled_at=SETTLED,
        ),
        amps=amps,
    )


def stored_shed(shed: Shed, *, handed_back: bool = False) -> LiveShed:
    return LiveShed(
        at=shed.occurred_at,
        reason=shed.reason,
        phase=shed.phase,
        was_on=shed.was_on,
        device=shed.device,
        handed_back=handed_back,
    )


def shed_confirmed(unconfirmed: UnconfirmedShed, record: LoadRecord, reading: LoadReading | None) -> bool | None:
    if moved(unconfirmed.shed, record):
        return True

    if reading is None or reading.read_at <= unconfirmed.shed.at:
        return None

    return reading.closed is False


def resumed_shed(record: LoadRecord, *, at: float, now: datetime, default_amps: Decimal) -> Shed | None:
    stored = record.shed
    if stored is None or stored.handed_back:
        return None

    shed = _resumed(record, stored, at=at, now=now, default_amps=default_amps)
    return replace(shed, unstamped_write_at=shed.at) if record.kind is LoadKind.ZONE else shed


def resumed_hand_back(record: LoadRecord, *, at: float, now: datetime, default_amps: Decimal) -> Shed | None:
    stored = record.shed
    if stored is None or not stored.handed_back:
        return None

    return _resumed(record, stored, at=at, now=now, default_amps=default_amps)


def resumed_write_hold(record: LoadRecord, *, now: datetime, write_interval: timedelta) -> ShedWriteHold | None:
    stored = record.shed
    if stored is None or record.zone_slug is None or _written_after_its_hold(record, stored, write_interval):
        return None

    elapsed = max(now - stored.at, timedelta(0))
    if elapsed >= write_interval:
        return None

    return ShedWriteHold(zone=record.zone_slug, at=stored.at, elapsed=elapsed)


def in_view(shed: Shed, *, live: bool) -> bool:
    return shed.live or not live


def relief(sheds: Iterable[Shed], phase: LoadPhase, read_at: float, *, estimated: bool = False) -> Decimal:
    return sum(
        (shed.amps for shed in sheds if shed.phase is phase and _relieves(shed, read_at, estimated=estimated)),
        Decimal(0),
    )


def can_hand_back(shed: Shed, at: float, write_interval: timedelta) -> bool:
    return elapsed(shed.unstamped_write_at, at, write_interval)


def drawing_since(shed: Shed, reading: LoadReading | None) -> bool:
    return reading is not None and reading.read_at > shed.at and reading.drawing


def moved(shed: Shed, record: LoadRecord) -> bool:
    return shed.live and shed.device != record.device_slug


def switched_back_on(shed: Shed, reading: LoadReading | None) -> bool:
    if not shed.live or shed.was_on is not True or reading is None:
        return False

    return reading.read_at > shed.at and reading.closed is True


def choose_shed(phase: LoadPhase, loads: Sequence[LoadStatus], *, unknown_phase: bool = True) -> LoadStatus | None:
    drawing = sorted(
        (load for load in loads if load.available and load.drawing and not load.shed),
        key=lambda load: load.priority,
    )
    on_phase = [load for load in drawing if load.phase is phase]
    of_unknown_phase = [load for load in drawing if unknown_phase and load.phase is LoadPhase.UNKNOWN]
    return next(iter([*on_phase, *of_unknown_phase]), None)


def choose_restore(
    sheds: Sequence[Shed],
    loads: Sequence[LoadStatus],
    *,
    calm: frozenset[LoadPhase],
    currents: Currents,
    last_restore_at: float | None,
    at: float,
    thresholds: PhaseThresholds,
) -> Shed | None:
    if not elapsed(last_restore_at, at, thresholds.restore_interval):
        return None

    statuses = {load.slug: load for load in loads if load.available}
    ready = sorted(
        (shed for shed in sheds if shed.load in statuses and _calm_for(shed.phase, calm)),
        key=lambda shed: (not shed.restore_failed, statuses[shed.load].priority),
        reverse=True,
    )
    return next(
        (
            shed
            for shed in ready
            if overrun(statuses[shed.load].phase, statuses[shed.load].amps, currents, thresholds.start_ceiling) is None
        ),
        None,
    )


def _resumed(record: LoadRecord, stored: LiveShed, *, at: float, now: datetime, default_amps: Decimal) -> Shed:
    return Shed(
        load=record.slug,
        phase=stored.phase,
        amps=record.effective_amps(default_amps),
        live=True,
        reason=stored.reason,
        was_on=stored.was_on,
        device=stored.device,
        at=at - max((now - stored.at).total_seconds(), 0.0),
        occurred_at=stored.at,
        settled_at=SETTLED,
    )


def _relieves(shed: Shed, read_at: float, *, estimated: bool) -> bool:
    if not shed.live:
        return True

    return not estimated and read_at < shed.settled_at


def _written_after_its_hold(record: LoadRecord, stored: LiveShed, write_interval: timedelta) -> bool:
    return record.last_command_at is not None and record.last_command_at - stored.at >= write_interval


def _calm_for(phase: LoadPhase, calm: frozenset[LoadPhase]) -> bool:
    if phase is LoadPhase.UNKNOWN:
        return all(measured in calm for measured in MEASURED_PHASES)

    return phase in calm
