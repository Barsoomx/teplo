from collections.abc import Collection, Sequence
from dataclasses import dataclass
from enum import StrEnum

from apps.power.domain.control import PhaseThresholds
from apps.power.domain.load_status import LoadStatus
from apps.power.domain.loads import LoadKind, LoadPhase
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


def phases_overlap(first: LoadPhase, second: LoadPhase) -> bool:
    return LoadPhase.UNKNOWN in (first, second) or first is second


def drawing_floors(loads: Sequence[LoadStatus]) -> list[LoadStatus]:
    return sorted(
        (load for load in loads if load.kind is LoadKind.ZONE and load.available and load.drawing and not load.shed),
        key=lambda load: load.priority,
    )


def limp_sheds(loads: Sequence[LoadStatus], sheddable: Collection[str]) -> tuple[LoadStatus, ...]:
    kept: list[LoadStatus] = []
    shed: list[LoadStatus] = []
    for load in reversed(drawing_floors(loads)):
        if load.slug in sheddable and any(phases_overlap(load.phase, other.phase) for other in kept):
            shed.append(load)
        else:
            kept.append(load)
    return tuple(reversed(shed))


def choose_limp_restore(
    sheds: Sequence[Shed],
    loads: Sequence[LoadStatus],
    *,
    last_restore_at: float | None,
    at: float,
    thresholds: PhaseThresholds,
) -> Shed | None:
    if not elapsed(last_restore_at, at, thresholds.restore_interval):
        return None

    floors = drawing_floors(loads)
    statuses = {load.slug: load for load in loads if load.kind is LoadKind.ZONE and load.available}
    ready = sorted(
        (
            shed
            for shed in sheds
            if shed.load in statuses
            and not any(phases_overlap(statuses[shed.load].phase, floor.phase) for floor in floors)
        ),
        key=lambda shed: (not shed.restore_failed, statuses[shed.load].priority),
        reverse=True,
    )
    return next(iter(ready), None)
