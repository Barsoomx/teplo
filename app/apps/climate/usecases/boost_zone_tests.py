import threading
from datetime import UTC, datetime, timedelta

import pytest

from apps.climate.domain.errors import ZoneNotFoundError
from apps.climate.domain.events import ZoneBoosted
from apps.climate.domain.write_guard import WriteReservation, WriteSlot
from apps.climate.factories import ZoneFactory
from apps.climate.models import Zone
from apps.climate.repositories.zone import ZoneRepository
from apps.climate.services.write_gate import KeyedWriteGate
from apps.climate.usecases.boost_zone import BoostZoneInputDTO, BoostZoneOutputDTO, BoostZoneUseCase
from apps.core.domain.events import DomainEvent, EventDispatcher
from apps.core.stubs import MonotonicClockStub

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 10, 29, 6, 0, tzinfo=UTC)
WRITTEN_AT = NOW - timedelta(minutes=5)
SHED_HELD_UNTIL = WRITTEN_AT + timedelta(minutes=30)


@pytest.fixture
def f_zone() -> Zone:
    return ZoneFactory.create(slug='bathroom-1f', last_command_at=WRITTEN_AT)


@pytest.fixture
def f_writes() -> KeyedWriteGate:
    writes = KeyedWriteGate(clock=MonotonicClockStub(now=0.0), key_lock=threading.Lock)
    writes.reserve('bathroom-1f', WRITTEN_AT, stamped_at=None, read_at=0.0)
    return writes


@pytest.fixture
def f_writes_of_a_zone_shed_live(request: pytest.FixtureRequest) -> KeyedWriteGate:
    since_shed: timedelta = request.param
    clock = MonotonicClockStub(now=0.0)
    writes = KeyedWriteGate(clock=clock, key_lock=threading.Lock)
    writes.override('bathroom-1f', WRITTEN_AT)
    clock.advance(since_shed.total_seconds())
    return writes


@pytest.fixture
def f_use_case_of_a_zone_shed_live(f_writes_of_a_zone_shed_live: KeyedWriteGate) -> BoostZoneUseCase:
    return BoostZoneUseCase(zones=ZoneRepository(), writes=f_writes_of_a_zone_shed_live, dispatcher=EventDispatcher())


@pytest.fixture
def f_events() -> list[DomainEvent]:
    return []


@pytest.fixture
def f_use_case(f_events: list[DomainEvent], f_writes: KeyedWriteGate) -> BoostZoneUseCase:
    dispatcher = EventDispatcher()
    dispatcher.register(ZoneBoosted, f_events.append)
    return BoostZoneUseCase(zones=ZoneRepository(), writes=f_writes, dispatcher=dispatcher)


def test_boost_sets_two_hours_and_clears_write_budget(
    f_use_case: BoostZoneUseCase,
    f_zone: Zone,
    f_events: list[DomainEvent],
) -> None:
    output = f_use_case.execute(BoostZoneInputDTO(zone=f_zone.slug, now=NOW))

    zone = Zone.objects.get(pk=f_zone.pk)
    assert (output, zone.boost_until, zone.last_command_at, f_events) == (
        BoostZoneOutputDTO(zone='bathroom-1f', until=NOW + timedelta(hours=2)),
        NOW + timedelta(hours=2),
        None,
        [ZoneBoosted(zone='bathroom-1f', until=NOW + timedelta(hours=2), occurred_at=NOW)],
    )


@pytest.mark.usefixtures('f_zone')
def test_boost_frees_the_zones_write_slot_so_the_boost_goes_out_at_once(
    f_use_case: BoostZoneUseCase,
    f_writes: KeyedWriteGate,
) -> None:
    f_use_case.execute(BoostZoneInputDTO(zone='bathroom-1f', now=NOW))

    assert f_writes.reserve('bathroom-1f', NOW, stamped_at=None, read_at=0.0) == WriteReservation(
        key='bathroom-1f',
        slot=WriteSlot(generation=2, at=NOW, moment=0.0),
        previous=None,
    )


@pytest.mark.parametrize(
    ('f_writes_of_a_zone_shed_live', 'now', 'expected'),
    [
        (timedelta(minutes=5), NOW, (WRITTEN_AT, None)),
        (
            timedelta(minutes=30),
            SHED_HELD_UNTIL,
            (
                None,
                WriteReservation(
                    key='bathroom-1f',
                    slot=WriteSlot(generation=2, at=SHED_HELD_UNTIL, moment=1800.0),
                    previous=None,
                ),
            ),
        ),
    ],
    ids=['five_minutes_after_the_shed', 'thirty_minutes_after_the_shed'],
    indirect=['f_writes_of_a_zone_shed_live'],
)
@pytest.mark.usefixtures('f_zone')
def test_boost_keeps_the_write_budget_a_live_shed_spent_until_thirty_minutes_after_it(
    f_use_case_of_a_zone_shed_live: BoostZoneUseCase,
    f_writes_of_a_zone_shed_live: KeyedWriteGate,
    now: datetime,
    expected: tuple[datetime | None, WriteReservation | None],
) -> None:
    f_use_case_of_a_zone_shed_live.execute(BoostZoneInputDTO(zone='bathroom-1f', now=now))

    assert (
        Zone.objects.get(slug='bathroom-1f').last_command_at,
        f_writes_of_a_zone_shed_live.reserve('bathroom-1f', now, stamped_at=None, read_at=0.0),
    ) == expected


def test_boost_unknown_zone_raises(f_use_case: BoostZoneUseCase, f_events: list[DomainEvent]) -> None:
    with pytest.raises(ZoneNotFoundError):
        f_use_case.execute(BoostZoneInputDTO(zone='attic', now=NOW))

    assert f_events == []


def test_boost_the_database_does_not_store_keeps_the_zones_write_slot(
    f_use_case: BoostZoneUseCase,
    f_writes: KeyedWriteGate,
) -> None:
    with pytest.raises(ZoneNotFoundError):
        f_use_case.execute(BoostZoneInputDTO(zone='bathroom-1f', now=NOW))

    assert f_writes.reserve('bathroom-1f', NOW, stamped_at=None, read_at=0.0) is None
