from decimal import Decimal

import pytest

from apps.power.domain.loads import LoadPhase
from apps.power.domain.phase_samples import PhaseSample, level_at

AMPS = ('9.0', '12.0', '15.0', '18.0')


@pytest.fixture
def f_samples() -> list[PhaseSample]:
    return [
        PhaseSample(
            at=float(second),
            amps={LoadPhase.L1: Decimal(amps), LoadPhase.L2: Decimal('5.0'), LoadPhase.L3: Decimal('3.0')},
        )
        for second, amps in enumerate(AMPS, start=100)
    ]


@pytest.mark.parametrize(
    ('at', 'expected'),
    [(103.0, Decimal('15.0')), (102.5, Decimal('12.0')), (101.0, None)],
    ids=['latest_three', 'three_up_to_the_moment', 'fewer_than_three'],
)
def test_level_is_the_mean_of_the_three_latest_samples_up_to_a_moment(
    f_samples: list[PhaseSample],
    at: float,
    expected: Decimal | None,
) -> None:
    assert level_at(f_samples, LoadPhase.L1, at) == expected
