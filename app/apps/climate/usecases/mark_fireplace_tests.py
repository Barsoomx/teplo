from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.climate.domain.errors import FireplaceNotFoundError
from apps.climate.domain.events import FireplaceMarkCleared, FireplaceMarked
from apps.climate.domain.heat_sources import FireplaceRecord
from apps.climate.domain.modes import CommandSource
from apps.climate.factories import HeatSourceFactory
from apps.climate.models import HeatSource
from apps.climate.stubs import FireplaceStoreStub
from apps.climate.usecases.mark_fireplace import (
    MarkFireplaceInputDTO,
    MarkFireplaceOutputDTO,
    MarkFireplaceUseCase,
)
from apps.core.domain.events import DomainEvent, EventDispatcher
from apps.core.stubs import TransactionStub
from apps.house.factories import RoomFactory
from apps.telemetry.models import JournalEntry

NOW = datetime(2026, 11, 4, 15, 10, tzinfo=UTC)
FOUR_HOURS_ON = NOW + timedelta(hours=4)
FIREPLACE = FireplaceRecord(
    slug='fireplace',
    name='Камин',
    room_slug='open-space',
    room_name='Гостиная, кухня и прихожая',
    live=False,
    lit_until=None,
)
LIT = MarkFireplaceInputDTO(room='open-space', lit=True, now=NOW, source=CommandSource.PANEL)
OUT = MarkFireplaceInputDTO(room=None, lit=False, now=NOW, source=CommandSource.TELEGRAM)


@pytest.fixture
def f_fireplaces(request: pytest.FixtureRequest) -> FireplaceStoreStub:
    return FireplaceStoreStub(fireplaces=[replace(FIREPLACE, lit_until=getattr(request, 'param', None))])


@pytest.fixture
def f_events() -> list[DomainEvent]:
    return []


@pytest.fixture
def f_transaction() -> TransactionStub:
    return TransactionStub(meanwhile=None)


@pytest.fixture
def f_use_case(
    f_fireplaces: FireplaceStoreStub,
    f_transaction: TransactionStub,
    f_events: list[DomainEvent],
) -> MarkFireplaceUseCase:
    dispatcher = EventDispatcher()
    dispatcher.register(FireplaceMarked, f_events.append)
    dispatcher.register(FireplaceMarkCleared, f_events.append)
    return MarkFireplaceUseCase(fireplaces=f_fireplaces, transaction=f_transaction, dispatcher=dispatcher)


@pytest.fixture
def f_stored_fireplace() -> HeatSource:
    return HeatSourceFactory.create(
        slug='fireplace',
        name='Камин',
        room=RoomFactory.create(slug='open-space', name='Гостиная, кухня и прихожая'),
    )


@pytest.mark.parametrize('f_fireplaces', [None, NOW + timedelta(hours=1)], ids=['unmarked', 'marked'], indirect=True)
def test_lit_marks_the_fireplaces_of_the_room_for_four_hours_and_journals_it(
    f_use_case: MarkFireplaceUseCase,
    f_fireplaces: FireplaceStoreStub,
    f_transaction: TransactionStub,
    f_events: list[DomainEvent],
) -> None:
    output = f_use_case.execute(LIT)

    assert (output, f_fireplaces.locked, f_fireplaces.saved, f_events, f_transaction.outcomes) == (
        MarkFireplaceOutputDTO(
            fireplaces=(replace(FIREPLACE, lit_until=FOUR_HOURS_ON),),
            until=FOUR_HOURS_ON,
            changed=True,
        ),
        ['open-space'],
        [('fireplace', FOUR_HOURS_ON)],
        [
            FireplaceMarked(
                fireplace='fireplace',
                name='Камин',
                room='open-space',
                room_name='Гостиная, кухня и прихожая',
                until=FOUR_HOURS_ON,
                source=CommandSource.PANEL,
                occurred_at=NOW,
            ),
        ],
        ['committed'],
    )


@pytest.mark.parametrize('f_fireplaces', [NOW + timedelta(hours=1)], ids=['marked'], indirect=True)
def test_out_clears_a_mark_that_counts_and_journals_it(
    f_use_case: MarkFireplaceUseCase,
    f_fireplaces: FireplaceStoreStub,
    f_events: list[DomainEvent],
) -> None:
    output = f_use_case.execute(OUT)

    assert (output, f_fireplaces.locked, f_fireplaces.saved, f_events) == (
        MarkFireplaceOutputDTO(fireplaces=(FIREPLACE,), until=None, changed=True),
        [None],
        [('fireplace', None)],
        [
            FireplaceMarkCleared(
                fireplace='fireplace',
                name='Камин',
                room='open-space',
                room_name='Гостиная, кухня и прихожая',
                source=CommandSource.TELEGRAM,
                occurred_at=NOW,
            ),
        ],
    )


@pytest.mark.parametrize(
    ('f_fireplaces', 'saved'),
    [
        (None, []),
        (NOW - timedelta(minutes=5), [('fireplace', None)]),
        (NOW + timedelta(hours=9), [('fireplace', None)]),
    ],
    ids=['never_marked', 'mark_ended', 'mark_beyond_four_hours_of_a_clock_set_back'],
    indirect=['f_fireplaces'],
)
def test_out_without_a_mark_that_counts_journals_nothing_and_clears_a_stale_mark(
    f_use_case: MarkFireplaceUseCase,
    f_fireplaces: FireplaceStoreStub,
    f_events: list[DomainEvent],
    saved: list[tuple[str, datetime | None]],
) -> None:
    output = f_use_case.execute(OUT)

    assert (output, f_fireplaces.saved, f_events) == (
        MarkFireplaceOutputDTO(fireplaces=(FIREPLACE,), until=None, changed=False),
        saved,
        [],
    )


def test_mark_of_a_room_without_a_fireplace_raises(
    f_use_case: MarkFireplaceUseCase,
    f_fireplaces: FireplaceStoreStub,
    f_transaction: TransactionStub,
    f_events: list[DomainEvent],
) -> None:
    with pytest.raises(FireplaceNotFoundError):
        f_use_case.execute(replace(LIT, room='office'))

    assert (f_fireplaces.saved, f_events, f_transaction.outcomes) == ([], [], ['rolled_back'])


@pytest.mark.django_db
def test_stored_fireplace_is_locked_before_it_is_read_and_its_mark_journaled(f_stored_fireplace: HeatSource) -> None:
    with CaptureQueriesContext(connection) as queries:
        MarkFireplaceUseCase.init().execute(LIT)

    statements = [query['sql'].split(' ', 1)[0] for query in queries.captured_queries]
    assert (
        statements[:3],
        HeatSource.objects.get(pk=f_stored_fireplace.pk).lit_until,
        list(JournalEntry.objects.values_list('kind', 'subject')),
    ) == (['SAVEPOINT', 'UPDATE', 'SELECT'], FOUR_HOURS_ON, [('fireplace_marked', 'fireplace')])
