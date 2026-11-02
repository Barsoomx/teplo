from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal

from apps.climate.domain.learning import EARLY_STOP_WATCH, AirSample, Morning
from apps.climate.domain.modes import FOLLOWED, UNREACHED, AirState, ScheduleMode, ZoneCycleOutcome
from apps.climate.domain.schedule import next_comfort_start
from apps.climate.domain.zone import ComfortWindow


@dataclass(frozen=True, kw_only=True)
class MorningReading:
    at: datetime
    mode: ScheduleMode
    previous_mode: ScheduleMode | None
    windows: tuple[ComfortWindow, ...]
    target: Decimal
    air: Decimal | None
    outdoor: Decimal | None
    heating: bool
    outcome: ZoneCycleOutcome


@dataclass(frozen=True, kw_only=True)
class MorningProgress:
    morning: Morning | None
    finished: Morning | None


@dataclass(frozen=True, kw_only=True)
class StopWatch:
    stopped_at: datetime
    target: Decimal
    peak: Decimal


@dataclass(frozen=True, kw_only=True)
class StopReading:
    at: datetime
    air: Decimal | None
    watching: bool
    heating: bool


@dataclass(frozen=True, kw_only=True)
class StopProgress:
    watch: StopWatch | None
    finished: StopWatch | None


def observe_morning(morning: Morning | None, reading: MorningReading) -> MorningProgress:
    if morning is None:
        return MorningProgress(morning=_start_morning(reading), finished=None)

    if reading.at >= morning.comfort_at:
        return MorningProgress(morning=None, finished=morning)

    sample = _sample(reading)
    if sample is None or reading.outdoor is None or reading.mode is not ScheduleMode.PREHEAT:
        return MorningProgress(morning=None, finished=None)

    return MorningProgress(morning=replace(morning, samples=(*morning.samples, sample)), finished=None)


def start_stop_watch(
    previous: AirState | None,
    state: AirState | None,
    *,
    applied: bool,
    heat_target: Decimal | None,
    target: Decimal,
    air: Decimal,
    at: datetime,
) -> StopWatch | None:
    if not applied or previous is not AirState.HEAT or state is not AirState.IDLE or heat_target != target:
        return None

    return StopWatch(stopped_at=at, target=target, peak=air)


def observe_stop(watch: StopWatch, reading: StopReading) -> StopProgress:
    if reading.at - watch.stopped_at > EARLY_STOP_WATCH:
        return StopProgress(watch=None, finished=watch)

    if reading.at < watch.stopped_at or not reading.watching or reading.air is None:
        return StopProgress(watch=None, finished=None)

    if reading.heating:
        return StopProgress(watch=None, finished=watch)

    return StopProgress(watch=replace(watch, peak=max(watch.peak, reading.air)), finished=None)


def _start_morning(reading: MorningReading) -> Morning | None:
    sample = _sample(reading)
    if sample is None or reading.outdoor is None or reading.outcome not in FOLLOWED:
        return None

    if reading.previous_mode is not ScheduleMode.NIGHT or reading.mode is not ScheduleMode.PREHEAT:
        return None

    comfort_at = next_comfort_start(reading.windows, reading.at)
    if comfort_at is None:
        return None

    return Morning(comfort_at=comfort_at, target=reading.target, outdoor=reading.outdoor, samples=(sample,))


def _sample(reading: MorningReading) -> AirSample | None:
    if not reading.heating or reading.air is None or reading.outcome in UNREACHED:
        return None

    return AirSample(at=reading.at, air=reading.air)
