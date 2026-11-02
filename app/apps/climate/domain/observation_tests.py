from dataclasses import replace
from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from apps.climate.domain.learning import AirSample, Morning
from apps.climate.domain.modes import AirState, ScheduleMode, ZoneCycleOutcome
from apps.climate.domain.observation import (
    MorningProgress,
    MorningReading,
    StopProgress,
    StopReading,
    StopWatch,
    note_preheat,
    observe_morning,
    observe_stop,
    start_stop_watch,
)
from apps.climate.domain.zone import ComfortWindow

MOSCOW = ZoneInfo('Europe/Moscow')
PREHEAT_START = datetime(2026, 11, 2, 3, 30, tzinfo=MOSCOW)
COMFORT_AT = datetime(2026, 11, 2, 6, 30, tzinfo=MOSCOW)
YESTERDAYS_COMFORT_AT = COMFORT_AT - timedelta(days=1)
STOPPED_AT = datetime(2026, 11, 2, 9, 0, tzinfo=MOSCOW)
WINDOWS = tuple(ComfortWindow(weekday=weekday, start=time(6, 30), end=time(23, 30)) for weekday in range(7))
TARGET = Decimal('22.0')
OUTDOOR = Decimal('-3.4')
AIR = Decimal('20.1')
FIRST_SAMPLE = AirSample(at=PREHEAT_START, air=Decimal('20.0'))


def _reading(at: datetime, *, air: str = '20.1', mode: ScheduleMode = ScheduleMode.PREHEAT) -> MorningReading:
    return MorningReading(
        at=at,
        mode=mode,
        previous_mode=ScheduleMode.PREHEAT,
        preheated_for=None,
        windows=WINDOWS,
        target=TARGET,
        air=Decimal(air),
        outdoor=OUTDOOR,
        heating=True,
        outcome=ZoneCycleOutcome.UNCHANGED,
        air_state=None,
    )


@pytest.fixture
def f_start_reading() -> MorningReading:
    return replace(_reading(PREHEAT_START, air='20.0'), previous_mode=ScheduleMode.NIGHT)


@pytest.fixture
def f_morning() -> Morning:
    return Morning(comfort_at=COMFORT_AT, target=TARGET, outdoor=OUTDOOR, samples=(FIRST_SAMPLE,))


@pytest.fixture
def f_watch() -> StopWatch:
    return StopWatch(stopped_at=STOPPED_AT, target=TARGET, peak=Decimal('21.8'))


@pytest.mark.parametrize('air_state', [None, AirState.HEAT], ids=['floor_heats', 'air_mode_heats'])
def test_morning_starts_when_night_turns_into_preheat(
    f_start_reading: MorningReading,
    air_state: AirState | None,
) -> None:
    reading = replace(f_start_reading, air_state=air_state)

    assert observe_morning(None, reading) == MorningProgress(
        morning=Morning(comfort_at=COMFORT_AT, target=TARGET, outdoor=OUTDOOR, samples=(FIRST_SAMPLE,)),
        finished=None,
    )


@pytest.mark.parametrize(
    ('previous_mode', 'mode', 'air', 'outdoor', 'heating'),
    [
        (None, ScheduleMode.PREHEAT, AIR, OUTDOOR, True),
        (ScheduleMode.PREHEAT, ScheduleMode.PREHEAT, AIR, OUTDOOR, True),
        (ScheduleMode.COMFORT, ScheduleMode.PREHEAT, AIR, OUTDOOR, True),
        (ScheduleMode.NIGHT, ScheduleMode.NIGHT, AIR, OUTDOOR, True),
        (ScheduleMode.NIGHT, ScheduleMode.PREHEAT, None, OUTDOOR, True),
        (ScheduleMode.NIGHT, ScheduleMode.PREHEAT, AIR, None, True),
        (ScheduleMode.NIGHT, ScheduleMode.PREHEAT, AIR, OUTDOOR, False),
    ],
    ids=[
        'first_cycle_after_restart',
        'preheat_already_running',
        'preheat_right_after_comfort',
        'still_night',
        'air_sensor_lost',
        'outdoor_sensor_lost',
        'zone_not_heating_on_schedule',
    ],
)
def test_morning_starts_only_from_night_with_both_sensors_and_scheduled_heat(
    previous_mode: ScheduleMode | None,
    mode: ScheduleMode,
    air: Decimal | None,
    outdoor: Decimal | None,
    heating: bool,
) -> None:
    reading = MorningReading(
        at=PREHEAT_START,
        mode=mode,
        previous_mode=previous_mode,
        preheated_for=None,
        windows=WINDOWS,
        target=TARGET,
        air=air,
        outdoor=outdoor,
        heating=heating,
        outcome=ZoneCycleOutcome.APPLIED,
        air_state=None,
    )

    assert observe_morning(None, reading) == MorningProgress(morning=None, finished=None)


@pytest.mark.parametrize(
    ('at', 'preheated_for'),
    [
        (PREHEAT_START, YESTERDAYS_COMFORT_AT),
        (PREHEAT_START + timedelta(minutes=16), None),
    ],
    ids=['preheated_for_yesterday', 'sensor_back_after_preheat_start'],
)
def test_morning_starts_for_a_comfort_start_not_preheated_for(
    f_start_reading: MorningReading,
    at: datetime,
    preheated_for: datetime | None,
) -> None:
    reading = replace(f_start_reading, at=at, preheated_for=preheated_for)

    assert observe_morning(None, reading) == MorningProgress(
        morning=Morning(
            comfort_at=COMFORT_AT,
            target=TARGET,
            outdoor=OUTDOOR,
            samples=(AirSample(at=at, air=Decimal('20.0')),),
        ),
        finished=None,
    )


def test_no_morning_starts_for_a_comfort_start_already_preheated_for(f_start_reading: MorningReading) -> None:
    reading = replace(
        f_start_reading,
        at=PREHEAT_START + timedelta(minutes=16),
        preheated_for=COMFORT_AT,
    )

    assert observe_morning(None, reading) == MorningProgress(morning=None, finished=None)


@pytest.mark.parametrize(
    ('mode', 'outcome', 'preheated_for'),
    [
        (ScheduleMode.PREHEAT, ZoneCycleOutcome.APPLIED, COMFORT_AT),
        (ScheduleMode.PREHEAT, ZoneCycleOutcome.UNCHANGED, COMFORT_AT),
        (ScheduleMode.PREHEAT, ZoneCycleOutcome.DRY_RUN, COMFORT_AT),
        (ScheduleMode.PREHEAT, ZoneCycleOutcome.THROTTLED, YESTERDAYS_COMFORT_AT),
        (ScheduleMode.PREHEAT, ZoneCycleOutcome.DEVICE_UNAVAILABLE, YESTERDAYS_COMFORT_AT),
        (ScheduleMode.PREHEAT, ZoneCycleOutcome.FAILED, YESTERDAYS_COMFORT_AT),
        (ScheduleMode.NIGHT, ZoneCycleOutcome.APPLIED, YESTERDAYS_COMFORT_AT),
        (None, ZoneCycleOutcome.APPLIED, YESTERDAYS_COMFORT_AT),
    ],
    ids=[
        'written',
        'already_held',
        'journaled_in_dry_run',
        'write_waits_its_turn',
        'thermostat_unavailable',
        'write_failed',
        'night',
        'clock_untrusted',
    ],
)
def test_comfort_start_is_preheated_for_once_the_thermostat_follows_its_preheat(
    f_start_reading: MorningReading,
    mode: ScheduleMode | None,
    outcome: ZoneCycleOutcome,
    preheated_for: datetime,
) -> None:
    reading = replace(f_start_reading, mode=mode, outcome=outcome, preheated_for=YESTERDAYS_COMFORT_AT)

    assert note_preheat(reading) == preheated_for


@pytest.mark.parametrize(
    'outcome',
    [ZoneCycleOutcome.THROTTLED, ZoneCycleOutcome.DEVICE_UNAVAILABLE, ZoneCycleOutcome.FAILED],
    ids=['write_waits_its_turn', 'thermostat_unavailable', 'write_failed'],
)
def test_morning_starts_only_when_thermostat_takes_the_preheat(
    f_start_reading: MorningReading,
    outcome: ZoneCycleOutcome,
) -> None:
    reading = replace(f_start_reading, outcome=outcome)

    assert observe_morning(None, reading) == MorningProgress(morning=None, finished=None)


def test_morning_in_progress_collects_air_samples(f_morning: Morning) -> None:
    at = PREHEAT_START + timedelta(minutes=40)

    progress = observe_morning(f_morning, _reading(at))

    assert progress == MorningProgress(
        morning=replace(f_morning, samples=(FIRST_SAMPLE, AirSample(at=at, air=Decimal('20.1')))),
        finished=None,
    )


@pytest.mark.parametrize(
    ('mode', 'air', 'outdoor', 'heating'),
    [
        (ScheduleMode.PREHEAT, None, OUTDOOR, True),
        (ScheduleMode.PREHEAT, AIR, None, True),
        (ScheduleMode.PREHEAT, AIR, OUTDOOR, False),
        (ScheduleMode.NIGHT, AIR, OUTDOOR, True),
    ],
    ids=['air_sensor_lost', 'outdoor_sensor_lost', 'boost_or_disabled_or_dry_run', 'preheat_ended_early'],
)
def test_morning_is_dropped_when_its_conditions_break(
    f_morning: Morning,
    mode: ScheduleMode,
    air: Decimal | None,
    outdoor: Decimal | None,
    heating: bool,
) -> None:
    reading = MorningReading(
        at=PREHEAT_START + timedelta(hours=1),
        mode=mode,
        previous_mode=ScheduleMode.PREHEAT,
        preheated_for=None,
        windows=WINDOWS,
        target=TARGET,
        air=air,
        outdoor=outdoor,
        heating=heating,
        outcome=ZoneCycleOutcome.UNCHANGED,
        air_state=None,
    )

    assert observe_morning(f_morning, reading) == MorningProgress(morning=None, finished=None)


def test_morning_does_not_start_while_air_mode_idles(f_start_reading: MorningReading) -> None:
    reading = replace(f_start_reading, air_state=AirState.IDLE)

    assert observe_morning(None, reading) == MorningProgress(morning=None, finished=None)


def test_first_idle_minute_of_air_mode_finishes_morning(f_morning: Morning) -> None:
    at = PREHEAT_START + timedelta(hours=1, minutes=52)

    progress = observe_morning(f_morning, replace(_reading(at, air='21.7'), air_state=AirState.IDLE))

    assert progress == MorningProgress(
        morning=None,
        finished=replace(f_morning, samples=(FIRST_SAMPLE, AirSample(at=at, air=Decimal('21.7')))),
    )


@pytest.mark.parametrize(
    'outcome',
    [ZoneCycleOutcome.DEVICE_UNAVAILABLE, ZoneCycleOutcome.FAILED],
    ids=['thermostat_unavailable', 'write_failed'],
)
def test_morning_is_dropped_when_its_thermostat_is_out_of_reach(f_morning: Morning, outcome: ZoneCycleOutcome) -> None:
    reading = replace(_reading(PREHEAT_START + timedelta(hours=1)), outcome=outcome)

    assert observe_morning(f_morning, reading) == MorningProgress(morning=None, finished=None)


def test_morning_goes_on_while_a_write_waits_its_turn(f_morning: Morning) -> None:
    at = PREHEAT_START + timedelta(minutes=40)

    progress = observe_morning(f_morning, replace(_reading(at), outcome=ZoneCycleOutcome.THROTTLED))

    assert progress == MorningProgress(
        morning=replace(f_morning, samples=(FIRST_SAMPLE, AirSample(at=at, air=Decimal('20.1')))),
        finished=None,
    )


@pytest.mark.parametrize('air', [Decimal('21.9'), None], ids=['sensor_reading', 'sensor_lost'])
def test_morning_finishes_when_comfort_period_starts(f_morning: Morning, air: Decimal | None) -> None:
    reading = replace(_reading(COMFORT_AT, mode=ScheduleMode.COMFORT), air=air)

    assert observe_morning(f_morning, reading) == MorningProgress(morning=None, finished=f_morning)


def test_applied_stop_of_heat_starts_watch() -> None:
    watch = start_stop_watch(
        AirState.HEAT,
        AirState.IDLE,
        applied=True,
        heat_target=TARGET,
        target=TARGET,
        air=Decimal('21.8'),
        at=STOPPED_AT,
    )

    assert watch == StopWatch(stopped_at=STOPPED_AT, target=TARGET, peak=Decimal('21.8'))


@pytest.mark.parametrize(
    ('previous', 'state', 'applied', 'heat_target'),
    [
        (AirState.HEAT, AirState.IDLE, False, TARGET),
        (AirState.IDLE, AirState.IDLE, True, TARGET),
        (None, AirState.IDLE, True, None),
        (AirState.HEAT, AirState.HEAT, True, TARGET),
        (AirState.HEAT, AirState.IDLE, True, Decimal('22.5')),
    ],
    ids=['stop_not_applied', 'already_idle', 'entering_air_mode', 'still_heating', 'target_fell'],
)
def test_stop_watch_needs_applied_stop_of_heat_under_unchanged_target(
    previous: AirState | None,
    state: AirState,
    applied: bool,
    heat_target: Decimal | None,
) -> None:
    watch = start_stop_watch(
        previous,
        state,
        applied=applied,
        heat_target=heat_target,
        target=TARGET,
        air=Decimal('21.8'),
        at=STOPPED_AT,
    )

    assert watch is None


@pytest.mark.parametrize(
    ('after', 'air', 'peak'),
    [
        (timedelta(hours=1), Decimal('22.3'), Decimal('22.3')),
        (timedelta(hours=1), Decimal('21.6'), Decimal('21.8')),
        (timedelta(hours=2), Decimal('22.4'), Decimal('22.4')),
    ],
    ids=['higher_air_raises_peak', 'lower_air_keeps_peak', 'reading_at_two_hours_counts'],
)
def test_stop_watch_keeps_highest_air_for_two_hours(
    f_watch: StopWatch,
    after: timedelta,
    air: Decimal,
    peak: Decimal,
) -> None:
    progress = observe_stop(f_watch, StopReading(at=STOPPED_AT + after, air=air, watching=True, heating=False))

    assert progress == StopProgress(watch=replace(f_watch, peak=peak), finished=None)


@pytest.mark.parametrize('watching', [True, False])
def test_stop_watch_finishes_after_two_hours_without_later_reading(f_watch: StopWatch, watching: bool) -> None:
    reading = StopReading(
        at=STOPPED_AT + timedelta(hours=2, minutes=1),
        air=Decimal('23.0'),
        watching=watching,
        heating=False,
    )

    assert observe_stop(f_watch, reading) == StopProgress(watch=None, finished=f_watch)


def test_stop_watch_finishes_early_when_heat_starts_again(f_watch: StopWatch) -> None:
    reading = StopReading(
        at=STOPPED_AT + timedelta(minutes=40),
        air=Decimal('21.6'),
        watching=True,
        heating=True,
    )

    assert observe_stop(f_watch, reading) == StopProgress(watch=None, finished=f_watch)


@pytest.mark.parametrize(
    ('after', 'air', 'watching'),
    [
        (timedelta(hours=1), Decimal('22.3'), False),
        (timedelta(hours=1), None, True),
        (-timedelta(minutes=5), Decimal('22.3'), True),
    ],
    ids=['boost_or_no_live_air_control', 'air_sensor_lost', 'clock_moved_back'],
)
def test_stop_watch_is_dropped_when_it_cannot_watch(
    f_watch: StopWatch,
    after: timedelta,
    air: Decimal | None,
    watching: bool,
) -> None:
    reading = StopReading(at=STOPPED_AT + after, air=air, watching=watching, heating=False)

    assert observe_stop(f_watch, reading) == StopProgress(watch=None, finished=None)
