from datetime import UTC, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from apps.climate.domain.room_intent import IntentReason, RoomIntent
from apps.climate.factories import ClimateSettingsFactory, ComfortPeriodFactory, RoomClimateFactory, ZoneFactory
from apps.climate.repositories.climate_settings import ClimateSettingsRepository
from apps.climate.repositories.room_climate import RoomClimateRepository
from apps.climate.services.control_book import ControlBook
from apps.climate.services.room_intents import RoomIntentReader
from apps.house.factories import FloorFactory, RoomFactory

pytestmark = pytest.mark.django_db

MOSCOW = ZoneInfo('Europe/Moscow')
NIGHT = datetime(2026, 11, 3, 22, 0, tzinfo=UTC)
LOCAL_MIDNIGHT = datetime(2026, 11, 4, 21, 0, tzinfo=UTC)
GUESTS_LEAVE = datetime(2026, 11, 4, 14, 0, tzinfo=MOSCOW)


@pytest.fixture
def book() -> ControlBook:
    return ControlBook()


@pytest.fixture
def f_reader(book: ControlBook) -> RoomIntentReader:
    ground = FloorFactory.create(level=1)
    upstairs = FloorFactory.create(level=2)
    open_space = RoomClimateFactory.create(room=RoomFactory.create(slug='open-space', floor=ground), guest_room=True)
    ComfortPeriodFactory.create_batch(7, zone=None, room_climate=open_space, start=time(6, 30), end=time(23, 30))
    RoomClimateFactory.create(
        room=RoomFactory.create(slug='bedroom', floor=upstairs, position=1),
        comfort_target=Decimal('20.5'),
        backup_zone=ZoneFactory.create(slug='bathroom-2f'),
        backup_live=True,
    )
    RoomClimateFactory.create(
        room=RoomFactory.create(slug='office', floor=upstairs, position=2),
        setback_target=Decimal('19.0'),
        backup_zone=ZoneFactory.create(slug='bathroom-1f'),
        backup_below=Decimal('18.0'),
    )
    ClimateSettingsFactory.create(guests_until=GUESTS_LEAVE)
    return RoomIntentReader(rooms=RoomClimateRepository(), guests=ClimateSettingsRepository(), book=book)


@pytest.fixture
def f_backups(request: pytest.FixtureRequest, book: ControlBook) -> ControlBook:
    for room, active in request.param.items():
        book.remember_backup(room, active)
    return book


def test_read_resolves_every_room_with_the_stored_guests(f_reader: RoomIntentReader) -> None:
    intents = f_reader.read(NIGHT, clock_trusted=True)

    assert ([room.slug for room in intents.rooms], dict(intents.by_room)) == (
        ['open-space', 'bedroom', 'office'],
        {
            'open-space': RoomIntent(
                room='open-space',
                reason=IntentReason.GUESTS,
                target=Decimal('22.0'),
                until=GUESTS_LEAVE,
                forced=True,
            ),
            'bedroom': RoomIntent(
                room='bedroom',
                reason=IntentReason.SETBACK,
                target=Decimal('19.5'),
                until=None,
                forced=False,
            ),
            'office': RoomIntent(
                room='office',
                reason=IntentReason.SETBACK,
                target=Decimal('19.0'),
                until=None,
                forced=False,
            ),
        },
    )


def test_read_takes_the_comfort_windows_in_house_time(f_reader: RoomIntentReader) -> None:
    intents = f_reader.read(LOCAL_MIDNIGHT, clock_trusted=True)

    assert intents.of('open-space') == RoomIntent(
        room='open-space',
        reason=IntentReason.SETBACK,
        target=Decimal('19.5'),
        until=datetime(2026, 11, 5, 6, 30, tzinfo=MOSCOW),
        forced=False,
    )


def test_read_on_an_untrusted_clock_holds_comfort_everywhere(f_reader: RoomIntentReader) -> None:
    intents = f_reader.read(NIGHT, clock_trusted=False)

    assert [(intent.room, intent.reason, intent.target) for intent in intents.by_room.values()] == [
        ('open-space', IntentReason.CLOCK, Decimal('22.0')),
        ('bedroom', IntentReason.CLOCK, Decimal('20.5')),
        ('office', IntentReason.CLOCK, Decimal('22.0')),
    ]


@pytest.mark.parametrize(
    ('f_backups', 'backed_up'),
    [
        ({'bedroom': True, 'office': True}, frozenset({'bathroom-2f'})),
        ({'bedroom': False, 'office': True}, frozenset()),
        ({}, frozenset()),
    ],
    ids=['live_and_active', 'active_with_its_gate_off', 'nothing_active'],
    indirect=['f_backups'],
)
def test_backed_up_names_the_backup_zones_of_live_and_active_backup_heat_only(
    f_reader: RoomIntentReader,
    f_backups: ControlBook,
    backed_up: frozenset[str],
) -> None:
    assert (f_reader.backed_up(f_reader.read(NIGHT, clock_trusted=True)), f_backups.backup_active('bedroom')) == (
        backed_up,
        'bathroom-2f' in backed_up,
    )
