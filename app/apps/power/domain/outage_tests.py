from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from apps.power.domain.loads import LoadPhase
from apps.power.domain.outage import (
    OutageCause,
    PrecedingOverload,
    outage_answer,
    overload_window,
    preceding_overload,
)

CUT = datetime(2026, 10, 31, 17, 24, 40, tzinfo=UTC)


@pytest.mark.parametrize(
    ('value', 'expected'),
    [
        ('overload', OutageCause.OVERLOAD),
        ('grid', OutageCause.GRID),
        ('unknown', OutageCause.UNKNOWN),
        ('unmarked', None),
        ('meteor', None),
        ('', None),
    ],
)
def test_answer_is_one_of_the_three_causes_the_owner_can_give(value: str, expected: OutageCause | None) -> None:
    assert outage_answer(value) is expected


def test_overload_window_spans_the_two_minutes_before_the_cut() -> None:
    assert overload_window(CUT) == (CUT - timedelta(minutes=2), CUT)


@pytest.mark.parametrize(
    ('payload', 'expected'),
    [
        ({'phase': 'l2', 'amps': '31.27'}, PrecedingOverload(phase=LoadPhase.L2, amps=Decimal('31.27'))),
        ({'phase': 'l3', 'amps': None}, PrecedingOverload(phase=LoadPhase.L3, amps=None)),
        ({'phase': 'l4', 'amps': 'many'}, PrecedingOverload(phase=LoadPhase.UNKNOWN, amps=None)),
        ({'phase': 'l1', 'amps': 'NaN'}, PrecedingOverload(phase=LoadPhase.L1, amps=None)),
        ({}, PrecedingOverload(phase=LoadPhase.UNKNOWN, amps=None)),
        (None, None),
    ],
    ids=['phase_and_current', 'without_a_current', 'garbled', 'not_a_number', 'empty', 'no_overload'],
)
def test_preceding_overload_reads_the_phase_and_current_of_the_overload_entry(
    payload: dict[str, object] | None,
    expected: PrecedingOverload | None,
) -> None:
    assert preceding_overload(payload) == expected
