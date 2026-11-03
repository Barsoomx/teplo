from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from apps.climate.domain.learning import (
    AirSample,
    HeatUpModel,
    Morning,
    MorningEstimate,
    ZoneLearningRecord,
    estimate_morning,
    learn_early_stop_offset,
    learn_morning,
    outdoor_bucket,
)

PREHEAT_START = datetime(2026, 11, 2, 3, 30, tzinfo=ZoneInfo('Europe/Moscow'))
COMFORT_AT = PREHEAT_START + timedelta(hours=3)
TARGET = Decimal('22.0')
INITIAL_OFFSET = Decimal('0.3')


def _morning(*readings: tuple[int, str]) -> Morning:
    return Morning(
        comfort_at=COMFORT_AT,
        target=TARGET,
        outdoor=Decimal('-3.4'),
        samples=tuple(
            AirSample(at=PREHEAT_START + timedelta(minutes=minutes), air=Decimal(air)) for minutes, air in readings
        ),
    )


@pytest.fixture
def f_morning() -> Morning:
    return _morning((0, '20.0'), (20, '19.9'), (40, '20.1'), (100, '20.9'), (160, '21.7'), (180, '21.9'))


@pytest.fixture
def f_morning_reaching_target() -> Morning:
    return _morning((0, '21.0'), (30, '21.1'), (90, '22.0'), (120, '22.3'), (180, '22.4'))


@pytest.fixture
def f_morning_with_readings_after_comfort_start() -> Morning:
    return _morning((0, '20.0'), (40, '20.1'), (180, '21.0'), (200, '22.0'))


@pytest.fixture
def f_first_estimate() -> MorningEstimate:
    return MorningEstimate(dead_time=timedelta(minutes=40), rate=Decimal('0.771'), outdoor_bucket=-5)


@pytest.fixture
def f_learned_once() -> HeatUpModel:
    return HeatUpModel(dead_time=timedelta(minutes=40), rates={-5: Decimal('0.771')}, mornings=1)


@pytest.fixture
def f_learned_offset() -> ZoneLearningRecord:
    return ZoneLearningRecord(
        early_stop_offset=Decimal('0.42'),
        heat_up=HeatUpModel(dead_time=None, rates={}, mornings=0),
    )


def test_morning_dead_time_lasts_until_air_rose_and_rate_runs_to_comfort_start(f_morning: Morning) -> None:
    assert estimate_morning(f_morning) == MorningEstimate(
        dead_time=timedelta(minutes=40),
        rate=Decimal('0.771'),
        outdoor_bucket=-5,
    )


def test_morning_rate_ends_when_air_reaches_target(f_morning_reaching_target: Morning) -> None:
    assert estimate_morning(f_morning_reaching_target) == MorningEstimate(
        dead_time=timedelta(minutes=30),
        rate=Decimal('0.900'),
        outdoor_bucket=-5,
    )


def test_morning_ignores_readings_after_comfort_start(f_morning_with_readings_after_comfort_start: Morning) -> None:
    assert estimate_morning(f_morning_with_readings_after_comfort_start) == MorningEstimate(
        dead_time=timedelta(minutes=40),
        rate=Decimal('0.386'),
        outdoor_bucket=-5,
    )


@pytest.mark.parametrize(
    'readings',
    [
        (),
        ((0, '21.0'), (180, '20.9')),
        ((0, '21.0'), (30, '21.1'), (180, '21.3')),
        ((0, '21.8'), (30, '21.9'), (60, '22.1'), (180, '22.4')),
        ((0, '21.9'), (30, '22.0'), (180, '22.5')),
    ],
    ids=[
        'no_readings',
        'no_rise',
        'rise_of_two_tenths_by_comfort_start',
        'rise_of_two_tenths_to_target',
        'target_reached_when_dead_time_ends',
    ],
)
def test_morning_without_a_rise_over_two_tenths_after_dead_time_teaches_nothing(
    readings: tuple[tuple[int, str], ...],
) -> None:
    assert estimate_morning(_morning(*readings)) is None


@pytest.mark.parametrize(
    ('outdoor', 'expected'),
    [
        (Decimal('-10.0'), -10),
        (Decimal('-5.1'), -10),
        (Decimal('-5.0'), -5),
        (Decimal('-3.4'), -5),
        (Decimal('-0.1'), -5),
        (Decimal('0.0'), 0),
        (Decimal('4.9'), 0),
        (Decimal('5.0'), 5),
        (Decimal('12.3'), 10),
    ],
)
def test_outdoor_bucket_is_lower_bound_of_five_degree_interval(outdoor: Decimal, expected: int) -> None:
    assert outdoor_bucket(outdoor) == expected


def test_first_morning_sets_dead_time_and_bucket_rate(f_first_estimate: MorningEstimate) -> None:
    model = learn_morning(HeatUpModel(dead_time=None, rates={}, mornings=0), f_first_estimate)

    assert model == HeatUpModel(dead_time=timedelta(minutes=40), rates={-5: Decimal('0.771')}, mornings=1)


def test_next_morning_in_bucket_moves_averages_by_three_tenths(f_learned_once: HeatUpModel) -> None:
    estimate = MorningEstimate(dead_time=timedelta(minutes=60), rate=Decimal('0.500'), outdoor_bucket=-5)

    assert learn_morning(f_learned_once, estimate) == HeatUpModel(
        dead_time=timedelta(minutes=46),
        rates={-5: Decimal('0.690')},
        mornings=2,
    )


def test_morning_in_another_bucket_keeps_learned_rates(f_learned_once: HeatUpModel) -> None:
    estimate = MorningEstimate(dead_time=timedelta(minutes=40), rate=Decimal('1.200'), outdoor_bucket=0)

    assert learn_morning(f_learned_once, estimate) == HeatUpModel(
        dead_time=timedelta(minutes=40),
        rates={-5: Decimal('0.771'), 0: Decimal('1.200')},
        mornings=2,
    )


@pytest.mark.parametrize(
    ('offset', 'peak', 'expected'),
    [
        (Decimal('0.30'), Decimal('22.4'), Decimal('0.42')),
        (Decimal('0.42'), Decimal('21.9'), Decimal('0.39')),
        (Decimal('0.30'), Decimal('22.0'), Decimal('0.30')),
        (Decimal('0.30'), Decimal('22.05'), Decimal('0.32')),
        (Decimal('0.05'), Decimal('21.0'), Decimal('0.00')),
        (Decimal('1.40'), Decimal('23.0'), Decimal('1.50')),
    ],
    ids=['overshoot_raises', 'undershoot_lowers', 'peak_on_target_keeps', 'rounds_to_hundredths', 'floor', 'ceiling'],
)
def test_early_stop_offset_moves_by_three_tenths_of_overshoot_within_bounds(
    offset: Decimal,
    peak: Decimal,
    expected: Decimal,
) -> None:
    assert learn_early_stop_offset(offset, used=offset, peak=peak, target=TARGET) == expected


@pytest.mark.parametrize(
    ('offset', 'used', 'peak', 'expected'),
    [
        (Decimal('0.30'), Decimal('0.10'), Decimal('22.3'), Decimal('0.33')),
        (Decimal('0.45'), Decimal('0.10'), Decimal('22.3'), Decimal('0.44')),
        (Decimal('0.60'), Decimal('0.10'), Decimal('22.5'), Decimal('0.60')),
    ],
    ids=['initial_offset_moves_toward_the_room', 'larger_learned_offset_settles', 'learned_offset_matches_the_room'],
)
def test_early_stop_offset_learns_from_the_offset_in_effect_at_the_stop(
    offset: Decimal,
    used: Decimal,
    peak: Decimal,
    expected: Decimal,
) -> None:
    assert learn_early_stop_offset(offset, used=used, peak=peak, target=TARGET) == expected


def test_unlearned_zone_starts_from_initial_early_stop_offset() -> None:
    assert ZoneLearningRecord.unlearned().early_stop_offset_or(INITIAL_OFFSET) == INITIAL_OFFSET


def test_learned_early_stop_offset_replaces_initial(f_learned_offset: ZoneLearningRecord) -> None:
    assert f_learned_offset.early_stop_offset_or(INITIAL_OFFSET) == Decimal('0.42')
