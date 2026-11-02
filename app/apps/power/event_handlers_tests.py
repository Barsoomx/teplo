from datetime import UTC, datetime, timedelta

import pytest

from apps.notifications.domain.events import TeploResumed
from apps.notifications.domain.outage import Outage as DetectedOutage
from apps.power.event_handlers import record_power_cut
from apps.power.models import Outage
from apps.telemetry.models import JournalEntry

pytestmark = pytest.mark.django_db

BEGAN_AT = datetime(2026, 10, 31, 17, 24, tzinfo=UTC)
ENDED_AT = BEGAN_AT + timedelta(minutes=9)
NOW = ENDED_AT + timedelta(minutes=6)


@pytest.fixture
def f_power_cut() -> TeploResumed:
    return TeploResumed(outage=DetectedOutage(began_at=BEGAN_AT, ended_at=ENDED_AT, power_cut=True), occurred_at=NOW)


@pytest.fixture
def f_restart() -> TeploResumed:
    return TeploResumed(outage=DetectedOutage(began_at=BEGAN_AT, ended_at=ENDED_AT, power_cut=False), occurred_at=NOW)


def test_power_cut_is_recorded_unmarked_and_journaled(f_power_cut: TeploResumed) -> None:
    record_power_cut(f_power_cut)

    outage = Outage.objects.get()
    entry = JournalEntry.objects.get()
    assert (
        (outage.began_at, outage.ended_at, outage.cause, outage.cause_marked_at),
        (entry.kind, entry.subject, entry.payload, entry.created_at),
    ) == (
        (BEGAN_AT, ENDED_AT, 'unmarked', None),
        (
            'power_outage',
            '',
            {'outage_id': outage.pk, 'began_at': '2026-10-31T17:24:00Z', 'ended_at': '2026-10-31T17:33:00Z'},
            NOW,
        ),
    )


def test_teplo_restart_is_not_recorded(f_restart: TeploResumed) -> None:
    record_power_cut(f_restart)

    assert (Outage.objects.count(), JournalEntry.objects.count()) == (0, 0)
