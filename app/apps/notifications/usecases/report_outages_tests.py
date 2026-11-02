from datetime import UTC, datetime, timedelta

import pytest
from django.db import IntegrityError, OperationalError
from structlog.testing import capture_logs

from apps.core.domain.events import DomainEvent, EventDispatcher
from apps.notifications.domain.events import TeploResumed
from apps.notifications.domain.outage import Outage, StartupNote
from apps.notifications.services.alert_book import AlertBook
from apps.notifications.stubs import PowerCutLedgerStub
from apps.notifications.usecases.note_startup import NoteStartupInputDTO, NoteStartupUseCase
from apps.notifications.usecases.report_outages import (
    ReportOutagesInputDTO,
    ReportOutagesOutputDTO,
    ReportOutagesUseCase,
)
from apps.power.domain.outage import PowerCut
from apps.power.models import Outage as OutageModel
from apps.power.repositories.outage import OutageRepository
from apps.power.services.power_cut_recorder import PowerCutRecorder
from apps.power.stubs import OutageJournalStub
from apps.telemetry.domain.starts import BootOpening, TeploStart
from apps.telemetry.factories import JournalEntryFactory, SampleFactory
from apps.telemetry.models import JournalEntry
from apps.telemetry.usecases.journal_startup import (
    JournalStartupInputDTO,
    JournalStartupOutputDTO,
    JournalStartupUseCase,
)

pytestmark = pytest.mark.django_db

BOOT = '5d1c2a7e-0b4f-4c8a-9e3d-6f2b1a0c9d8e'
EARLIER_BOOT = '0b7d3c1e-9a2f-4e6b-8c5d-3f1a2b4c6d8e'
NOW = datetime(2026, 10, 31, 17, 40, tzinfo=UTC)
NOTICED = 1000.0
CLOCK_STEP = timedelta(hours=3)
RESTART = Outage(began_at=NOW - timedelta(minutes=16), ended_at=NOW, power_cut=False)
POWER_CUT = Outage(began_at=NOW - timedelta(minutes=2), ended_at=NOW, power_cut=True)
RESTART_NOTE = StartupNote(
    last_activity=NOW - timedelta(minutes=16),
    monotonic=NOTICED,
    router_uptime=timedelta(days=3),
    boot=BootOpening(
        start=TeploStart(boot_id=BOOT, last_activity=NOW - timedelta(minutes=16), router_uptime=timedelta(days=3)),
        previous_boot_id=None,
    ),
)
POWER_CUT_NOTE = StartupNote(
    last_activity=NOW - timedelta(minutes=2),
    monotonic=NOTICED,
    router_uptime=timedelta(minutes=1),
    boot=BootOpening(
        start=TeploStart(boot_id=BOOT, last_activity=NOW - timedelta(minutes=2), router_uptime=timedelta(minutes=1)),
        previous_boot_id=EARLIER_BOOT,
    ),
)
RESTART_IN_A_CUT_BOOT_NOTE = StartupNote(
    last_activity=NOW - timedelta(minutes=6),
    monotonic=NOTICED,
    router_uptime=timedelta(minutes=31),
    boot=BootOpening(
        start=TeploStart(boot_id=BOOT, last_activity=NOW - timedelta(minutes=40), router_uptime=timedelta(minutes=1)),
        previous_boot_id=EARLIER_BOOT,
    ),
)

EARLIER_STARTED_AT = datetime(2026, 10, 29, 8, 0, tzinfo=UTC)
CUT_AT = datetime(2026, 10, 31, 17, 24, tzinfo=UTC)
CAME_UP_AT = CUT_AT + timedelta(minutes=9)
OPENED_AT = CAME_UP_AT + timedelta(minutes=1)
CLOCK_BEHIND = timedelta(hours=5)
STARTED = 700.0
CUT = Outage(began_at=CUT_AT, ended_at=OPENED_AT, power_cut=True)


def _input(after: timedelta, *, clock_trusted: bool = True) -> ReportOutagesInputDTO:
    return ReportOutagesInputDTO(
        now=NOW + after,
        monotonic=NOTICED + after.total_seconds(),
        clock_trusted=clock_trusted,
    )


def _use_case(
    note: StartupNote,
    ledger: PowerCutLedgerStub,
    book: AlertBook,
    dispatcher: EventDispatcher,
) -> ReportOutagesUseCase:
    book.note_startup(note)
    return ReportOutagesUseCase(book=book, power_cuts=ledger, dispatcher=dispatcher)


def _start(at: datetime, boot_id: str | None, router_uptime: timedelta) -> JournalStartupOutputDTO:
    return JournalStartupUseCase.init().execute(
        JournalStartupInputDTO(now=at, boot_id=boot_id, router_uptime=router_uptime),
    )


def _start_before_the_cut() -> None:
    _start(EARLIER_STARTED_AT, EARLIER_BOOT, timedelta(days=2))
    SampleFactory.create(recorded_at=CUT_AT)


def _note_start(book: AlertBook, at: datetime, boot_id: str | None, router_uptime: timedelta) -> None:
    started = _start(at, boot_id, router_uptime)
    NoteStartupUseCase(book=book).execute(
        NoteStartupInputDTO(
            last_activity=started.last_activity,
            monotonic=STARTED,
            router_uptime=router_uptime,
            boot=started.boot,
        ),
    )


def _report_input(started_at: datetime, reported_at: datetime) -> ReportOutagesInputDTO:
    return ReportOutagesInputDTO(
        now=reported_at,
        monotonic=STARTED + (reported_at - started_at).total_seconds(),
        clock_trusted=True,
    )


def _stored() -> list[tuple[str | None, datetime, datetime]]:
    return list(OutageModel.objects.order_by('pk').values_list('boot_id', 'began_at', 'ended_at'))


def _journaled_outages() -> list[tuple[object, datetime]]:
    return list(JournalEntry.objects.filter(kind='power_outage').order_by('pk').values_list('payload', 'created_at'))


@pytest.fixture
def f_events() -> list[DomainEvent]:
    return []


@pytest.fixture
def dispatcher(f_events: list[DomainEvent]) -> EventDispatcher:
    dispatcher = EventDispatcher()
    dispatcher.register(TeploResumed, f_events.append)
    return dispatcher


@pytest.fixture
def f_book() -> AlertBook:
    return AlertBook()


@pytest.fixture
def f_ledger() -> PowerCutLedgerStub:
    return PowerCutLedgerStub(errors=(), boots_on_record=())


@pytest.fixture
def f_locked_ledger() -> PowerCutLedgerStub:
    return PowerCutLedgerStub(errors=(OperationalError('database is locked'),), boots_on_record=())


@pytest.fixture
def f_restarted_use_case(
    f_ledger: PowerCutLedgerStub,
    f_book: AlertBook,
    dispatcher: EventDispatcher,
) -> ReportOutagesUseCase:
    return _use_case(RESTART_NOTE, f_ledger, f_book, dispatcher)


@pytest.fixture
def f_restarted_held_back_use_case(f_restarted_use_case: ReportOutagesUseCase) -> ReportOutagesUseCase:
    f_restarted_use_case.execute(
        ReportOutagesInputDTO(now=NOW - CLOCK_STEP + timedelta(minutes=6), monotonic=NOTICED + 360, clock_trusted=True),
    )
    return f_restarted_use_case


@pytest.fixture
def f_power_cut_use_case(
    f_ledger: PowerCutLedgerStub,
    f_book: AlertBook,
    dispatcher: EventDispatcher,
) -> ReportOutagesUseCase:
    return _use_case(POWER_CUT_NOTE, f_ledger, f_book, dispatcher)


@pytest.fixture
def f_recorded_power_cut_use_case(f_book: AlertBook, dispatcher: EventDispatcher) -> ReportOutagesUseCase:
    return _use_case(POWER_CUT_NOTE, PowerCutLedgerStub(errors=(), boots_on_record=(BOOT,)), f_book, dispatcher)


@pytest.fixture
def f_locked_use_case(
    f_locked_ledger: PowerCutLedgerStub,
    f_book: AlertBook,
    dispatcher: EventDispatcher,
) -> ReportOutagesUseCase:
    return _use_case(POWER_CUT_NOTE, f_locked_ledger, f_book, dispatcher)


@pytest.fixture
def f_retrying_use_case(f_locked_use_case: ReportOutagesUseCase) -> ReportOutagesUseCase:
    f_locked_use_case.execute(_input(timedelta(minutes=1)))
    return f_locked_use_case


@pytest.fixture
def f_broken_use_case(f_book: AlertBook, dispatcher: EventDispatcher) -> ReportOutagesUseCase:
    ledger = PowerCutLedgerStub(
        errors=(IntegrityError('UNIQUE constraint failed: power_outage.boot_id'),),
        boots_on_record=(),
    )
    return _use_case(POWER_CUT_NOTE, ledger, f_book, dispatcher)


@pytest.fixture
def f_restarted_in_a_cut_boot_use_case(
    f_ledger: PowerCutLedgerStub,
    f_book: AlertBook,
    dispatcher: EventDispatcher,
) -> ReportOutagesUseCase:
    return _use_case(RESTART_IN_A_CUT_BOOT_NOTE, f_ledger, f_book, dispatcher)


@pytest.fixture
def f_recording_use_case(f_book: AlertBook, dispatcher: EventDispatcher) -> ReportOutagesUseCase:
    return ReportOutagesUseCase(book=f_book, power_cuts=PowerCutRecorder.init(), dispatcher=dispatcher)


@pytest.fixture
def f_locked_journal() -> OutageJournalStub:
    return OutageJournalStub(errors=(OperationalError('database is locked'),))


@pytest.fixture
def f_locked_journal_use_case(
    f_locked_journal: OutageJournalStub,
    f_book: AlertBook,
    dispatcher: EventDispatcher,
) -> ReportOutagesUseCase:
    recorder = PowerCutRecorder(outages=OutageRepository(), journal=f_locked_journal)
    return ReportOutagesUseCase(book=f_book, power_cuts=recorder, dispatcher=dispatcher)


@pytest.fixture
def restart_before_ntp(f_book: AlertBook) -> None:
    _start_before_the_cut()
    _start(OPENED_AT - CLOCK_BEHIND, BOOT, timedelta(minutes=1))
    JournalEntryFactory.create(kind='zone_command_issued', created_at=OPENED_AT + timedelta(minutes=4) - CLOCK_BEHIND)
    _note_start(f_book, CAME_UP_AT + timedelta(minutes=17) - CLOCK_BEHIND, BOOT, timedelta(minutes=17))


@pytest.fixture
def restart_between_ntp_and_the_report(f_book: AlertBook) -> None:
    _start_before_the_cut()
    _start(OPENED_AT - CLOCK_BEHIND, BOOT, timedelta(minutes=1))
    JournalEntryFactory.create(kind='zone_command_issued', created_at=CAME_UP_AT + timedelta(minutes=3, seconds=10))
    _note_start(f_book, CAME_UP_AT + timedelta(minutes=3, seconds=20), BOOT, timedelta(minutes=3, seconds=20))


@pytest.fixture
def restart_after_the_power_cut_was_recorded(f_book: AlertBook) -> None:
    _start_before_the_cut()
    first_book = AlertBook()
    _note_start(first_book, OPENED_AT, BOOT, timedelta(minutes=1))
    ReportOutagesUseCase(book=first_book, power_cuts=PowerCutRecorder.init(), dispatcher=EventDispatcher()).execute(
        _report_input(OPENED_AT, OPENED_AT + timedelta(minutes=1)),
    )
    JournalEntryFactory.create(kind='zone_command_issued', created_at=OPENED_AT + timedelta(minutes=5))
    _note_start(f_book, OPENED_AT + timedelta(minutes=6), BOOT, timedelta(minutes=7))


@pytest.fixture
def first_start_after_the_upgrade(f_book: AlertBook, request: pytest.FixtureRequest) -> None:
    JournalEntryFactory.create(
        kind='teplo_started',
        subject='',
        payload={'last_activity': '2026-10-29T07:59:00Z'},
        created_at=EARLIER_STARTED_AT,
    )
    SampleFactory.create(recorded_at=CUT_AT)
    _note_start(f_book, CAME_UP_AT + request.param, BOOT, request.param)


@pytest.fixture
def start_without_a_boot_id(f_book: AlertBook) -> None:
    _start_before_the_cut()
    _note_start(f_book, OPENED_AT, None, timedelta(minutes=1))


@pytest.fixture
def power_cut_to_record(f_book: AlertBook) -> None:
    _start_before_the_cut()
    _note_start(f_book, OPENED_AT, BOOT, timedelta(minutes=1))


@pytest.fixture
def f_journal_retrying_use_case(
    f_book: AlertBook,
    f_locked_journal_use_case: ReportOutagesUseCase,
) -> ReportOutagesUseCase:
    _start_before_the_cut()
    _note_start(f_book, OPENED_AT, BOOT, timedelta(minutes=1))
    f_locked_journal_use_case.execute(_report_input(OPENED_AT, OPENED_AT + timedelta(minutes=1)))
    return f_locked_journal_use_case


def test_restart_is_reported_once(
    f_restarted_use_case: ReportOutagesUseCase,
    f_book: AlertBook,
    f_ledger: PowerCutLedgerStub,
    f_events: list[DomainEvent],
) -> None:
    output = f_restarted_use_case.execute(_input(timedelta(minutes=7)))

    assert (output, f_events, f_book.startup(), f_ledger.recorded) == (
        ReportOutagesOutputDTO(reported=(RESTART,)),
        [TeploResumed(outage=RESTART, occurred_at=NOW + timedelta(minutes=7))],
        None,
        [],
    )


def test_report_waits_for_a_trusted_clock(
    f_power_cut_use_case: ReportOutagesUseCase,
    f_book: AlertBook,
    f_ledger: PowerCutLedgerStub,
    f_events: list[DomainEvent],
) -> None:
    output = f_power_cut_use_case.execute(_input(timedelta(minutes=7), clock_trusted=False))

    assert (output, f_events, f_book.startup(), f_ledger.recorded) == (
        ReportOutagesOutputDTO(reported=()),
        [],
        POWER_CUT_NOTE,
        [],
    )


def test_restart_is_held_back_until_the_start_no_longer_precedes_the_last_activity(
    f_restarted_held_back_use_case: ReportOutagesUseCase,
    f_events: list[DomainEvent],
) -> None:
    output = f_restarted_held_back_use_case.execute(_input(timedelta(minutes=7)))

    assert (output, f_events) == (
        ReportOutagesOutputDTO(reported=(RESTART,)),
        [TeploResumed(outage=RESTART, occurred_at=NOW + timedelta(minutes=7))],
    )


def test_power_cut_is_recorded_with_its_boot_and_then_announced(
    f_power_cut_use_case: ReportOutagesUseCase,
    f_book: AlertBook,
    f_ledger: PowerCutLedgerStub,
    f_events: list[DomainEvent],
) -> None:
    output = f_power_cut_use_case.execute(_input(timedelta(minutes=7)))

    assert (output, f_events, f_book.startup(), f_ledger.recorded) == (
        ReportOutagesOutputDTO(reported=(POWER_CUT,)),
        [TeploResumed(outage=POWER_CUT, occurred_at=NOW + timedelta(minutes=7))],
        None,
        [
            PowerCut(
                boot_id=BOOT,
                began_at=NOW - timedelta(minutes=2),
                ended_at=NOW,
                noticed_at=NOW + timedelta(minutes=7),
            ),
        ],
    )


def test_power_cut_of_a_boot_on_record_is_not_announced_again(
    f_recorded_power_cut_use_case: ReportOutagesUseCase,
    f_book: AlertBook,
    f_events: list[DomainEvent],
) -> None:
    output = f_recorded_power_cut_use_case.execute(_input(timedelta(minutes=7)))

    assert (output, f_events, f_book.startup()) == (ReportOutagesOutputDTO(reported=()), [], None)


def test_power_cut_the_database_fails_to_record_waits_for_the_next_report(
    f_locked_use_case: ReportOutagesUseCase,
    f_book: AlertBook,
    f_events: list[DomainEvent],
) -> None:
    with capture_logs() as logs:
        output = f_locked_use_case.execute(_input(timedelta(minutes=7)))

    assert (
        output,
        f_events,
        f_book.startup(),
        [(log['event'], log['log_level'], log['began_at'], log['ended_at'], log['error']) for log in logs],
    ) == (
        ReportOutagesOutputDTO(reported=()),
        [],
        POWER_CUT_NOTE,
        [
            (
                'outage_record_failed',
                'warning',
                '2026-10-31T17:38:00+00:00',
                '2026-10-31T17:40:00+00:00',
                'database is locked',
            ),
        ],
    )


def test_power_cut_is_recorded_and_announced_at_the_report_after_a_failure(
    f_retrying_use_case: ReportOutagesUseCase,
    f_locked_ledger: PowerCutLedgerStub,
    f_events: list[DomainEvent],
) -> None:
    output = f_retrying_use_case.execute(_input(timedelta(minutes=2)))

    assert (output, f_events, [power_cut.noticed_at for power_cut in f_locked_ledger.recorded]) == (
        ReportOutagesOutputDTO(reported=(POWER_CUT,)),
        [TeploResumed(outage=POWER_CUT, occurred_at=NOW + timedelta(minutes=2))],
        [NOW + timedelta(minutes=2)],
    )


def test_unexpected_error_keeps_the_startup_for_the_next_report(
    f_broken_use_case: ReportOutagesUseCase,
    f_book: AlertBook,
) -> None:
    with pytest.raises(IntegrityError):
        f_broken_use_case.execute(_input(timedelta(minutes=7)))

    assert f_book.startup() == POWER_CUT_NOTE


def test_restart_with_its_own_gap_is_announced_after_the_power_cut_of_its_boot(
    f_restarted_in_a_cut_boot_use_case: ReportOutagesUseCase,
    f_events: list[DomainEvent],
) -> None:
    f_restarted_in_a_cut_boot_use_case.execute(_input(timedelta(minutes=1)))

    assert f_events == [
        TeploResumed(
            outage=Outage(began_at=NOW - timedelta(minutes=40), ended_at=NOW - timedelta(minutes=30), power_cut=True),
            occurred_at=NOW + timedelta(minutes=1),
        ),
        TeploResumed(
            outage=Outage(began_at=NOW - timedelta(minutes=6), ended_at=NOW, power_cut=False),
            occurred_at=NOW + timedelta(minutes=1),
        ),
    ]


@pytest.mark.usefixtures('restart_before_ntp')
def test_power_cut_is_recorded_after_a_restart_before_ntp(
    f_recording_use_case: ReportOutagesUseCase,
    f_events: list[DomainEvent],
) -> None:
    reported_at = CAME_UP_AT + timedelta(minutes=30)

    f_recording_use_case.execute(_report_input(CAME_UP_AT + timedelta(minutes=17), reported_at))

    assert (_stored(), _journaled_outages(), f_events) == (
        [(BOOT, CUT_AT, OPENED_AT)],
        [
            (
                {
                    'outage_id': OutageModel.objects.get().pk,
                    'began_at': '2026-10-31T17:24:00Z',
                    'ended_at': '2026-10-31T17:34:00Z',
                },
                reported_at,
            ),
        ],
        [TeploResumed(outage=CUT, occurred_at=reported_at)],
    )


@pytest.mark.usefixtures('restart_between_ntp_and_the_report')
def test_power_cut_is_recorded_after_a_restart_between_ntp_and_the_report(
    f_recording_use_case: ReportOutagesUseCase,
    f_events: list[DomainEvent],
) -> None:
    reported_at = CAME_UP_AT + timedelta(minutes=4)

    f_recording_use_case.execute(_report_input(CAME_UP_AT + timedelta(minutes=3, seconds=20), reported_at))

    assert (_stored(), f_events) == (
        [(BOOT, CUT_AT, OPENED_AT)],
        [TeploResumed(outage=CUT, occurred_at=reported_at)],
    )


@pytest.mark.usefixtures('restart_after_the_power_cut_was_recorded')
def test_restart_in_the_same_boot_neither_records_nor_announces_the_power_cut_again(
    f_recording_use_case: ReportOutagesUseCase,
    f_events: list[DomainEvent],
) -> None:
    output = f_recording_use_case.execute(
        _report_input(OPENED_AT + timedelta(minutes=6), OPENED_AT + timedelta(minutes=7)),
    )

    assert (output, _stored(), len(_journaled_outages()), f_events) == (
        ReportOutagesOutputDTO(reported=()),
        [(BOOT, CUT_AT, OPENED_AT)],
        1,
        [],
    )


@pytest.mark.parametrize(
    ('first_start_after_the_upgrade', 'stored', 'outage'),
    [
        (timedelta(minutes=1), [(BOOT, CUT_AT, OPENED_AT)], CUT),
        (
            timedelta(minutes=12),
            [],
            Outage(began_at=CUT_AT, ended_at=CAME_UP_AT + timedelta(minutes=12), power_cut=False),
        ),
    ],
    ids=['router_up_under_ten_minutes', 'router_up_longer'],
    indirect=['first_start_after_the_upgrade'],
)
@pytest.mark.usefixtures('first_start_after_the_upgrade')
def test_first_start_after_a_start_without_a_boot_id_follows_a_power_cut_by_the_router_uptime(
    f_recording_use_case: ReportOutagesUseCase,
    f_events: list[DomainEvent],
    stored: list[tuple[str | None, datetime, datetime]],
    outage: Outage,
) -> None:
    started_at = outage.ended_at
    reported_at = started_at + timedelta(minutes=1)

    f_recording_use_case.execute(_report_input(started_at, reported_at))

    assert (_stored(), f_events) == (stored, [TeploResumed(outage=outage, occurred_at=reported_at)])


@pytest.mark.usefixtures('start_without_a_boot_id')
def test_start_without_a_boot_id_follows_a_power_cut_by_the_router_uptime(
    f_recording_use_case: ReportOutagesUseCase,
    f_events: list[DomainEvent],
) -> None:
    reported_at = OPENED_AT + timedelta(minutes=1)

    f_recording_use_case.execute(_report_input(OPENED_AT, reported_at))

    assert (_stored(), f_events) == (
        [(None, CUT_AT, OPENED_AT)],
        [TeploResumed(outage=CUT, occurred_at=reported_at)],
    )


@pytest.mark.usefixtures('power_cut_to_record')
def test_failing_journal_entry_keeps_no_outage_and_the_power_cut_waiting(
    f_locked_journal_use_case: ReportOutagesUseCase,
    f_book: AlertBook,
    f_events: list[DomainEvent],
) -> None:
    output = f_locked_journal_use_case.execute(_report_input(OPENED_AT, OPENED_AT + timedelta(minutes=1)))

    assert (output, _stored(), f_events, f_book.startup() is not None) == (
        ReportOutagesOutputDTO(reported=()),
        [],
        [],
        True,
    )


def test_report_after_a_failing_journal_entry_stores_the_outage_with_its_entry(
    f_journal_retrying_use_case: ReportOutagesUseCase,
    f_locked_journal: OutageJournalStub,
    f_events: list[DomainEvent],
) -> None:
    reported_at = OPENED_AT + timedelta(minutes=2)

    f_journal_retrying_use_case.execute(_report_input(OPENED_AT, reported_at))

    outage_id = OutageModel.objects.get().pk
    assert (_stored(), f_locked_journal.lines, f_events) == (
        [(BOOT, CUT_AT, OPENED_AT)],
        [('power_outage', '', {'outage_id': outage_id, 'began_at': CUT_AT, 'ended_at': OPENED_AT}, reported_at)],
        [TeploResumed(outage=CUT, occurred_at=reported_at)],
    )
