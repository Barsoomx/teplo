from collections.abc import Collection, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from apps.power.domain.control import PhaseThresholds
from apps.power.domain.headroom import Currents
from apps.power.domain.load_status import LoadStatus
from apps.power.domain.loads import LoadKind, LoadPhase, ShedReason
from apps.power.domain.moments import elapsed, held
from apps.power.domain.shedding import Shed


class MeterCondition(StrEnum):
    FRESH = 'fresh'
    STALE = 'stale'
    LOST = 'lost'


@dataclass(frozen=True, kw_only=True)
class MeterWatch:
    seen: bool
    stale_since: float | None

    @classmethod
    def started(cls, at: float) -> 'MeterWatch':
        return cls(seen=False, stale_since=at)


def watch_meter(watch: MeterWatch, *, fresh: bool, at: float) -> MeterWatch:
    if fresh:
        return MeterWatch(seen=True, stale_since=None)

    return MeterWatch(seen=watch.seen, stale_since=at if watch.stale_since is None else watch.stale_since)


def meter_condition(watch: MeterWatch, at: float, thresholds: PhaseThresholds) -> MeterCondition:
    if watch.stale_since is None:
        return MeterCondition.FRESH

    return MeterCondition.LOST if held(watch.stale_since, at, thresholds.meter_lost_after) else MeterCondition.STALE


def drawing_floors(loads: Sequence[LoadStatus]) -> list[LoadStatus]:
    return sorted(
        (load for load in loads if load.kind is LoadKind.ZONE and load.available and load.drawing and not load.shed),
        key=lambda load: load.priority,
    )


def limp_sheds(loads: Sequence[LoadStatus], sheddable: Collection[str]) -> tuple[LoadStatus, ...]:
    taken: set[LoadPhase] = set()
    shed: list[LoadStatus] = []
    for load in reversed(drawing_floors(loads)):
        if load.slug in sheddable and load.phase in taken:
            shed.append(load)
        else:
            taken.add(load.phase)
    return tuple(reversed(shed))


def choose_limp_restore(
    sheds: Sequence[Shed],
    loads: Sequence[LoadStatus],
    *,
    calm: frozenset[LoadPhase],
    calming: frozenset[LoadPhase],
    overloaded: frozenset[LoadPhase],
    currents: Currents,
    last_restore_at: float | None,
    at: float,
    thresholds: PhaseThresholds,
) -> Shed | None:
    if not elapsed(last_restore_at, at, thresholds.restore_interval):
        return None

    taken = {floor.phase for floor in drawing_floors(loads)}
    statuses = {load.slug: load for load in loads if load.kind is LoadKind.ZONE and load.available}
    ready = sorted(
        (
            shed
            for shed in sheds
            if shed.load in statuses
            and statuses[shed.load].phase not in taken
            and _phase_allows_restore(
                shed,
                statuses[shed.load].amps,
                calm=calm,
                calming=calming,
                overloaded=overloaded,
                currents=currents,
                ceiling=thresholds.start_ceiling,
            )
        ),
        key=lambda shed: (not shed.restore_failed, statuses[shed.load].priority),
        reverse=True,
    )
    return next(iter(ready), None)


def _phase_allows_restore(
    shed: Shed,
    amps: Decimal,
    *,
    calm: frozenset[LoadPhase],
    calming: frozenset[LoadPhase],
    overloaded: frozenset[LoadPhase],
    currents: Currents,
    ceiling: Decimal,
) -> bool:
    if shed.phase in currents:
        return shed.phase in calm and currents[shed.phase] + amps <= ceiling

    if shed.phase in overloaded:
        return False

    return shed.reason is not ShedReason.OVERLOAD or shed.phase in calming
