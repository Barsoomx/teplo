from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import timedelta
from decimal import Decimal

from apps.power.domain.loads import MEASURED_PHASES, LoadPhase
from apps.power.domain.moments import held
from apps.power.domain.phase_learning import STEP_MARGIN, LoadChange
from apps.power.domain.phase_samples import PhaseSample, level_at

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


@dataclass(frozen=True, kw_only=True)
class EvDetection:
    watches: tuple[EvWatch, ...]
    started: tuple[EvWatch, ...]
    stopped: tuple[EvWatch, ...]


@dataclass(frozen=True, kw_only=True)
class _Levels:
    now: Decimal
    then: Decimal | None


def detect_ev(
    watches: Sequence[EvWatch],
    samples: Sequence[PhaseSample],
    changes: Sequence[LoadChange],
    at: float,
) -> EvDetection:
    previous = {watch.phase: watch for watch in watches}
    current = {phase: _follow(phase, previous.get(phase), samples, changes, at) for phase in MEASURED_PHASES}
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
    at: float,
) -> EvWatch | None:
    now = level_at(samples, phase, at)
    if now is None:
        return watch

    levels = _Levels(now=now, then=level_at(samples, phase, at - EV_RISE.total_seconds()))
    if watch is None:
        return _rise(phase, levels, changes, at)

    if _dropped(watch, levels):
        return None

    return watch if watch.charging else _held(watch, levels, changes, at)


def _rise(phase: LoadPhase, levels: _Levels, changes: Sequence[LoadChange], at: float) -> EvWatch | None:
    if levels.then is None or not EV_STEP_MIN <= levels.now - levels.then <= EV_STEP_MAX:
        return None

    since = at - EV_RISE.total_seconds()
    if _explained(since, at, changes):
        return None

    return EvWatch(
        phase=phase,
        since=since,
        until=at,
        before=levels.then,
        step=levels.now - levels.then,
        charging=False,
    )


def _dropped(watch: EvWatch, levels: _Levels) -> bool:
    if levels.now <= watch.before + EV_BACK:
        return True

    return levels.then is not None and levels.then - levels.now >= watch.step - EV_BACK


def _held(watch: EvWatch, levels: _Levels, changes: Sequence[LoadChange], at: float) -> EvWatch | None:
    rising = at - watch.since <= 2 * EV_RISE.total_seconds() and levels.now - watch.before > watch.step
    grown = replace(watch, until=at, step=levels.now - watch.before) if rising else watch
    if grown.step > EV_STEP_MAX or _explained(grown.since, grown.until, changes):
        return None

    return replace(grown, charging=True) if held(grown.since, at, EV_HOLD) else grown


def _explained(since: float, until: float, changes: Sequence[LoadChange]) -> bool:
    margin = STEP_MARGIN.total_seconds()
    return any(change.overlaps(since - margin, until + margin) for change in changes)


def _charging(watch: EvWatch | None) -> bool:
    return watch is not None and watch.charging
