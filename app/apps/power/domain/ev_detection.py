from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import timedelta
from decimal import Decimal

from apps.power.domain.load_status import LoadStatus
from apps.power.domain.loads import MEASURED_PHASES, LoadPhase
from apps.power.domain.moments import held
from apps.power.domain.phase_learning import STEP_MARGIN, VOTE_DELAY, LoadChange, covered
from apps.power.domain.phase_samples import PhaseSample, level_at
from apps.power.domain.shedding import SETTLE, Shed

EV_STEP_MIN = Decimal(10)
EV_STEP_MAX = Decimal(22)
EV_RISE = timedelta(seconds=15)
EV_HOLD = timedelta(minutes=5)
EV_BACK = Decimal(3)


@dataclass(frozen=True, kw_only=True)
class EvWatch:
    phase: LoadPhase
    since: float
    until: float
    before: Decimal
    step: Decimal
    charging: bool
    relief: Decimal = Decimal(0)
    switched_off: Decimal = Decimal(0)
    drop_seen_at: float | None = None


@dataclass(frozen=True, kw_only=True)
class EvDetection:
    watches: tuple[EvWatch, ...]
    started: tuple[EvWatch, ...]
    stopped: tuple[EvWatch, ...]


@dataclass(frozen=True, kw_only=True)
class _Levels:
    now: Decimal
    then: Decimal | None


@dataclass(frozen=True, kw_only=True)
class _GuardSheds:
    relief: Decimal
    added: Decimal
    settling: bool
    recent: bool
    loads: frozenset[str]


def detect_ev(
    watches: Sequence[EvWatch],
    samples: Sequence[PhaseSample],
    changes: Sequence[LoadChange],
    at: float,
    *,
    sheds: Sequence[Shed],
    loads: Sequence[LoadStatus],
) -> EvDetection:
    previous = {watch.phase: watch for watch in watches}
    current = {
        phase: _follow(phase, previous.get(phase), samples, changes, _guard_sheds(sheds, phase, at), loads, at)
        for phase in MEASURED_PHASES
    }
    return EvDetection(
        watches=tuple(watch for watch in current.values() if watch is not None),
        started=tuple(
            watch
            for phase, watch in current.items()
            if watch is not None and watch.charging and not _charging(previous.get(phase))
        ),
        stopped=tuple(watch for phase, watch in previous.items() if watch.charging and not _charging(current[phase])),
    )


def _follow(
    phase: LoadPhase,
    watch: EvWatch | None,
    samples: Sequence[PhaseSample],
    changes: Sequence[LoadChange],
    sheds: _GuardSheds,
    loads: Sequence[LoadStatus],
    at: float,
) -> EvWatch | None:
    now = level_at(samples, phase, at)
    if now is None:
        return watch

    levels = _Levels(now=now, then=level_at(samples, phase, at - EV_RISE.total_seconds()))
    if watch is None:
        return _rise(phase, levels, samples, changes, sheds, at)

    if watch.drop_seen_at is not None:
        return _after_drop(watch, watch.drop_seen_at, levels, changes, sheds, loads, at)

    if _dropped(watch, levels, sheds):
        return replace(watch, drop_seen_at=at)

    return watch if watch.charging else _held(watch, levels, changes, sheds, at)


def _rise(
    phase: LoadPhase,
    levels: _Levels,
    samples: Sequence[PhaseSample],
    changes: Sequence[LoadChange],
    sheds: _GuardSheds,
    at: float,
) -> EvWatch | None:
    since = at - EV_RISE.total_seconds()
    if levels.then is None or sheds.settling or not _covered(samples, since, at):
        return None

    step = levels.now + sheds.added - levels.then
    if not EV_STEP_MIN <= step <= EV_STEP_MAX or _explained(since, at, changes):
        return None

    return EvWatch(
        phase=phase,
        since=since,
        until=at,
        before=levels.then,
        step=step,
        charging=False,
        relief=sheds.relief - sheds.added,
    )


def _dropped(watch: EvWatch, levels: _Levels, sheds: _GuardSheds) -> bool:
    if levels.now <= _base(watch, sheds) + EV_BACK:
        return True

    return not sheds.recent and levels.then is not None and levels.then - levels.now >= watch.step - EV_BACK


def _after_drop(
    watch: EvWatch,
    drop_seen_at: float,
    levels: _Levels,
    changes: Sequence[LoadChange],
    sheds: _GuardSheds,
    loads: Sequence[LoadStatus],
    at: float,
) -> EvWatch | None:
    if at - drop_seen_at < VOTE_DELAY.total_seconds():
        return watch

    switched_off = _switched_off(watch.phase, drop_seen_at, changes, sheds, loads)
    if switched_off == 0:
        return None

    kept = replace(watch, switched_off=watch.switched_off + switched_off, drop_seen_at=None)
    return None if levels.now <= _base(kept, sheds) + EV_BACK else kept


def _switched_off(
    phase: LoadPhase,
    drop_seen_at: float,
    changes: Sequence[LoadChange],
    sheds: _GuardSheds,
    loads: Sequence[LoadStatus],
) -> Decimal:
    margin = STEP_MARGIN.total_seconds()
    since = drop_seen_at - EV_RISE.total_seconds() - margin
    off = {
        change.load
        for change in changes
        if not change.up and change.overlaps(since, drop_seen_at + margin) and change.load not in sheds.loads
    }
    return sum(
        (load.amps for load in loads if load.slug in off and load.phase in (phase, LoadPhase.UNKNOWN)),
        Decimal(0),
    )


def _held(
    watch: EvWatch,
    levels: _Levels,
    changes: Sequence[LoadChange],
    sheds: _GuardSheds,
    at: float,
) -> EvWatch | None:
    step = levels.now - _base(watch, sheds)
    rising = at - watch.since <= 2 * EV_RISE.total_seconds() and not sheds.settling and step > watch.step
    grown = replace(watch, until=at, step=step) if rising else watch
    if grown.step > EV_STEP_MAX or _explained(grown.since, grown.until, changes):
        return None

    return replace(grown, charging=True) if held(grown.since, at, EV_HOLD) else grown


def _guard_sheds(sheds: Sequence[Shed], phase: LoadPhase, at: float) -> _GuardSheds:
    live = [shed for shed in sheds if shed.live and shed.phase is phase]
    return _GuardSheds(
        relief=sum((shed.amps for shed in live), Decimal(0)),
        added=sum((shed.amps for shed in live if shed.at > at - EV_RISE.total_seconds()), Decimal(0)),
        settling=any(shed.at > at - SETTLE.total_seconds() for shed in live),
        recent=any(shed.at > at - (EV_RISE + STEP_MARGIN).total_seconds() for shed in live),
        loads=frozenset(shed.load for shed in sheds if shed.live),
    )


def _base(watch: EvWatch, sheds: _GuardSheds) -> Decimal:
    return watch.before - watch.switched_off - (sheds.relief - watch.relief)


def _covered(samples: Sequence[PhaseSample], since: float, at: float) -> bool:
    return covered([sample.at for sample in samples], since - STEP_MARGIN.total_seconds(), at)


def _explained(since: float, until: float, changes: Sequence[LoadChange]) -> bool:
    margin = STEP_MARGIN.total_seconds()
    return any(change.up and change.overlaps(since - margin, until + margin) for change in changes)


def _charging(watch: EvWatch | None) -> bool:
    return watch is not None and watch.charging
