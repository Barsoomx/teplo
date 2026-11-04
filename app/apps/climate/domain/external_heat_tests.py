from decimal import Decimal

import pytest

from apps.climate.domain.external_heat import AirPoint, FireEnd, FireStep, FireWatch, follow_fire

MINUTE = 60.0
HOUR = 60 * MINUTE
NOW = 14 * MINUTE
RISE_PER_MINUTE = Decimal('0.03')
RISING = tuple(Decimal('20.0') + RISE_PER_MINUTE * minute for minute in range(14))
FLAT = tuple(Decimal('20.0') for _ in range(14))
RISING_NOW = Decimal('20.0') + RISE_PER_MINUTE * 14
FLAT_NOW = Decimal('20.0')
OPEN_SPACE = ('air-hallway', 'air-kitchen', 'air-living-room')

WatchedSensors = dict[str, tuple[Decimal, ...]]


def _points(airs: tuple[Decimal, ...], *, since: float) -> tuple[AirPoint, ...]:
    return tuple(AirPoint(moment=since + index * MINUTE, air=air) for index, air in enumerate(airs))


@pytest.fixture
def f_watch(request: pytest.FixtureRequest) -> FireWatch:
    sensors: WatchedSensors = request.param.get('sensors', {})
    return FireWatch(
        points={slug: _points(airs, since=NOW - len(airs) * MINUTE) for slug, airs in sensors.items()},
        fire_since=request.param.get('fire_since'),
        calm_since=request.param.get('calm_since'),
    )


@pytest.fixture
def f_aged_watch() -> FireWatch:
    return FireWatch(
        points={
            'air-kitchen': (
                AirPoint(moment=0.0, air=Decimal('20.1')),
                AirPoint(moment=600.0, air=Decimal('20.2')),
                AirPoint(moment=601.0, air=Decimal('20.3')),
            ),
        },
    )


def test_points_older_than_twenty_minutes_drop(f_aged_watch: FireWatch) -> None:
    step = follow_fire(f_aged_watch, {'air-kitchen': Decimal('20.4')}, moment=1800.0)

    assert step.watch.points == {
        'air-kitchen': (AirPoint(moment=601.0, air=Decimal('20.3')), AirPoint(moment=1800.0, air=Decimal('20.4'))),
    }


@pytest.mark.parametrize(
    ('f_watch', 'rates'),
    [
        ({'sensors': {'air-kitchen': RISING[1:]}}, {}),
        ({'sensors': {'air-kitchen': RISING}}, {'air-kitchen': Decimal('1.80')}),
    ],
    ids=['fourteen_points', 'fifteen_points'],
    indirect=['f_watch'],
)
def test_a_sensor_gets_its_rate_from_fifteen_points(f_watch: FireWatch, rates: dict[str, Decimal]) -> None:
    step = follow_fire(f_watch, {'air-kitchen': RISING_NOW}, moment=NOW)

    assert step.rates == rates


@pytest.mark.parametrize(
    ('f_watch', 'readings', 'started'),
    [
        (
            {'sensors': {'air-hallway': FLAT, 'air-kitchen': RISING, 'air-living-room': RISING}},
            {'air-hallway': FLAT_NOW, 'air-kitchen': RISING_NOW, 'air-living-room': RISING_NOW},
            True,
        ),
        (
            {'sensors': {'air-hallway': FLAT, 'air-kitchen': RISING, 'air-living-room': FLAT}},
            {'air-hallway': FLAT_NOW, 'air-kitchen': RISING_NOW, 'air-living-room': FLAT_NOW},
            False,
        ),
        ({'sensors': {'air-office': RISING}}, {'air-office': RISING_NOW}, True),
    ],
    ids=['two_sensors_of_three_rise', 'one_sensor_of_three_rises', 'the_only_sensor_of_a_room_rises'],
    indirect=['f_watch'],
)
def test_a_fire_starts_once_two_sensors_of_the_room_or_its_only_one_rise(
    f_watch: FireWatch,
    readings: dict[str, Decimal | None],
    started: bool,
) -> None:
    step = follow_fire(f_watch, readings, moment=NOW)

    assert (step.started, step.watch.burning, step.watch.fire_since) == (started, started, NOW if started else None)


@pytest.mark.parametrize('f_watch', [{}], indirect=True)
def test_a_room_without_an_air_sensor_starts_no_fire(f_watch: FireWatch) -> None:
    step = follow_fire(f_watch, {}, moment=NOW)

    assert step == FireStep(watch=FireWatch(), started=False, ended=False, reason=None, rates={})


@pytest.mark.parametrize(
    'f_watch',
    [{'sensors': {'air-hallway': RISING, 'air-kitchen': RISING, 'air-living-room': RISING}}],
    indirect=True,
)
def test_a_lost_sensor_starts_no_fire_on_its_old_points(f_watch: FireWatch) -> None:
    step = follow_fire(f_watch, {'air-hallway': None, 'air-kitchen': RISING_NOW, 'air-living-room': None}, moment=NOW)

    assert (step.started, step.rates) == (False, {'air-kitchen': Decimal('1.80')})


@pytest.mark.parametrize(
    ('f_watch', 'reading', 'calm_since'),
    [
        ({'sensors': {'air-living-room': FLAT}, 'fire_since': 0.0}, FLAT_NOW, NOW),
        (
            {'sensors': {'air-living-room': FLAT}, 'fire_since': 0.0, 'calm_since': NOW - 29 * MINUTE},
            FLAT_NOW,
            NOW - 29 * MINUTE,
        ),
        (
            {'sensors': {'air-living-room': RISING}, 'fire_since': 0.0, 'calm_since': NOW - 10 * MINUTE},
            RISING_NOW,
            None,
        ),
        (
            {'sensors': {'air-living-room': FLAT}, 'fire_since': 0.0, 'calm_since': NOW - 10 * MINUTE},
            None,
            None,
        ),
    ],
    ids=['first_calm_minute', 'calm_for_29_minutes', 'rising_again', 'sensor_lost'],
    indirect=['f_watch'],
)
def test_a_fire_counts_its_calm_from_the_first_calm_minute_while_a_rate_is_read(
    f_watch: FireWatch,
    reading: Decimal | None,
    calm_since: float | None,
) -> None:
    step = follow_fire(f_watch, {'air-living-room': reading}, moment=NOW)

    assert (step.ended, step.watch.fire_since, step.watch.calm_since) == (False, 0.0, calm_since)


@pytest.mark.parametrize(
    ('f_watch', 'reading', 'reason'),
    [
        (
            {'sensors': {'air-living-room': FLAT}, 'fire_since': 0.0, 'calm_since': NOW - 30 * MINUTE},
            FLAT_NOW,
            FireEnd.CALM,
        ),
        ({'sensors': {'air-living-room': RISING}, 'fire_since': NOW - 6 * HOUR}, RISING_NOW, FireEnd.TIMEOUT),
    ],
    ids=['calm_for_30_minutes', 'six_hours'],
    indirect=['f_watch'],
)
def test_a_fire_ends_after_half_an_hour_of_calm_or_six_hours(
    f_watch: FireWatch,
    reading: Decimal,
    reason: FireEnd,
) -> None:
    step = follow_fire(f_watch, {'air-living-room': reading}, moment=NOW)

    assert (step.ended, step.reason, step.watch.burning, step.watch.calm_since, len(step.watch.points)) == (
        True,
        reason,
        False,
        None,
        1,
    )
