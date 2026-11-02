from decimal import Decimal

import pytest

from apps.power.domain.load_state import LoadState, load_state
from apps.power.domain.loads import LoadPhase, ShedReason
from apps.power.domain.shedding import Shed

AT = 1000.0
LIVE_SHED = Shed(
    load='boiler-1f',
    phase=LoadPhase.L2,
    amps=Decimal('6.0'),
    live=True,
    reason=ShedReason.OVERLOAD,
    was_on=True,
    at=AT,
    settled_at=AT + 5,
)
DRY_RUN_SHED = Shed(
    load='boiler-1f',
    phase=LoadPhase.L2,
    amps=Decimal('6.0'),
    live=False,
    reason=ShedReason.OVERLOAD,
    was_on=True,
    at=AT,
    settled_at=AT + 5,
)


@pytest.mark.parametrize(
    ('drawing', 'shed', 'waiting', 'live', 'expected'),
    [
        (True, None, False, False, LoadState.DRAWING),
        (False, None, False, True, LoadState.IDLE),
        (None, None, False, True, LoadState.UNAVAILABLE),
        (True, LIVE_SHED, False, True, LoadState.SHED),
        (True, DRY_RUN_SHED, False, True, LoadState.SHED_DRY_RUN),
        (False, None, True, True, LoadState.WAITING),
        (False, None, True, False, LoadState.WAITING_DRY_RUN),
        (None, LIVE_SHED, True, True, LoadState.SHED),
    ],
    ids=[
        'drawing',
        'idle',
        'unavailable',
        'shed',
        'shed_by_a_dry_run_decision',
        'waiting_to_start',
        'waiting_in_dry_run',
        'shed_before_anything_else',
    ],
)
def test_load_state_names_a_shed_or_a_wait_before_what_the_device_reads(
    drawing: bool | None,
    shed: Shed | None,
    waiting: bool,
    live: bool,
    expected: LoadState,
) -> None:
    assert load_state(drawing=drawing, shed=shed, waiting=waiting, live=live) is expected
