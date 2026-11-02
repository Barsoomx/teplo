from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from apps.power.domain.control import PhaseThresholds
from apps.power.domain.load_status import LoadStatus
from apps.power.domain.loads import LoadKind
from apps.power.domain.moments import held


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


def limp_sheds(loads: Sequence[LoadStatus]) -> tuple[LoadStatus, ...]:
    floors = drawing_floors(loads)
    kept = {load.phase: load.slug for load in floors}
    return tuple(load for load in floors if kept[load.phase] != load.slug)
