import contextlib
from datetime import UTC, datetime, timedelta

import pytest
from django.db import OperationalError

from apps.power.domain.outage import OutageCause, OutageRecord, PowerCut
from apps.power.factories import OutageFactory
from apps.power.models import Outage
from apps.power.repositories.outage import OutageRepository
from apps.power.services.power_cut_recorder import PowerCutRecorder
from apps.power.stubs import OutageJournalStub
from apps.telemetry.models import JournalEntry
from apps.telemetry.repositories.journal import JournalRepository

pytestmark = pytest.mark.django_db

BOOT_ID = '5d1c2a7e-0b4f-4c8a-9e3d-6f2b1a0c9d8e'
BEGAN_AT = datetime(2026, 10, 31, 17, 24, tzinfo=UTC)
ENDED_AT = BEGAN_AT + timedelta(minutes=9)
NOW = ENDED_AT + timedelta(minutes=6)
POWER_CUT = PowerCut(boot_id=BOOT_ID, began_at=BEGAN_AT, ended_at=ENDED_AT, noticed_at=NOW)


@pytest.fixture
def f_recorder() -> PowerCutRecorder:
    return PowerCutRecorder(outages=OutageRepository(), journal=JournalRepository())


@pytest.fixture
def f_outage_of_the_boot() -> Outage:
    return OutageFactory.create(boot_id=BOOT_ID, began_at=BEGAN_AT, ended_at=ENDED_AT)


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


@pytest.fixture
def outage_entered_by_hand() -> None:
    OutageFactory.create(began_at=BEGAN_AT - timedelta(days=1), ended_at=ENDED_AT - timedelta(days=1))


def test_power_cut_is_stored_unmarked_with_its_boot_and_journal_entry(f_recorder: PowerCutRecorder) -> None:
    outage = f_recorder.record(POWER_CUT)

    stored = Outage.objects.get()
    entry = JournalEntry.objects.get()
    assert (outage, stored.boot_id, (entry.kind, entry.subject, entry.payload, entry.created_at)) == (
        OutageRecord(
            id=stored.pk,
            began_at=BEGAN_AT,
            ended_at=ENDED_AT,
            cause=OutageCause.UNMARKED,
            cause_marked_at=None,
        ),
        BOOT_ID,
        (
            'power_outage',
            '',
            {'outage_id': stored.pk, 'began_at': '2026-10-31T17:24:00Z', 'ended_at': '2026-10-31T17:33:00Z'},
            NOW,
        ),
    )


def test_power_cut_of_a_boot_on_record_is_neither_stored_nor_journaled_again(
    f_recorder: PowerCutRecorder,
    f_outage_of_the_boot: Outage,
) -> None:
    outage = f_recorder.record(POWER_CUT)

    assert (outage, list(Outage.objects.values_list('pk', flat=True)), JournalEntry.objects.count()) == (
        None,
        [f_outage_of_the_boot.pk],
        0,
    )


@pytest.mark.usefixtures('outage_entered_by_hand')
def test_power_cut_without_a_boot_id_is_stored_beside_outages_without_one(f_recorder: PowerCutRecorder) -> None:
    f_recorder.record(PowerCut(boot_id=None, began_at=BEGAN_AT, ended_at=ENDED_AT, noticed_at=NOW))

    assert list(Outage.objects.order_by('began_at').values_list('boot_id', 'began_at')) == [
        (None, BEGAN_AT - timedelta(days=1)),
        (None, BEGAN_AT),
    ]


def test_failing_journal_entry_rolls_back_the_outage(f_locked_journal_recorder: PowerCutRecorder) -> None:
    with pytest.raises(OperationalError):
        f_locked_journal_recorder.record(POWER_CUT)

    assert Outage.objects.count() == 0


def test_retry_after_a_failing_journal_entry_stores_both(
    f_retrying_recorder: PowerCutRecorder,
    f_locked_journal: OutageJournalStub,
) -> None:
    outage = f_retrying_recorder.record(POWER_CUT)

    stored = Outage.objects.get()
    assert (outage, stored.boot_id, f_locked_journal.lines) == (
        OutageRecord(
            id=stored.pk,
            began_at=BEGAN_AT,
            ended_at=ENDED_AT,
            cause=OutageCause.UNMARKED,
            cause_marked_at=None,
        ),
        BOOT_ID,
        [('power_outage', '', {'outage_id': stored.pk, 'began_at': BEGAN_AT, 'ended_at': ENDED_AT}, NOW)],
    )
