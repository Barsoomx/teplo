from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from apps.power.domain.control import PhaseThresholds
from apps.power.domain.headroom import Currents, overrun
from apps.power.domain.load_status import LoadReading, LoadStatus
from apps.power.domain.loads import MEASURED_PHASES, LoadPhase, LoadRecord, ShedReason
from apps.power.domain.moments import elapsed

SETTLE = timedelta(seconds=5)
SETTLED = float('-inf')


@dataclass(frozen=True, kw_only=True)
class Shed:
    load: str
    phase: LoadPhase
    amps: Decimal
    live: bool
    reason: ShedReason
    was_on: bool | None
    at: float
    settled_at: float
    restore_failed: bool = False
    stamp_lost: bool = False


@dataclass(frozen=True, kw_only=True)
class UnconfirmedShed:
    shed: Shed
    amps: Decimal | None
    at: datetime


def new_shed(
    load: LoadStatus,
    *,
    phase: LoadPhase,
    live: bool,
    reason: ShedReason,
    was_on: bool | None,
    at: float,
) -> Shed:
    return Shed(
        load=load.slug,
        phase=phase,
        amps=load.amps,
        live=live,
        reason=reason,
        was_on=was_on,
        at=at,
        settled_at=at + SETTLE.total_seconds(),
    )


def unconfirmed_shed(
    load: LoadStatus,
    *,
    phase: LoadPhase,
    reason: ShedReason,
    amps: Decimal | None,
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
            at=at,
            settled_at=SETTLED,
        ),
        amps=amps,
        at=now,
    )


def shed_confirmed(unconfirmed: UnconfirmedShed, reading: LoadReading | None) -> bool | None:
    if reading is None or reading.read_at <= unconfirmed.shed.at:
        return None

    return reading.closed is False


def resumed_shed(record: LoadRecord, *, at: float, now: datetime, default_amps: Decimal) -> Shed | None:
    if record.shed is None:
        return None

    return Shed(
        load=record.slug,
        phase=record.shed.phase,
        amps=record.effective_amps(default_amps),
        live=True,
        reason=record.shed.reason,
        was_on=record.shed.was_on,
        at=at - max((now - record.shed.at).total_seconds(), 0.0),
        settled_at=SETTLED,
    )


def in_view(shed: Shed, *, live: bool) -> bool:
    return shed.live or not live


def relief(sheds: Iterable[Shed], phase: LoadPhase, read_at: float) -> Decimal:
    return sum(
        (shed.amps for shed in sheds if shed.phase is phase and (not shed.live or read_at < shed.settled_at)),
        Decimal(0),
    )


def can_hand_back(shed: Shed, at: float, write_interval: timedelta) -> bool:
    return not shed.stamp_lost or elapsed(shed.at, at, write_interval)


def switched_back_on(shed: Shed, reading: LoadReading | None) -> bool:
    if not shed.live or shed.was_on is not True or reading is None:
        return False

    return reading.read_at > shed.at and reading.closed is True


def choose_shed(phase: LoadPhase, loads: Sequence[LoadStatus]) -> LoadStatus | None:
    drawing = sorted(
        (load for load in loads if load.available and load.drawing and not load.shed),
        key=lambda load: load.priority,
    )
    on_phase = [load for load in drawing if load.phase is phase]
    of_unknown_phase = [load for load in drawing if load.phase is LoadPhase.UNKNOWN]
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


def _calm_for(phase: LoadPhase, calm: frozenset[LoadPhase]) -> bool:
    if phase is LoadPhase.UNKNOWN:
        return all(measured in calm for measured in MEASURED_PHASES)

    return phase in calm
