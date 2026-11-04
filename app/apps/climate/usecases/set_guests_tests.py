from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.climate.domain.errors import ClockUntrustedError
from apps.climate.domain.events import GuestsCleared, GuestsSet
from apps.climate.domain.modes import CommandSource
from apps.climate.domain.room_intent import GuestsRecord
from apps.climate.factories import ClimateSettingsFactory
from apps.climate.models import ClimateSettings
from apps.climate.stubs import GuestsStoreStub
from apps.climate.usecases.set_guests import SetGuestsInputDTO, SetGuestsOutputDTO, SetGuestsUseCase
from apps.core.domain.errors import SettingsRejectedError
from apps.core.domain.events import DomainEvent, EventDispatcher
from apps.core.domain.settings import SettingProblem
from apps.core.stubs import TransactionStub
from apps.telemetry.models import JournalEntry

MOSCOW = ZoneInfo('Europe/Moscow')
EVENING = datetime(2026, 11, 4, 15, 0, tzinfo=UTC)
LATE_NIGHT = datetime(2026, 11, 4, 0, 30, tzinfo=UTC)
TOMORROW_AT_TWO = datetime(2026, 11, 5, 14, 0, tzinfo=MOSCOW)


@pytest.fixture
def f_guests(request: pytest.FixtureRequest) -> GuestsStoreStub:
    until, end_hour = getattr(request, 'param', (None, 14))
    return GuestsStoreStub(record=GuestsRecord(until=until, end_hour=end_hour))


@pytest.fixture
def f_events() -> list[DomainEvent]:
    return []


@pytest.fixture
def f_transaction() -> TransactionStub:
    return TransactionStub(meanwhile=None)


@pytest.fixture
def f_use_case(
    f_guests: GuestsStoreStub,
    f_transaction: TransactionStub,
    f_events: list[DomainEvent],
) -> SetGuestsUseCase:
    dispatcher = EventDispatcher()
    for event_type in (GuestsSet, GuestsCleared):
        dispatcher.register(event_type, f_events.append)
    return SetGuestsUseCase(guests=f_guests, transaction=f_transaction, dispatcher=dispatcher)


@pytest.fixture
def f_stored_guests() -> ClimateSettings:
    return ClimateSettingsFactory.create(guests_end_hour=12)


@pytest.mark.parametrize(
    ('now', 'nights', 'until'),
    [
        (EVENING, 1, TOMORROW_AT_TWO),
        (EVENING, 3, TOMORROW_AT_TWO + timedelta(days=2)),
        (LATE_NIGHT, 1, datetime(2026, 11, 4, 14, 0, tzinfo=MOSCOW)),
    ],
    ids=['evening_one_night', 'evening_three_nights', 'before_six_ends_today'],
)
def test_guests_stay_until_the_end_hour_after_the_coming_night(
    f_use_case: SetGuestsUseCase,
    f_guests: GuestsStoreStub,
    f_transaction: TransactionStub,
    f_events: list[DomainEvent],
    now: datetime,
    nights: int,
    until: datetime,
) -> None:
    output = f_use_case.execute(
        SetGuestsInputDTO(nights=nights, now=now, clock_trusted=True, source=CommandSource.PANEL),
    )

    assert (output, f_guests.saved, f_events, f_transaction.outcomes) == (
        SetGuestsOutputDTO(until=until),
        [until],
        [GuestsSet(until=until, nights=nights, source=CommandSource.PANEL, occurred_at=now)],
        ['committed'],
    )


@pytest.mark.parametrize('f_guests', [(None, 12)], indirect=True)
def test_guests_leave_at_the_hour_of_the_settings(f_use_case: SetGuestsUseCase) -> None:
    output = f_use_case.execute(
        SetGuestsInputDTO(nights=1, now=EVENING, clock_trusted=True, source=CommandSource.TELEGRAM),
    )

    assert output.until == datetime(2026, 11, 5, 12, 0, tzinfo=MOSCOW)


@pytest.mark.parametrize(
    ('f_guests', 'events'),
    [
        ((TOMORROW_AT_TWO, 14), [GuestsCleared(source=CommandSource.TELEGRAM, occurred_at=EVENING)]),
        ((None, 14), []),
        ((EVENING - timedelta(minutes=1), 14), []),
    ],
    ids=['staying', 'none', 'already_gone'],
    indirect=['f_guests'],
)
def test_no_nights_end_guests_and_journal_it_only_while_they_stay(
    f_use_case: SetGuestsUseCase,
    f_guests: GuestsStoreStub,
    f_events: list[DomainEvent],
    events: list[DomainEvent],
) -> None:
    output = f_use_case.execute(
        SetGuestsInputDTO(nights=0, now=EVENING, clock_trusted=True, source=CommandSource.TELEGRAM),
    )

    assert (output, f_guests.saved, f_events) == (SetGuestsOutputDTO(until=None), [None], events)


@pytest.mark.parametrize('nights', [4, -1])
def test_guests_for_more_than_three_nights_are_refused_before_anything_is_read(
    f_use_case: SetGuestsUseCase,
    f_guests: GuestsStoreStub,
    f_transaction: TransactionStub,
    f_events: list[DomainEvent],
    nights: int,
) -> None:
    with pytest.raises(SettingsRejectedError) as raised:
        f_use_case.execute(
            SetGuestsInputDTO(nights=nights, now=EVENING, clock_trusted=True, source=CommandSource.PANEL),
        )

    assert (raised.value.problems, f_guests.saved, f_events, f_transaction.outcomes) == (
        (SettingProblem(field='nights', code='out_of_range'),),
        [],
        [],
        [],
    )


def test_guests_wait_for_a_synchronised_clock_to_know_their_end(
    f_use_case: SetGuestsUseCase,
    f_guests: GuestsStoreStub,
    f_transaction: TransactionStub,
    f_events: list[DomainEvent],
) -> None:
    with pytest.raises(ClockUntrustedError):
        f_use_case.execute(SetGuestsInputDTO(nights=1, now=LATE_NIGHT, clock_trusted=False, source=CommandSource.PANEL))

    assert (f_guests.saved, f_events, f_transaction.outcomes) == ([], [], [])


@pytest.mark.parametrize('f_guests', [(TOMORROW_AT_TWO, 14)], indirect=True)
def test_guests_end_on_an_untrusted_clock_too(
    f_use_case: SetGuestsUseCase,
    f_guests: GuestsStoreStub,
    f_events: list[DomainEvent],
) -> None:
    output = f_use_case.execute(
        SetGuestsInputDTO(nights=0, now=EVENING, clock_trusted=False, source=CommandSource.PANEL),
    )

    assert (output, f_guests.saved, f_events) == (
        SetGuestsOutputDTO(until=None),
        [None],
        [GuestsCleared(source=CommandSource.PANEL, occurred_at=EVENING)],
    )


@pytest.mark.django_db
def test_stored_guests_are_locked_before_they_are_read_and_journaled_once_set(
    f_stored_guests: ClimateSettings,
) -> None:
    with CaptureQueriesContext(connection) as queries:
        SetGuestsUseCase.init().execute(
            SetGuestsInputDTO(nights=1, now=EVENING, clock_trusted=True, source=CommandSource.PANEL),
        )

    statements = [query['sql'].split(' ', 1)[0] for query in queries.captured_queries]
    assert (
        statements[:3],
        ClimateSettings.objects.get(pk=f_stored_guests.pk).guests_until,
        list(JournalEntry.objects.values_list('kind', 'subject')),
    ) == (['SAVEPOINT', 'UPDATE', 'SELECT'], datetime(2026, 11, 5, 12, 0, tzinfo=MOSCOW), [('guests_set', '')])
