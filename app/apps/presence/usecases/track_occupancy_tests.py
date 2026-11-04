from datetime import UTC, datetime, timedelta

import pytest
from structlog.testing import capture_logs

from apps.core.domain.events import DomainEvent, EventDispatcher
from apps.devices.domain.errors import SensorReadingError
from apps.devices.domain.readings import PresenceReading
from apps.devices.stubs import PresenceSensorProviderStub, PresenceSensorStub
from apps.presence.domain.events import RoomOccupancyChanged
from apps.presence.domain.occupancy import Occupancy
from apps.presence.domain.rooms import OccupancyRoom
from apps.presence.services.occupancy_book import OccupancyBook
from apps.presence.stubs import OccupancyRoomStoreStub
from apps.presence.usecases.track_occupancy import (
    TrackOccupancyInputDTO,
    TrackOccupancyOutputDTO,
    TrackOccupancyUseCase,
)

NOW = datetime(2026, 11, 5, 9, 0, tzinfo=UTC)
OFFICE = OccupancyRoom(
    slug='office',
    name='Кабинет',
    vacant_after=timedelta(minutes=20),
    follows_occupancy=True,
    sources=('pc-office',),
)
BEDROOM = OccupancyRoom(
    slug='bedroom',
    name='Спальня',
    vacant_after=timedelta(minutes=20),
    follows_occupancy=False,
    sources=('presence-bedroom',),
)
JUST_STARTED = PresenceReading(present=False, seen_ago=None, watched_for=timedelta(seconds=30), read_at=NOW)
SOMEONE_THERE = PresenceReading(present=True, seen_ago=timedelta(0), watched_for=timedelta(seconds=30), read_at=NOW)
NOBODY_FOR_AN_HOUR = PresenceReading(present=False, seen_ago=None, watched_for=timedelta(hours=1), read_at=NOW)


@pytest.fixture
def f_events() -> list[DomainEvent]:
    return []


@pytest.fixture
def f_book(request: pytest.FixtureRequest) -> OccupancyBook:
    book = OccupancyBook()
    book.replace(getattr(request, 'param', {}))
    return book


@pytest.fixture
def sensors(request: pytest.FixtureRequest) -> PresenceSensorProviderStub:
    office, bedroom = getattr(request, 'param', (JUST_STARTED, NOBODY_FOR_AN_HOUR))
    return PresenceSensorProviderStub(
        sensors={
            'pc-office': PresenceSensorStub(reading=office),
            'presence-bedroom': PresenceSensorStub(
                reading=bedroom,
                error=SensorReadingError('presence: the node reported nan') if bedroom is None else None,
            ),
        },
    )


@pytest.fixture
def f_use_case(
    sensors: PresenceSensorProviderStub,
    f_book: OccupancyBook,
    f_events: list[DomainEvent],
) -> TrackOccupancyUseCase:
    dispatcher = EventDispatcher()
    dispatcher.register(RoomOccupancyChanged, f_events.append)
    return TrackOccupancyUseCase(
        rooms=OccupancyRoomStoreStub(rooms=[OFFICE, BEDROOM]),
        sensors=sensors,
        book=f_book,
        dispatcher=dispatcher,
    )


@pytest.fixture
def f_driverless_use_case() -> TrackOccupancyUseCase:
    return TrackOccupancyUseCase(
        rooms=OccupancyRoomStoreStub(rooms=[OFFICE]),
        sensors=PresenceSensorProviderStub(sensors={}),
        book=OccupancyBook(),
        dispatcher=EventDispatcher(),
    )


@pytest.mark.parametrize('sensors', [(JUST_STARTED, None)], indirect=True)
def test_first_run_after_a_start_reads_unknown_and_journals_nothing(
    f_use_case: TrackOccupancyUseCase,
    f_book: OccupancyBook,
    f_events: list[DomainEvent],
) -> None:
    output = f_use_case.execute(TrackOccupancyInputDTO(now=NOW))

    assert (output, f_book.snapshot(), f_events) == (
        TrackOccupancyOutputDTO(occupancy={'office': Occupancy.UNKNOWN, 'bedroom': Occupancy.UNKNOWN}),
        {'office': Occupancy.UNKNOWN, 'bedroom': Occupancy.UNKNOWN},
        [],
    )


@pytest.mark.parametrize('sensors', [(SOMEONE_THERE, NOBODY_FOR_AN_HOUR)], indirect=True)
def test_change_of_occupancy_is_journaled_with_the_previous_one_and_whether_the_room_follows_it(
    f_use_case: TrackOccupancyUseCase,
    f_book: OccupancyBook,
    f_events: list[DomainEvent],
) -> None:
    with capture_logs() as logs:
        f_use_case.execute(TrackOccupancyInputDTO(now=NOW))

    assert (f_book.snapshot(), f_events, [log for log in logs if log['event'] == 'room_occupancy_changed']) == (
        {'office': Occupancy.OCCUPIED, 'bedroom': Occupancy.VACANT},
        [
            RoomOccupancyChanged(
                room='office',
                name='Кабинет',
                occupancy=Occupancy.OCCUPIED,
                previous=Occupancy.UNKNOWN,
                live=True,
                occurred_at=NOW,
            ),
            RoomOccupancyChanged(
                room='bedroom',
                name='Спальня',
                occupancy=Occupancy.VACANT,
                previous=Occupancy.UNKNOWN,
                live=False,
                occurred_at=NOW,
            ),
        ],
        [
            {
                'event': 'room_occupancy_changed',
                'room': 'office',
                'occupancy': 'occupied',
                'previous': 'unknown',
                'live': True,
                'log_level': 'info',
            },
            {
                'event': 'room_occupancy_changed',
                'room': 'bedroom',
                'occupancy': 'vacant',
                'previous': 'unknown',
                'live': False,
                'log_level': 'info',
            },
        ],
    )


@pytest.mark.parametrize(
    ('sensors', 'f_book'),
    [((SOMEONE_THERE, NOBODY_FOR_AN_HOUR), {'office': Occupancy.OCCUPIED, 'bedroom': Occupancy.VACANT})],
    indirect=True,
)
def test_same_occupancy_journals_nothing(f_use_case: TrackOccupancyUseCase, f_events: list[DomainEvent]) -> None:
    f_use_case.execute(TrackOccupancyInputDTO(now=NOW))

    assert f_events == []


@pytest.mark.parametrize(
    ('sensors', 'f_book'),
    [((SOMEONE_THERE, None), {'office': Occupancy.OCCUPIED, 'bedroom': Occupancy.VACANT})],
    indirect=True,
)
def test_failing_source_counts_as_unavailable(f_use_case: TrackOccupancyUseCase, f_events: list[DomainEvent]) -> None:
    f_use_case.execute(TrackOccupancyInputDTO(now=NOW))

    assert f_events == [
        RoomOccupancyChanged(
            room='bedroom',
            name='Спальня',
            occupancy=Occupancy.UNKNOWN,
            previous=Occupancy.VACANT,
            live=False,
            occurred_at=NOW,
        ),
    ]


@pytest.mark.parametrize(
    'f_book',
    [{'office': Occupancy.UNKNOWN, 'bedroom': Occupancy.VACANT, 'attic': Occupancy.OCCUPIED}],
    indirect=True,
)
def test_room_whose_sources_are_gone_drops_out_of_the_book(
    f_use_case: TrackOccupancyUseCase,
    f_book: OccupancyBook,
) -> None:
    f_use_case.execute(TrackOccupancyInputDTO(now=NOW))

    assert f_book.snapshot() == {'office': Occupancy.UNKNOWN, 'bedroom': Occupancy.VACANT}


def test_source_teplo_has_no_driver_for_counts_as_unavailable(
    f_driverless_use_case: TrackOccupancyUseCase,
) -> None:
    assert f_driverless_use_case.execute(TrackOccupancyInputDTO(now=NOW)) == TrackOccupancyOutputDTO(
        occupancy={'office': Occupancy.UNKNOWN},
    )
