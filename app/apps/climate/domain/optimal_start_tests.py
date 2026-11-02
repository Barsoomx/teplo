from datetime import timedelta
from decimal import Decimal

import pytest

from apps.climate.domain.learning import HeatUpModel
from apps.climate.domain.optimal_start import OptimalStartSettings, heat_up_rate, optimal_lead

SETTINGS = OptimalStartSettings(lead_cap=timedelta(hours=4), mornings_needed=3)
FIXED_LEAD = timedelta(hours=3)
TARGET = Decimal('22.0')
DEAD_TIME = timedelta(minutes=40)


@pytest.fixture
def f_learned() -> HeatUpModel:
    return HeatUpModel(dead_time=DEAD_TIME, rates={-5: Decimal('0.800'), 5: Decimal('1.600')}, mornings=3)


@pytest.fixture
def f_learning() -> HeatUpModel:
    return HeatUpModel(dead_time=DEAD_TIME, rates={-5: Decimal('0.800')}, mornings=2)


@pytest.fixture
def f_slow_learning() -> HeatUpModel:
    return HeatUpModel(dead_time=timedelta(hours=3, minutes=30), rates={-5: Decimal('0.300')}, mornings=1)


@pytest.fixture
def f_unusable_rates() -> HeatUpModel:
    return HeatUpModel(dead_time=DEAD_TIME, rates={-5: Decimal('0.000')}, mornings=3)


@pytest.mark.parametrize(
    ('outdoor', 'expected'),
    [
        (Decimal('-3.4'), timedelta(hours=2, minutes=40)),
        (Decimal('7.0'), timedelta(hours=1, minutes=40)),
        (Decimal('-12.0'), timedelta(hours=2, minutes=40)),
        (Decimal('1.0'), timedelta(hours=2, minutes=40)),
    ],
    ids=['own_bucket', 'warmer_bucket', 'nearest_learned_bucket', 'colder_of_two_nearest_buckets'],
)
def test_learned_lead_is_dead_time_plus_gap_over_bucket_rate(
    f_learned: HeatUpModel,
    outdoor: Decimal,
    expected: timedelta,
) -> None:
    lead = optimal_lead(
        f_learned,
        air=Decimal('20.4'),
        outdoor=outdoor,
        target=TARGET,
        fixed_lead=FIXED_LEAD,
        settings=SETTINGS,
    )

    assert lead == expected


def test_learned_lead_is_capped(f_learned: HeatUpModel) -> None:
    lead = optimal_lead(
        f_learned,
        air=Decimal('18.0'),
        outdoor=Decimal('-3.4'),
        target=TARGET,
        fixed_lead=FIXED_LEAD,
        settings=SETTINGS,
    )

    assert lead == timedelta(hours=4)


def test_learned_lead_of_warm_room_is_dead_time(f_learned: HeatUpModel) -> None:
    lead = optimal_lead(
        f_learned,
        air=Decimal('22.3'),
        outdoor=Decimal('-3.4'),
        target=TARGET,
        fixed_lead=FIXED_LEAD,
        settings=SETTINGS,
    )

    assert lead == DEAD_TIME


def test_fixed_lead_until_three_learned_mornings(f_learning: HeatUpModel) -> None:
    lead = optimal_lead(
        f_learning,
        air=Decimal('20.4'),
        outdoor=Decimal('-3.4'),
        target=TARGET,
        fixed_lead=FIXED_LEAD,
        settings=SETTINGS,
    )

    assert lead == FIXED_LEAD


@pytest.mark.parametrize(
    ('air', 'outdoor'),
    [(None, Decimal('-3.4')), (Decimal('20.4'), None)],
    ids=['no_air_reading', 'no_outdoor_reading'],
)
def test_fixed_lead_without_air_or_outdoor_reading(
    f_learned: HeatUpModel,
    air: Decimal | None,
    outdoor: Decimal | None,
) -> None:
    lead = optimal_lead(f_learned, air=air, outdoor=outdoor, target=TARGET, fixed_lead=FIXED_LEAD, settings=SETTINGS)

    assert lead == FIXED_LEAD


def test_fixed_lead_without_usable_rate(f_unusable_rates: HeatUpModel) -> None:
    lead = optimal_lead(
        f_unusable_rates,
        air=Decimal('20.4'),
        outdoor=Decimal('-3.4'),
        target=TARGET,
        fixed_lead=FIXED_LEAD,
        settings=SETTINGS,
    )

    assert lead == FIXED_LEAD


def test_lead_is_never_below_dead_time(f_slow_learning: HeatUpModel) -> None:
    lead = optimal_lead(
        f_slow_learning,
        air=Decimal('20.4'),
        outdoor=Decimal('-3.4'),
        target=TARGET,
        fixed_lead=FIXED_LEAD,
        settings=SETTINGS,
    )

    assert lead == timedelta(hours=3, minutes=30)


def test_cap_bounds_fixed_lead_too(f_learning: HeatUpModel) -> None:
    settings = OptimalStartSettings(lead_cap=timedelta(hours=2), mornings_needed=3)

    lead = optimal_lead(
        f_learning,
        air=Decimal('20.4'),
        outdoor=Decimal('-3.4'),
        target=TARGET,
        fixed_lead=FIXED_LEAD,
        settings=settings,
    )

    assert lead == timedelta(hours=2)


def test_heat_up_rate_is_unknown_without_learned_buckets() -> None:
    assert heat_up_rate(HeatUpModel(dead_time=None, rates={}, mornings=0), Decimal('-3.4')) is None
