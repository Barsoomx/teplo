from datetime import timedelta
from decimal import Decimal

import pytest

from apps.climate.domain.learning import HeatUpModel
from apps.climate.domain.morning_cap import morning_rate_cap


@pytest.fixture
def f_model(request: pytest.FixtureRequest) -> HeatUpModel:
    return HeatUpModel(dead_time=timedelta(minutes=40), rates=request.param, mornings=len(request.param))


@pytest.mark.parametrize(
    ('f_model', 'cap'),
    [
        ({}, Decimal('1.5')),
        ({-5: Decimal('0.290')}, Decimal('1.5')),
        ({-5: Decimal('0.800')}, Decimal('2.400')),
        ({-10: Decimal('0.600'), 5: Decimal('0.900')}, Decimal('1.800')),
        ({-10: Decimal('0.600'), 0: Decimal('0.700')}, Decimal('1.800')),
    ],
    ids=[
        'nothing_learned_takes_the_floor',
        'slow_learned_rate_takes_the_floor',
        'three_times_the_bucket_rate',
        'nearest_learned_bucket',
        'colder_bucket_on_a_tie',
    ],
    indirect=['f_model'],
)
def test_morning_rate_cap_is_three_times_the_nearest_learned_rate_and_at_least_one_and_a_half(
    f_model: HeatUpModel,
    cap: Decimal,
) -> None:
    assert morning_rate_cap(f_model, -5) == cap
