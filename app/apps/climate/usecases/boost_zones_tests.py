import threading
from datetime import UTC, datetime, timedelta

import pytest
from django.db import OperationalError

from apps.climate.domain.errors import ZoneNotFoundError
from apps.climate.domain.events import ZoneBoosted
from apps.climate.domain.ports import ZoneBoostStore
from apps.climate.factories import ZoneFactory
from apps.climate.models import Zone
from apps.climate.repositories.zone import ZoneRepository
from apps.climate.services.write_gate import ZoneWriteGate
from apps.climate.usecases.boost_zones import BoostZonesInputDTO, BoostZonesOutputDTO, BoostZonesUseCase
from apps.core.domain.events import DomainEvent, EventDispatcher
from apps.core.services.database_transaction import DatabaseTransaction
from apps.core.stubs import MonotonicClockStub

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 11, 4, 16, 40, tzinfo=UTC)
WRITTEN_AT = NOW - timedelta(minutes=5)
UNTIL = NOW + timedelta(hours=2)
FLOORS = ('living-room', 'kitchen', 'hallway')
UNBOOSTED = [('hallway', None, WRITTEN_AT), ('kitchen', None, WRITTEN_AT), ('living-room', None, WRITTEN_AT)]


class LockedZoneBoostStoreStub:
    def __init__(self, locked: str) -> None:
        self._locked = locked

    def boost(self, slug: str, until: datetime, *, clear_stamps: bool) -> None:
        if slug == self._locked:
            raise OperationalError('database is locked')

        ZoneRepository.boost(slug, until, clear_stamps=clear_stamps)


def _use_case(zones: ZoneBoostStore, writes: ZoneWriteGate, events: list[DomainEvent]) -> BoostZonesUseCase:
    dispatcher = EventDispatcher()
    dispatcher.register(ZoneBoosted, events.append)
    return BoostZonesUseCase(zones=zones, writes=writes, transaction=DatabaseTransaction(), dispatcher=dispatcher)


def _boosts() -> list[tuple[str, datetime | None, datetime | None]]:
    return sorted(Zone.objects.values_list('slug', 'boost_until', 'last_command_at'))


def _held(writes: ZoneWriteGate) -> list[bool]:
    return [not writes.allows(slug, NOW, stamped_at=None, read_at=0.0) for slug in FLOORS]


@pytest.fixture
def floors() -> list[Zone]:
    return [ZoneFactory.create(slug=slug, last_command_at=WRITTEN_AT) for slug in FLOORS]


@pytest.fixture
def clock() -> MonotonicClockStub:
    return MonotonicClockStub(now=0.0)


@pytest.fixture
def f_writes(clock: MonotonicClockStub) -> ZoneWriteGate:
    writes = ZoneWriteGate(clock=clock, zone_lock=threading.Lock)
    for slug in FLOORS:
        writes.reserve(slug, WRITTEN_AT, stamped_at=None, read_at=0.0)
    return writes


@pytest.fixture
def f_writes_with_the_kitchen_shed_live(clock: MonotonicClockStub) -> ZoneWriteGate:
    writes = ZoneWriteGate(clock=clock, zone_lock=threading.Lock)
    writes.override('kitchen', WRITTEN_AT)
    clock.advance(timedelta(minutes=5).total_seconds())
    return writes


@pytest.fixture
def f_events() -> list[DomainEvent]:
    return []


@pytest.fixture
def f_use_case(f_writes: ZoneWriteGate, f_events: list[DomainEvent]) -> BoostZonesUseCase:
    return _use_case(ZoneRepository(), f_writes, f_events)


@pytest.fixture
def f_locked_use_case(f_writes: ZoneWriteGate, f_events: list[DomainEvent]) -> BoostZonesUseCase:
    return _use_case(LockedZoneBoostStoreStub(locked='kitchen'), f_writes, f_events)


@pytest.fixture
def f_use_case_of_a_room_with_a_floor_shed_live(
    f_writes_with_the_kitchen_shed_live: ZoneWriteGate,
) -> BoostZonesUseCase:
    return _use_case(ZoneRepository(), f_writes_with_the_kitchen_shed_live, [])


@pytest.mark.usefixtures('floors')
def test_boost_of_a_room_boosts_every_floor_for_two_hours_and_frees_their_write_budget(
    f_use_case: BoostZonesUseCase,
    f_writes: ZoneWriteGate,
    f_events: list[DomainEvent],
) -> None:
    output = f_use_case.execute(BoostZonesInputDTO(zones=FLOORS, now=NOW))

    assert (output, _boosts(), _held(f_writes), f_events) == (
        BoostZonesOutputDTO(zones=FLOORS, until=UNTIL),
        [('hallway', UNTIL, None), ('kitchen', UNTIL, None), ('living-room', UNTIL, None)],
        [False, False, False],
        [ZoneBoosted(zone=slug, until=UNTIL, occurred_at=NOW) for slug in FLOORS],
    )


@pytest.mark.usefixtures('floors')
def test_boost_of_a_room_the_database_refuses_for_one_floor_boosts_none_and_keeps_their_write_budget(
    f_locked_use_case: BoostZonesUseCase,
    f_writes: ZoneWriteGate,
    f_events: list[DomainEvent],
) -> None:
    with pytest.raises(OperationalError):
        f_locked_use_case.execute(BoostZonesInputDTO(zones=FLOORS, now=NOW))

    assert (_boosts(), _held(f_writes), f_events) == (UNBOOSTED, [True, True, True], [])


@pytest.mark.usefixtures('floors')
def test_boost_of_a_room_with_a_floor_gone_boosts_none_and_keeps_their_write_budget(
    f_use_case: BoostZonesUseCase,
    f_writes: ZoneWriteGate,
    f_events: list[DomainEvent],
) -> None:
    with pytest.raises(ZoneNotFoundError):
        f_use_case.execute(BoostZonesInputDTO(zones=(*FLOORS, 'attic'), now=NOW))

    assert (_boosts(), _held(f_writes), f_events) == (UNBOOSTED, [True, True, True], [])


@pytest.mark.usefixtures('floors')
def test_boost_of_a_room_keeps_the_write_budget_of_a_floor_shed_live_and_frees_the_others(
    f_use_case_of_a_room_with_a_floor_shed_live: BoostZonesUseCase,
    f_writes_with_the_kitchen_shed_live: ZoneWriteGate,
) -> None:
    f_use_case_of_a_room_with_a_floor_shed_live.execute(BoostZonesInputDTO(zones=FLOORS, now=NOW))

    assert (_boosts(), _held(f_writes_with_the_kitchen_shed_live)) == (
        [('hallway', UNTIL, None), ('kitchen', UNTIL, WRITTEN_AT), ('living-room', UNTIL, None)],
        [False, True, False],
    )
