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
from apps.climate.domain.room_intent import GuestsRecord
from apps.climate.domain.zone import AirTargets
from apps.climate.factories import RoomClimateFactory
from apps.climate.models import RoomClimate
from apps.climate.stubs import GuestsStoreStub, OccupancyStoreStub, RoomOverrideStoreStub
from apps.climate.usecases.clear_room_override import (
    ClearRoomOverrideInputDTO,
    ClearRoomOverrideOutputDTO,
    ClearRoomOverrideUseCase,
)
from apps.core.domain.events import DomainEvent, EventDispatcher
from apps.core.stubs import TransactionStub
from apps.house.domain.errors import RoomNotFoundError
from apps.house.factories import RoomFactory
from apps.presence.domain.occupancy import Occupancy
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
OFFICE = replace(
    OPEN_SPACE,
    slug='office',
    name='Кабинет',
    targets=AirTargets(comfort=Decimal('21.5'), night=Decimal('19.0')),
    guest_room=False,
    override=OVERRIDE,
    follows_occupancy=True,
)
CLEAR = ClearRoomOverrideInputDTO(room='open-space', now=NOW, source=CommandSource.TELEGRAM)
GUESTS_LEAVE = NOW + timedelta(hours=20)


@pytest.fixture
def office(request: pytest.FixtureRequest) -> RoomClimateSettings:
    return getattr(request, 'param', OFFICE)


@pytest.fixture
def f_rooms(request: pytest.FixtureRequest, office: RoomClimateSettings) -> RoomOverrideStoreStub:
    return RoomOverrideStoreStub(rooms=[replace(OPEN_SPACE, override=getattr(request, 'param', None)), office])


@pytest.fixture
def guests(request: pytest.FixtureRequest) -> GuestsStoreStub:
    return GuestsStoreStub(record=GuestsRecord(until=getattr(request, 'param', None), end_hour=14))


@pytest.fixture
def occupancy(request: pytest.FixtureRequest) -> OccupancyStoreStub:
    return OccupancyStoreStub(occupancy=getattr(request, 'param', {}))


@pytest.fixture
def f_events() -> list[DomainEvent]:
    return []


@pytest.fixture
def f_transaction() -> TransactionStub:
    return TransactionStub(meanwhile=None)


@pytest.fixture
def f_use_case(
    f_rooms: RoomOverrideStoreStub,
    guests: GuestsStoreStub,
    occupancy: OccupancyStoreStub,
    f_transaction: TransactionStub,
    f_events: list[DomainEvent],
) -> ClearRoomOverrideUseCase:
    dispatcher = EventDispatcher()
    dispatcher.register(RoomOverrideCleared, f_events.append)
    return ClearRoomOverrideUseCase(
        rooms=f_rooms,
        guests=guests,
        occupancy=occupancy,
        transaction=f_transaction,
        dispatcher=dispatcher,
    )


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


@pytest.mark.parametrize(
    ('office', 'occupancy', 'guests', 'expected'),
    [
        (OFFICE, {'office': Occupancy.VACANT}, None, Occupancy.VACANT),
        (OFFICE, {'office': Occupancy.OCCUPIED}, None, Occupancy.OCCUPIED),
        (replace(OFFICE, follows_occupancy=False), {'office': Occupancy.VACANT}, None, None),
        (replace(OFFICE, has_floor=True), {'office': Occupancy.VACANT}, None, None),
        (OFFICE, {'office': Occupancy.UNKNOWN}, None, None),
        (OFFICE, {}, None, None),
        (replace(OFFICE, guest_room=True), {'office': Occupancy.VACANT}, GUESTS_LEAVE, None),
    ],
    ids=['vacant', 'occupied', 'gate_off', 'room_with_a_floor', 'unknown', 'not_tracked', 'guests_stay'],
    indirect=['office', 'occupancy', 'guests'],
)
def test_back_to_the_schedule_names_the_occupancy_the_room_returns_to(
    f_use_case: ClearRoomOverrideUseCase,
    f_events: list[DomainEvent],
    expected: Occupancy | None,
) -> None:
    f_use_case.execute(replace(CLEAR, room='office'))

    assert f_events == [
        RoomOverrideCleared(
            room='office',
            name='Кабинет',
            source=CommandSource.TELEGRAM,
            occupancy=expected,
            occurred_at=NOW,
        ),
    ]


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
