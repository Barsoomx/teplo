from decimal import Decimal

import pytest

from apps.power.domain.headroom import PhaseOverrun, overrun
from apps.power.domain.loads import LoadPhase

CEILING = Decimal('20.0')
CURRENTS = {LoadPhase.L1: Decimal('12.0'), LoadPhase.L2: Decimal('15.0'), LoadPhase.L3: Decimal('9.0')}


@pytest.mark.parametrize(
    ('phase', 'amps', 'expected'),
    [
        (LoadPhase.L1, Decimal('8.0'), None),
        (LoadPhase.L2, Decimal('6.0'), PhaseOverrun(phase=LoadPhase.L2, amps=Decimal('21.0'))),
        (LoadPhase.UNKNOWN, Decimal('5.0'), None),
        (LoadPhase.UNKNOWN, Decimal('6.0'), PhaseOverrun(phase=LoadPhase.L2, amps=Decimal('21.0'))),
    ],
    ids=[
        'fits_up_to_the_ceiling',
        'overruns_its_phase',
        'unknown_phase_fits_every_phase',
        'unknown_phase_overruns_the_fullest_phase',
    ],
)
def test_overrun_names_the_phase_a_load_would_push_over_the_ceiling(
    phase: LoadPhase,
    amps: Decimal,
    expected: PhaseOverrun | None,
) -> None:
    assert overrun(phase, amps, CURRENTS, CEILING) == expected
