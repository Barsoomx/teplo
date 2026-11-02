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
