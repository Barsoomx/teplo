from datetime import UTC, datetime, timedelta

import pytest
from django.db import IntegrityError, OperationalError
from structlog.testing import capture_logs

from apps.core.domain.events import DomainEvent, EventDispatcher
from apps.power.domain.events import OutageRecorded
from apps.power.domain.outage import OutageCause, OutageRecord, PowerCut
from apps.power.models import Outage
from apps.power.repositories.outage import OutageRepository
from apps.power.services.outage_backlog import OutageBacklog
from apps.power.stubs import OutageStoreStub
from apps.power.usecases.record_outages import RecordOutagesInputDTO, RecordOutagesOutputDTO, RecordOutagesUseCase

pytestmark = pytest.mark.django_db

BEGAN_AT = datetime(2026, 10, 31, 17, 24, tzinfo=UTC)
ENDED_AT = BEGAN_AT + timedelta(minutes=9)
NOW = ENDED_AT + timedelta(minutes=6)
EVENING_CUT = PowerCut(began_at=BEGAN_AT, ended_at=ENDED_AT, noticed_at=NOW)
NIGHT_CUT = PowerCut(
    began_at=BEGAN_AT + timedelta(hours=5),
    ended_at=BEGAN_AT + timedelta(hours=5, minutes=2),
    noticed_at=BEGAN_AT + timedelta(hours=5, minutes=4),
)


def _unmarked(outage_id: int, power_cut: PowerCut) -> OutageRecord:
    return OutageRecord(
        id=outage_id,
        began_at=power_cut.began_at,
        ended_at=power_cut.ended_at,
        cause=OutageCause.UNMARKED,
        cause_marked_at=None,
    )


def _recorded(outage_id: int, power_cut: PowerCut) -> OutageRecorded:
    return OutageRecorded(
        outage_id=outage_id,
        began_at=power_cut.began_at,
        ended_at=power_cut.ended_at,
        occurred_at=power_cut.noticed_at,
    )


@pytest.fixture
def f_events() -> list[DomainEvent]:
    return []


@pytest.fixture
def dispatcher(f_events: list[DomainEvent]) -> EventDispatcher:
    dispatcher = EventDispatcher()
    dispatcher.register(OutageRecorded, f_events.append)
    return dispatcher


@pytest.fixture
def f_backlog() -> OutageBacklog:
    return OutageBacklog()


@pytest.fixture
def f_waiting_backlog() -> OutageBacklog:
    backlog = OutageBacklog()
    backlog.put(EVENING_CUT)
    return backlog


@pytest.fixture
def f_use_case(f_backlog: OutageBacklog, dispatcher: EventDispatcher) -> RecordOutagesUseCase:
    return RecordOutagesUseCase(outages=OutageRepository(), backlog=f_backlog, dispatcher=dispatcher)


@pytest.fixture
def locked_store() -> OutageStoreStub:
    return OutageStoreStub(errors=(OperationalError('database is locked'),))


@pytest.fixture
def f_locked_use_case(
    locked_store: OutageStoreStub,
    f_backlog: OutageBacklog,
    dispatcher: EventDispatcher,
) -> RecordOutagesUseCase:
    return RecordOutagesUseCase(outages=locked_store, backlog=f_backlog, dispatcher=dispatcher)


@pytest.fixture
def f_retrying_use_case(f_waiting_backlog: OutageBacklog, dispatcher: EventDispatcher) -> RecordOutagesUseCase:
    return RecordOutagesUseCase(outages=OutageStoreStub(errors=()), backlog=f_waiting_backlog, dispatcher=dispatcher)


@pytest.fixture
def f_still_locked_use_case(
    locked_store: OutageStoreStub,
    f_waiting_backlog: OutageBacklog,
    dispatcher: EventDispatcher,
) -> RecordOutagesUseCase:
    return RecordOutagesUseCase(outages=locked_store, backlog=f_waiting_backlog, dispatcher=dispatcher)


@pytest.fixture
def f_broken_use_case(f_waiting_backlog: OutageBacklog, dispatcher: EventDispatcher) -> RecordOutagesUseCase:
    return RecordOutagesUseCase(
        outages=OutageStoreStub(errors=(IntegrityError('NOT NULL constraint failed: power_outage.began_at'),)),
        backlog=f_waiting_backlog,
        dispatcher=dispatcher,
    )


def test_power_cut_is_recorded_unmarked_and_announced(
    f_use_case: RecordOutagesUseCase,
    f_backlog: OutageBacklog,
    f_events: list[DomainEvent],
) -> None:
    output = f_use_case.execute(RecordOutagesInputDTO(power_cut=EVENING_CUT))

    outage_id = Outage.objects.get().pk
    assert (output, f_backlog.pending(), f_events) == (
        RecordOutagesOutputDTO(recorded=(_unmarked(outage_id, EVENING_CUT),)),
        (),
        [_recorded(outage_id, EVENING_CUT)],
    )


def test_power_cut_the_database_fails_to_record_waits_in_the_backlog(
    f_locked_use_case: RecordOutagesUseCase,
    f_backlog: OutageBacklog,
    f_events: list[DomainEvent],
) -> None:
    with capture_logs() as logs:
        output = f_locked_use_case.execute(RecordOutagesInputDTO(power_cut=EVENING_CUT))

    assert (
        output,
        f_backlog.pending(),
        f_events,
        [(log['event'], log['log_level'], log['began_at'], log['ended_at'], log['error']) for log in logs],
    ) == (
        RecordOutagesOutputDTO(recorded=()),
        (EVENING_CUT,),
        [],
        [
            (
                'outage_record_failed',
                'warning',
                '2026-10-31T17:24:00+00:00',
                '2026-10-31T17:33:00+00:00',
                'database is locked',
            ),
        ],
    )


def test_power_cut_left_in_the_backlog_is_recorded_at_the_next_run(
    f_retrying_use_case: RecordOutagesUseCase,
    f_waiting_backlog: OutageBacklog,
    f_events: list[DomainEvent],
) -> None:
    output = f_retrying_use_case.execute(RecordOutagesInputDTO(power_cut=None))

    assert (output, f_waiting_backlog.pending(), f_events) == (
        RecordOutagesOutputDTO(recorded=(_unmarked(1, EVENING_CUT),)),
        (),
        [_recorded(1, EVENING_CUT)],
    )


def test_power_cut_left_in_the_backlog_is_recorded_before_a_new_one(
    f_retrying_use_case: RecordOutagesUseCase,
    f_events: list[DomainEvent],
) -> None:
    output = f_retrying_use_case.execute(RecordOutagesInputDTO(power_cut=NIGHT_CUT))

    assert (output, f_events) == (
        RecordOutagesOutputDTO(recorded=(_unmarked(1, EVENING_CUT), _unmarked(2, NIGHT_CUT))),
        [_recorded(1, EVENING_CUT), _recorded(2, NIGHT_CUT)],
    )


def test_recording_stops_at_the_first_failure_and_keeps_every_power_cut_in_order(
    f_still_locked_use_case: RecordOutagesUseCase,
    f_waiting_backlog: OutageBacklog,
    f_events: list[DomainEvent],
) -> None:
    output = f_still_locked_use_case.execute(RecordOutagesInputDTO(power_cut=NIGHT_CUT))

    assert (output, f_waiting_backlog.pending(), f_events) == (
        RecordOutagesOutputDTO(recorded=()),
        (EVENING_CUT, NIGHT_CUT),
        [],
    )


def test_power_cuts_stay_in_the_backlog_when_an_unexpected_error_stops_the_recording(
    f_broken_use_case: RecordOutagesUseCase,
    f_waiting_backlog: OutageBacklog,
) -> None:
    with pytest.raises(IntegrityError):
        f_broken_use_case.execute(RecordOutagesInputDTO(power_cut=NIGHT_CUT))

    assert f_waiting_backlog.pending() == (EVENING_CUT, NIGHT_CUT)
