from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.climate.domain.backup_heat import BackupHeat
from apps.climate.domain.events import RoomOverrideCleared
from apps.climate.domain.modes import CommandSource
from apps.climate.domain.room_climate import RoomClimateSettings, RoomOverride
from apps.climate.domain.zone import AirTargets
from apps.climate.factories import RoomClimateFactory
from apps.climate.models import RoomClimate
from apps.climate.stubs import RoomOverrideStoreStub
from apps.climate.usecases.clear_room_override import (
    ClearRoomOverrideInputDTO,
    ClearRoomOverrideOutputDTO,
    ClearRoomOverrideUseCase,
)
from apps.core.domain.events import DomainEvent, EventDispatcher
from apps.core.stubs import TransactionStub
from apps.house.domain.errors import RoomNotFoundError
from apps.house.factories import RoomFactory
from apps.telemetry.models import JournalEntry

NOW = datetime(2026, 11, 4, 13, 45, tzinfo=UTC)
OVERRIDE = RoomOverride(target=Decimal('22.5'), until=NOW + timedelta(hours=1), source=CommandSource.PANEL)
OPEN_SPACE = RoomClimateSettings(
    slug='open-space',
    name='Гостиная, кухня и прихожая',
    air_sensor_slug=None,
    targets=AirTargets(comfort=Decimal('22.0'), night=Decimal('19.5')),
    windows=(),
    backup=BackupHeat(zone_slug=None, below=Decimal('19.0'), hysteresis=Decimal('0.30'), live=False),
    guest_room=True,
    override=None,
    adjustable=True,
)
CLEAR = ClearRoomOverrideInputDTO(room='open-space', now=NOW, source=CommandSource.TELEGRAM)


@pytest.fixture
def f_rooms(request: pytest.FixtureRequest) -> RoomOverrideStoreStub:
    return RoomOverrideStoreStub(rooms=[replace(OPEN_SPACE, override=getattr(request, 'param', None))])


@pytest.fixture
def f_events() -> list[DomainEvent]:
    return []


@pytest.fixture
def f_transaction() -> TransactionStub:
    return TransactionStub(meanwhile=None)


@pytest.fixture
def f_use_case(
    f_rooms: RoomOverrideStoreStub,
    f_transaction: TransactionStub,
    f_events: list[DomainEvent],
) -> ClearRoomOverrideUseCase:
    dispatcher = EventDispatcher()
    dispatcher.register(RoomOverrideCleared, f_events.append)
    return ClearRoomOverrideUseCase(rooms=f_rooms, transaction=f_transaction, dispatcher=dispatcher)


@pytest.fixture
def f_stored_override() -> RoomClimate:
    return RoomClimateFactory.create(
        room=RoomFactory.create(slug='open-space', name='Гостиная, кухня и прихожая'),
        override_target=Decimal('22.5'),
        override_until=NOW + timedelta(hours=1),
        override_source='panel',
    )


@pytest.mark.parametrize('f_rooms', [OVERRIDE], indirect=True)
def test_back_to_the_schedule_clears_the_stored_override_and_journals_it(
    f_use_case: ClearRoomOverrideUseCase,
    f_rooms: RoomOverrideStoreStub,
    f_transaction: TransactionStub,
    f_events: list[DomainEvent],
) -> None:
    output = f_use_case.execute(CLEAR)

    assert (output, f_rooms.saved, f_events, f_transaction.outcomes) == (
        ClearRoomOverrideOutputDTO(cleared=True),
        [('open-space', None)],
        [
            RoomOverrideCleared(
                room='open-space',
                name='Гостиная, кухня и прихожая',
                source=CommandSource.TELEGRAM,
                occurred_at=NOW,
            ),
        ],
        ['committed'],
    )


def test_back_to_the_schedule_without_an_override_writes_and_journals_nothing(
    f_use_case: ClearRoomOverrideUseCase,
    f_rooms: RoomOverrideStoreStub,
    f_events: list[DomainEvent],
) -> None:
    output = f_use_case.execute(CLEAR)

    assert (output, f_rooms.saved, f_events) == (ClearRoomOverrideOutputDTO(cleared=False), [], [])


def test_back_to_the_schedule_of_an_unknown_room_raises(
    f_use_case: ClearRoomOverrideUseCase,
    f_transaction: TransactionStub,
) -> None:
    with pytest.raises(RoomNotFoundError):
        f_use_case.execute(replace(CLEAR, room='attic'))

    assert f_transaction.outcomes == ['rolled_back']


@pytest.mark.django_db
def test_stored_override_is_locked_before_it_is_read_and_its_end_journaled(f_stored_override: RoomClimate) -> None:
    with CaptureQueriesContext(connection) as queries:
        ClearRoomOverrideUseCase.init().execute(CLEAR)

    stored = RoomClimate.objects.get(pk=f_stored_override.pk)
    statements = [query['sql'].split(' ', 1)[0] for query in queries.captured_queries]
    assert (
        statements[:3],
        (stored.override_target, stored.override_until, stored.override_source),
        list(JournalEntry.objects.values_list('kind', 'subject')),
    ) == (['SAVEPOINT', 'UPDATE', 'SELECT'], (None, None, ''), [('room_override_cleared', 'open-space')])
