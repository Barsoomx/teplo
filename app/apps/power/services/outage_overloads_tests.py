from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from apps.power.domain.loads import LoadPhase
from apps.power.domain.outage import OutageCause, OutageRecord, PrecedingOverload
from apps.power.services.outage_overloads import OutageOverloads
from apps.telemetry.factories import JournalEntryFactory
from apps.telemetry.repositories.journal import JournalRepository

pytestmark = pytest.mark.django_db

CUT = datetime(2026, 10, 31, 17, 24, 40, tzinfo=UTC)
OUTAGE = OutageRecord(
    id=7,
    began_at=CUT,
    ended_at=CUT + timedelta(minutes=9),
    cause=OutageCause.UNMARKED,
    cause_marked_at=None,
)


@pytest.fixture
def f_overloads() -> OutageOverloads:
    return OutageOverloads(journal=JournalRepository())


@pytest.fixture
def overload_before_the_cut(request: pytest.FixtureRequest) -> None:
    JournalEntryFactory.create(
        kind='phase_overload',
        subject='l2',
        payload={'phase': 'l2', 'amps': '31.3'},
        created_at=CUT - request.param,
    )


@pytest.mark.usefixtures('overload_before_the_cut')
@pytest.mark.parametrize(
    ('overload_before_the_cut', 'expected'),
    [
        (timedelta(0), PrecedingOverload(phase=LoadPhase.L2, amps=Decimal('31.3'))),
        (timedelta(minutes=2), PrecedingOverload(phase=LoadPhase.L2, amps=Decimal('31.3'))),
        (timedelta(minutes=2, seconds=1), None),
    ],
    ids=['as_the_power_went', 'two_minutes_before', 'earlier'],
    indirect=['overload_before_the_cut'],
)
def test_outage_notes_an_overload_journaled_within_two_minutes_before_it_began(
    f_overloads: OutageOverloads,
    expected: PrecedingOverload | None,
) -> None:
    assert f_overloads.noted(OUTAGE).overload == expected


def test_outage_without_an_overload_in_the_journal_notes_none(f_overloads: OutageOverloads) -> None:
    assert f_overloads.noted(OUTAGE) == OUTAGE


@pytest.fixture
def overloads_measured_and_estimated_before_the_cut(request: pytest.FixtureRequest) -> None:
    JournalEntryFactory.create(
        kind='phase_overload',
        subject='l3',
        payload={'phase': 'l3', 'amps': '26.0', 'estimated': False},
        created_at=CUT - timedelta(seconds=90),
    )
    JournalEntryFactory.create(
        kind='phase_overload',
        subject='l2',
        payload={'phase': 'l2', 'amps': '24.6', 'estimated': True},
        created_at=CUT - request.param,
    )


@pytest.mark.usefixtures('overloads_measured_and_estimated_before_the_cut')
@pytest.mark.parametrize(
    'overloads_measured_and_estimated_before_the_cut',
    [timedelta(seconds=30), timedelta(minutes=2)],
    ids=['estimate_after_the_measured_overload', 'estimate_before_the_measured_overload'],
    indirect=True,
)
def test_outage_puts_forward_a_measured_overload_and_never_one_by_the_estimate(f_overloads: OutageOverloads) -> None:
    assert f_overloads.noted(OUTAGE).overload == PrecedingOverload(phase=LoadPhase.L3, amps=Decimal('26.0'))


@pytest.fixture
def overload_by_the_estimate_before_the_cut() -> None:
    JournalEntryFactory.create(
        kind='phase_overload',
        subject='l2',
        payload={'phase': 'l2', 'amps': '24.6', 'estimated': True},
        created_at=CUT - timedelta(seconds=30),
    )


@pytest.mark.usefixtures('overload_by_the_estimate_before_the_cut')
def test_outage_after_an_overload_only_by_the_estimate_notes_none(f_overloads: OutageOverloads) -> None:
    assert f_overloads.noted(OUTAGE).overload is None
