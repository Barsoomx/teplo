import statistics
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

FIRE_WINDOW = timedelta(minutes=20)
FIRE_POINTS_NEEDED = 15
FIRE_RISE = Decimal('1.5')
FIRE_CALM = Decimal('0.3')
FIRE_CALM_FOR = timedelta(minutes=30)
FIRE_LONGEST = timedelta(hours=6)
SENSORS_TO_START = 2
RATE_STEP = Decimal('0.01')
SECONDS_PER_HOUR = 3600


class FireEnd(StrEnum):
    CALM = 'calm'
    TIMEOUT = 'timeout'


@dataclass(frozen=True, kw_only=True)
class AirPoint:
    moment: float
    air: Decimal


@dataclass(frozen=True, kw_only=True)
class FireWatch:
    points: Mapping[str, tuple[AirPoint, ...]] = field(default_factory=dict)
    fire_since: float | None = None
    calm_since: float | None = None

    @property
    def burning(self) -> bool:
        return self.fire_since is not None


@dataclass(frozen=True, kw_only=True)
class FireStep:
    watch: FireWatch
    started: bool
    ended: bool
    reason: FireEnd | None
    rates: Mapping[str, Decimal]


def follow_fire(watch: FireWatch, readings: Mapping[str, Decimal | None], *, moment: float) -> FireStep:
    points = {slug: _points(watch.points.get(slug, ()), air, moment) for slug, air in readings.items()}
    rates = _rates(points, readings)
    if watch.fire_since is None:
        return _watch_for_fire(points, rates, needed=min(SENSORS_TO_START, len(readings)), moment=moment)

    return _watch_the_fire(watch, points, rates, moment=moment)


def _points(kept: tuple[AirPoint, ...], air: Decimal | None, moment: float) -> tuple[AirPoint, ...]:
    window = FIRE_WINDOW.total_seconds()
    recent = tuple(point for point in kept if 0 < moment - point.moment < window)
    return recent if air is None else (*recent, AirPoint(moment=moment, air=air))


def _rates(points: Mapping[str, tuple[AirPoint, ...]], readings: Mapping[str, Decimal | None]) -> dict[str, Decimal]:
    rates: dict[str, Decimal] = {}
    for slug, air in readings.items():
        rate = None if air is None else _rate(points[slug])
        if rate is not None:
            rates[slug] = rate
    return rates


def _rate(points: tuple[AirPoint, ...]) -> Decimal | None:
    if len(points) < FIRE_POINTS_NEEDED:
        return None

    first = points[0].moment
    hours = [(point.moment - first) / SECONDS_PER_HOUR for point in points]
    slope, _intercept = statistics.linear_regression(hours, [float(point.air) for point in points])
    return Decimal(slope).quantize(RATE_STEP, rounding=ROUND_HALF_UP)


def _watch_for_fire(
    points: Mapping[str, tuple[AirPoint, ...]],
    rates: Mapping[str, Decimal],
    *,
    needed: int,
    moment: float,
) -> FireStep:
    rising = sum(1 for rate in rates.values() if rate >= FIRE_RISE)
    started = needed >= 1 and rising >= needed
    return FireStep(
        watch=FireWatch(points=points, fire_since=moment if started else None),
        started=started,
        ended=False,
        reason=None,
        rates=rates,
    )


def _watch_the_fire(
    watch: FireWatch,
    points: Mapping[str, tuple[AirPoint, ...]],
    rates: Mapping[str, Decimal],
    *,
    moment: float,
) -> FireStep:
    calm = bool(rates) and all(rate <= FIRE_CALM for rate in rates.values())
    calm_since = (moment if watch.calm_since is None else watch.calm_since) if calm else None
    reason = _end(watch.fire_since, calm_since, moment)
    if reason is not None:
        return FireStep(watch=FireWatch(points=points), started=False, ended=True, reason=reason, rates=rates)

    return FireStep(
        watch=FireWatch(points=points, fire_since=watch.fire_since, calm_since=calm_since),
        started=False,
        ended=False,
        reason=None,
        rates=rates,
    )


def _end(fire_since: float | None, calm_since: float | None, moment: float) -> FireEnd | None:
    if calm_since is not None and moment - calm_since >= FIRE_CALM_FOR.total_seconds():
        return FireEnd.CALM

    if fire_since is not None and moment - fire_since >= FIRE_LONGEST.total_seconds():
        return FireEnd.TIMEOUT

    return None
