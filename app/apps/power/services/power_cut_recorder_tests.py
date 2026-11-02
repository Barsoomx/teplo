import contextlib
from datetime import UTC, datetime, timedelta

import pytest
from django.db import OperationalError

from apps.power.domain.outage import OutageCause, OutageRecord, PowerCut
from apps.power.models import Outage
from apps.power.repositories.outage import OutageRepository
from apps.power.services.power_cut_recorder import PowerCutRecorder
from apps.power.stubs import OutageJournalStub
from apps.telemetry.models import JournalEntry
from apps.telemetry.repositories.journal import JournalRepository

pytestmark = pytest.mark.django_db

BEGAN_AT = datetime(2026, 10, 31, 17, 24, tzinfo=UTC)
ENDED_AT = BEGAN_AT + timedelta(minutes=9)
NOW = ENDED_AT + timedelta(minutes=6)
POWER_CUT = PowerCut(began_at=BEGAN_AT, ended_at=ENDED_AT, noticed_at=NOW)


@pytest.fixture
def f_recorder() -> PowerCutRecorder:
    return PowerCutRecorder(outages=OutageRepository(), journal=JournalRepository())


@pytest.fixture
def f_locked_journal() -> OutageJournalStub:
    return OutageJournalStub(errors=(OperationalError('database is locked'),))


@pytest.fixture
def f_locked_journal_recorder(f_locked_journal: OutageJournalStub) -> PowerCutRecorder:
    return PowerCutRecorder(outages=OutageRepository(), journal=f_locked_journal)


@pytest.fixture
def f_retrying_recorder(f_locked_journal_recorder: PowerCutRecorder) -> PowerCutRecorder:
    with contextlib.suppress(OperationalError):
        f_locked_journal_recorder.record(POWER_CUT)
    return f_locked_journal_recorder


def test_power_cut_is_stored_unmarked_with_its_journal_entry(f_recorder: PowerCutRecorder) -> None:
    outage = f_recorder.record(POWER_CUT)

    stored = Outage.objects.get()
    entry = JournalEntry.objects.get()
    assert (outage, (entry.kind, entry.subject, entry.payload, entry.created_at)) == (
        OutageRecord(
            id=stored.pk,
            began_at=BEGAN_AT,
            ended_at=ENDED_AT,
            cause=OutageCause.UNMARKED,
            cause_marked_at=None,
        ),
        (
            'power_outage',
            '',
            {'outage_id': stored.pk, 'began_at': '2026-10-31T17:24:00Z', 'ended_at': '2026-10-31T17:33:00Z'},
            NOW,
        ),
    )


def test_failing_journal_entry_rolls_back_the_outage(f_locked_journal_recorder: PowerCutRecorder) -> None:
    with pytest.raises(OperationalError):
        f_locked_journal_recorder.record(POWER_CUT)

    assert Outage.objects.count() == 0


def test_retry_after_a_failing_journal_entry_stores_both(
    f_retrying_recorder: PowerCutRecorder,
    f_locked_journal: OutageJournalStub,
) -> None:
    outage = f_retrying_recorder.record(POWER_CUT)

    assert (list(Outage.objects.values_list('pk', 'began_at', 'ended_at')), f_locked_journal.lines) == (
        [(outage.id, BEGAN_AT, ENDED_AT)],
        [('power_outage', '', {'outage_id': outage.id, 'began_at': BEGAN_AT, 'ended_at': ENDED_AT}, NOW)],
    )
