from dataclasses import replace
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.climate.domain.backup_heat import BackupHeat
from apps.climate.domain.errors import RoomNotAdjustableError
from apps.climate.domain.events import RoomOverridden
from apps.climate.domain.modes import CommandSource
from apps.climate.domain.room_climate import RoomClimateSettings, RoomOverride
from apps.climate.domain.room_intent import GuestsRecord, OverrideChange
from apps.climate.domain.zone import AirTargets, ComfortWindow
from apps.climate.factories import ComfortPeriodFactory, RoomClimateFactory, ZoneFactory
from apps.climate.models import RoomClimate
from apps.climate.stubs import GuestsStoreStub, RoomOverrideStoreStub
from apps.climate.usecases.override_room import OverrideRoomInputDTO, OverrideRoomOutputDTO, OverrideRoomUseCase
from apps.core.domain.errors import SettingsRejectedError
from apps.core.domain.events import DomainEvent, EventDispatcher
from apps.core.domain.settings import SettingProblem
from apps.core.stubs import TransactionStub
from apps.devices.domain.kinds import DeviceDriver, DeviceKind
from apps.devices.factories import DeviceFactory
from apps.house.domain.errors import RoomNotFoundError
from apps.house.factories import RoomFactory
from apps.telemetry.models import JournalEntry

MOSCOW = ZoneInfo('Europe/Moscow')
MORNING = datetime(2026, 11, 4, 4, 0, tzinfo=UTC)
NIGHT = datetime(2026, 11, 3, 22, 0, tzinfo=UTC)
OPEN_SPACE = RoomClimateSettings(
    slug='open-space',
    name='Гостиная, кухня и прихожая',
    air_sensor_slug=None,
    targets=AirTargets(comfort=Decimal('22.0'), night=Decimal('19.5')),
    windows=tuple(ComfortWindow(weekday=weekday, start=time(6, 30), end=time(23, 30)) for weekday in range(7)),
    backup=BackupHeat(zone_slug=None, below=Decimal('19.0'), hysteresis=Decimal('0.30'), live=False),
    guest_room=True,
    override=None,
    adjustable=True,
)
BEDROOM = replace(OPEN_SPACE, slug='bedroom', name='Спальня', windows=(), guest_room=False, adjustable=False)
EDIT = OverrideRoomInputDTO(
    room='open-space',
    change=OverrideChange.UP,
    value=None,
    now=MORNING,
    clock_trusted=True,
    source=CommandSource.PANEL,
)


def _overridden(target: str, until: datetime) -> RoomOverride:
    return RoomOverride(target=Decimal(target), until=until, source=CommandSource.PANEL)


@pytest.fixture
def f_rooms(request: pytest.FixtureRequest) -> RoomOverrideStoreStub:
    override = getattr(request, 'param', None)
    return RoomOverrideStoreStub(rooms=[replace(OPEN_SPACE, override=override), BEDROOM])


@pytest.fixture
def f_guests(request: pytest.FixtureRequest) -> GuestsStoreStub:
    return GuestsStoreStub(record=GuestsRecord(until=getattr(request, 'param', None), end_hour=14))


@pytest.fixture
def f_events() -> list[DomainEvent]:
    return []


@pytest.fixture
def f_transaction() -> TransactionStub:
    return TransactionStub(meanwhile=None)


@pytest.fixture
def f_use_case(
    f_rooms: RoomOverrideStoreStub,
    f_guests: GuestsStoreStub,
    f_transaction: TransactionStub,
    f_events: list[DomainEvent],
) -> OverrideRoomUseCase:
    dispatcher = EventDispatcher()
    dispatcher.register(RoomOverridden, f_events.append)
    return OverrideRoomUseCase(rooms=f_rooms, guests=f_guests, transaction=f_transaction, dispatcher=dispatcher)


@pytest.fixture
def f_stored_open_space() -> RoomClimate:
    climate = RoomClimateFactory.create(room=RoomFactory.create(slug='open-space', name='Гостиная, кухня и прихожая'))
    ComfortPeriodFactory.create_batch(7, zone=None, room_climate=climate, start=time(6, 30), end=time(23, 30))
    ZoneFactory.create(
        slug='kitchen',
        room=climate.room,
        air_sensor=DeviceFactory.create(kind=DeviceKind.TEMPERATURE_SENSOR, driver=DeviceDriver.ESPHOME_TEMPERATURE),
    )
    return climate


def test_step_overrides_the_scheduled_target_for_two_hours(
    f_use_case: OverrideRoomUseCase,
    f_rooms: RoomOverrideStoreStub,
    f_transaction: TransactionStub,
    f_events: list[DomainEvent],
) -> None:
    output = f_use_case.execute(EDIT)

    until = MORNING + timedelta(hours=2)
    assert (output, f_rooms.saved, f_events, f_transaction.outcomes) == (
        OverrideRoomOutputDTO(
            room='open-space',
            name='Гостиная, кухня и прихожая',
            target=Decimal('22.5'),
            until=until,
        ),
        [('open-space', _overridden('22.5', until))],
        [
            RoomOverridden(
                room='open-space',
                name='Гостиная, кухня и прихожая',
                target=Decimal('22.5'),
                until=until,
                source=CommandSource.PANEL,
                occurred_at=MORNING,
            ),
        ],
        ['committed'],
    )


@pytest.mark.parametrize('f_rooms', [_overridden('22.5', MORNING + timedelta(hours=1))], indirect=True)
def test_each_press_steps_from_the_override_and_restarts_its_two_hours(
    f_use_case: OverrideRoomUseCase,
    f_rooms: RoomOverrideStoreStub,
) -> None:
    f_use_case.execute(EDIT)

    assert f_rooms.saved == [('open-space', _overridden('23.0', MORNING + timedelta(hours=2)))]


@pytest.mark.parametrize(
    ('f_guests', 'edit', 'target'),
    [
        (None, replace(EDIT, change=OverrideChange.WARMER, now=NIGHT, source=CommandSource.TELEGRAM), '22.0'),
        (None, replace(EDIT, change=OverrideChange.COOLER, now=NIGHT), '18.5'),
        (datetime(2026, 11, 4, 14, 0, tzinfo=MOSCOW), replace(EDIT, change=OverrideChange.DOWN, now=NIGHT), '21.5'),
        (None, replace(EDIT, change=OverrideChange.DOWN, now=NIGHT, clock_trusted=False), '21.5'),
        (None, replace(EDIT, change=OverrideChange.SET, value=Decimal('20.25'), now=NIGHT), '20.3'),
    ],
    ids=[
        'warmer_at_night_reaches_comfort',
        'cooler_at_night_from_the_night_target',
        'down_from_the_comfort_guests_hold',
        'down_from_the_comfort_an_untrusted_clock_holds',
        'set_outright_to_tenths',
    ],
    indirect=['f_guests'],
)
def test_step_starts_from_what_the_room_holds_now(
    f_use_case: OverrideRoomUseCase,
    f_rooms: RoomOverrideStoreStub,
    edit: OverrideRoomInputDTO,
    target: str,
) -> None:
    output = f_use_case.execute(edit)

    assert (output.target, [override for _, override in f_rooms.saved]) == (
        Decimal(target),
        [RoomOverride(target=Decimal(target), until=NIGHT + timedelta(hours=2), source=edit.source)],
    )


@pytest.mark.parametrize(
    ('edit', 'error'),
    [
        (replace(EDIT, room='bedroom'), RoomNotAdjustableError),
        (replace(EDIT, room='attic'), RoomNotFoundError),
    ],
    ids=['room_without_an_air_reading', 'unknown_room'],
)
def test_room_that_takes_no_override_raises_and_nothing_is_written(
    f_use_case: OverrideRoomUseCase,
    f_rooms: RoomOverrideStoreStub,
    f_transaction: TransactionStub,
    f_events: list[DomainEvent],
    edit: OverrideRoomInputDTO,
    error: type[Exception],
) -> None:
    with pytest.raises(error):
        f_use_case.execute(edit)

    assert (f_rooms.saved, f_events, f_transaction.outcomes) == ([], [], ['rolled_back'])


@pytest.mark.parametrize(
    'edit',
    [
        replace(EDIT, change=OverrideChange.SET, value=Decimal('30.5')),
        replace(EDIT, change=OverrideChange.SET, value=Decimal('9.5')),
    ],
    ids=['above_thirty', 'below_ten'],
)
def test_target_outside_10_to_30_is_refused_and_nothing_is_written(
    f_use_case: OverrideRoomUseCase,
    f_rooms: RoomOverrideStoreStub,
    f_events: list[DomainEvent],
    edit: OverrideRoomInputDTO,
) -> None:
    with pytest.raises(SettingsRejectedError) as raised:
        f_use_case.execute(edit)

    assert (raised.value.problems, f_rooms.saved, f_events) == (
        (SettingProblem(field='target', code='out_of_range'),),
        [],
        [],
    )


@pytest.mark.django_db
def test_stored_room_is_locked_before_it_is_read_and_its_override_journaled(
    f_stored_open_space: RoomClimate,
) -> None:
    with CaptureQueriesContext(connection) as queries:
        OverrideRoomUseCase.init().execute(EDIT)

    stored = RoomClimate.objects.get(pk=f_stored_open_space.pk)
    statements = [query['sql'].split(' ', 1)[0] for query in queries.captured_queries]
    assert (
        statements[:3],
        (stored.override_target, stored.override_until, stored.override_source),
        list(JournalEntry.objects.values_list('kind', 'subject')),
    ) == (
        ['SAVEPOINT', 'UPDATE', 'SELECT'],
        (Decimal('22.5'), MORNING + timedelta(hours=2), 'panel'),
        [('room_overridden', 'open-space')],
    )
