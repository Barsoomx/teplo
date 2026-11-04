from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.db import OperationalError

from apps.climate.domain.drive import PendingKind, PendingWrite
from apps.climate.domain.errors import ClockUntrustedError, FireplaceNotFoundError, RoomNotAdjustableError
from apps.climate.domain.heat_sources import FireplaceRecord, HeatSourceKind
from apps.climate.domain.modes import CommandSource
from apps.climate.domain.room_intent import IntentReason, OverrideChange, RoomIntent
from apps.climate.domain.room_status import RoomStatus, SourceStatus, ZoneStatus
from apps.climate.domain.zone_intent import ForcedTarget
from apps.climate.usecases.boost_zones import BoostZonesInputDTO, BoostZonesOutputDTO
from apps.climate.usecases.clear_room_override import ClearRoomOverrideInputDTO, ClearRoomOverrideOutputDTO
from apps.climate.usecases.mark_fireplace import MarkFireplaceInputDTO, MarkFireplaceOutputDTO
from apps.climate.usecases.override_room import OverrideRoomInputDTO, OverrideRoomOutputDTO
from apps.climate.usecases.set_guests import SetGuestsInputDTO, SetGuestsOutputDTO
from apps.core.domain.errors import SettingsRejectedError
from apps.core.domain.settings import SettingProblem
from apps.house.domain.errors import RoomNotFoundError
from apps.notifications.domain.alerts import PhaseReading
from apps.notifications.domain.commands import CommandKind, CommandRoom, OwnerCommand
from apps.notifications.services.climate_commands import ClimateCommandRunner
from apps.notifications.stubs import ExecutableStub, PhaseReadoutStub, RoomStatusSourceStub

MOSCOW = ZoneInfo('Europe/Moscow')
NOW = datetime(2026, 11, 4, 16, 40, tzinfo=MOSCOW)
UNTIL = NOW + timedelta(hours=2)
GUESTS_LEAVE = datetime(2026, 11, 5, 14, 0, tzinfo=MOSCOW)
PHASES = (
    PhaseReading(phase='l1', amps=Decimal('12.4'), estimated=False),
    PhaseReading(phase='l2', amps=Decimal('8.42'), estimated=True),
    PhaseReading(phase='l3', amps=None, estimated=False),
)
OPEN_SPACE_ROOM = CommandRoom(slug='open-space', name='Гостиная, кухня и прихожая', level=1, stems=('гостин',))
BATHROOM_ROOM = CommandRoom(slug='bathroom-2f', name='Санузел 2 этаж', level=2, stems=('санузел',))
BEDROOM_ROOM = CommandRoom(slug='bedroom', name='Спальня', level=2, stems=('спальн',))
BATHROOM_1F_ROOM = CommandRoom(slug='bathroom-1f', name='Санузел 1 этаж', level=1, stems=('санузел',))
LIVING_ROOM = ZoneStatus(
    slug='living-room',
    name='Гостиная',
    available=True,
    air=Decimal('21.4'),
    floor_temperature=Decimal('27.5'),
    setpoint=Decimal('28.0'),
    power=True,
    air_path=False,
    floor_target=Decimal('28.0'),
    pending=PendingWrite(kind=PendingKind.HOLDS, at=None),
)
KITCHEN = replace(LIVING_ROOM, slug='kitchen', name='Кухня', air=Decimal('21.9'), floor_temperature=Decimal('26.0'))
HALLWAY = replace(
    LIVING_ROOM,
    slug='hallway',
    name='Прихожая',
    air=Decimal('20.8'),
    floor_temperature=Decimal('24.5'),
    power=False,
    pending=PendingWrite(kind=PendingKind.HELD, at=None),
)
BATHROOM_FLOOR = replace(
    LIVING_ROOM,
    slug='bathroom-2f',
    name='Санузел 2 этаж',
    air=None,
    floor_temperature=Decimal('25.0'),
)
OPEN_SPACE = RoomStatus(
    slug='open-space',
    name='Гостиная, кухня и прихожая',
    air=None,
    intent=RoomIntent(
        room='open-space',
        reason=IntentReason.COMFORT,
        target=Decimal('22.0'),
        until=datetime(2026, 11, 4, 23, 30, tzinfo=MOSCOW),
        forced=(),
    ),
    adjustable=True,
    guest_room=True,
    zones=(LIVING_ROOM, KITCHEN, HALLWAY),
)
BATHROOM = RoomStatus(
    slug='bathroom-2f',
    name='Санузел 2 этаж',
    air=None,
    intent=replace(OPEN_SPACE.intent, room='bathroom-2f'),
    adjustable=False,
    guest_room=False,
    zones=(BATHROOM_FLOOR,),
)
BEDROOM = RoomStatus(
    slug='bedroom',
    name='Спальня',
    air=Decimal('18.8'),
    intent=RoomIntent(room='bedroom', reason=IntentReason.SETBACK, target=Decimal('19.5'), until=None, forced=()),
    adjustable=False,
    guest_room=False,
    zones=(),
)
OFFICE = replace(BEDROOM, slug='office', name='Кабинет', air=None, intent=replace(BEDROOM.intent, room='office'))
HOUSE = [OPEN_SPACE, BATHROOM, BEDROOM, OFFICE]
OFFICE_ROOM = CommandRoom(slug='office', name='Кабинет', level=2, stems=('кабинет', 'офис'))
CONVECTOR = SourceStatus(
    slug='convector-office',
    name='Конвектор: кабинет',
    kind=HeatSourceKind.HEATER,
    room='office',
    device='heater-office',
    priority=1,
    live=False,
    available=True,
    error_code=None,
    error=None,
    on=True,
    setpoint=Decimal(19),
    level='High',
    temperature=Decimal(21),
    target_on=True,
    target_setpoint=Decimal(19),
    commands_live=False,
    held_until=None,
    shed=False,
    pending=PendingWrite(kind=PendingKind.HOLDS, at=None),
)
HEATED_OFFICE = replace(OFFICE, adjustable=True, sources=(CONVECTOR,))
HEATED_OFFICE_AFTER = replace(
    HEATED_OFFICE,
    sources=(
        replace(CONVECTOR, target_setpoint=Decimal(22), pending=PendingWrite(kind=PendingKind.WATCHING, at=None)),
    ),
)
WRITES = {
    'living-room': PendingWrite(kind=PendingKind.NEXT_WRITE, at=NOW),
    'kitchen': PendingWrite(kind=PendingKind.NEXT_WRITE, at=NOW + timedelta(minutes=12)),
    'bathroom-2f': PendingWrite(kind=PendingKind.NEXT_WRITE, at=NOW),
}
HOUSE_AFTER = [
    replace(
        room,
        zones=tuple(
            replace(zone, floor_target=Decimal('30.0'), pending=WRITES.get(zone.slug, zone.pending))
            for zone in room.zones
        ),
    )
    for room in HOUSE
]
GUESTS_HOUSE = [
    replace(
        OPEN_SPACE,
        intent=replace(
            OPEN_SPACE.intent,
            reason=IntentReason.GUESTS,
            until=GUESTS_LEAVE,
            forced=(ForcedTarget(target=Decimal('22.0'), beneath=Decimal('22.0')),),
        ),
    ),
    BATHROOM,
    BEDROOM,
    OFFICE,
]
OPEN_SPACE_FLOORS = (
    'Гостиная: пол 30° в ближайшую минуту\nКухня: пол 30° не раньше 16:52\nПрихожая: пол отключён управлением нагрузкой'
)
HELP = (
    'Команды:\n'
    'гости — гостевые комнаты в комфорте до 14:00 (гости 2 — на две ночи, гости всё — выключить)\n'
    'холодно в <комната>, жарко в <комната> — на 1° теплее или холоднее на 2 часа\n'
    '<комната> 22 — держать 22° 2 часа; <комната> авто — вернуть расписание\n'
    'буст <комната> — тёплый пол на 2 часа\n'
    'топлю камин — камин топится 4 часа; камин погас — снять отметку\n'
    'статус — комнаты, цели и фазы'
)
ROOM_NAMES = 'Гостиная, кухня и прихожая; Санузел 2 этаж; Спальня; Кабинет'
LOCKED = OperationalError('database is locked')
FIREPLACE = FireplaceRecord(
    slug='fireplace',
    name='Камин',
    room_slug='open-space',
    room_name='Гостиная, кухня и прихожая',
    live=False,
    lit_until=None,
)
FIREPLACE_LIT = MarkFireplaceOutputDTO(
    fireplaces=(replace(FIREPLACE, lit_until=NOW + timedelta(hours=4)),),
    until=NOW + timedelta(hours=4),
    changed=True,
)


def _runner(
    guests: ExecutableStub[SetGuestsInputDTO, SetGuestsOutputDTO],
    override: ExecutableStub[OverrideRoomInputDTO, OverrideRoomOutputDTO],
    clear: ExecutableStub[ClearRoomOverrideInputDTO, ClearRoomOverrideOutputDTO],
    boost: ExecutableStub[BoostZonesInputDTO, BoostZonesOutputDTO],
    rooms: RoomStatusSourceStub,
    language: str = 'ru',
    fireplace: ExecutableStub[MarkFireplaceInputDTO, MarkFireplaceOutputDTO] | None = None,
) -> ClimateCommandRunner:
    return ClimateCommandRunner(
        guests=guests,
        override=override,
        clear=clear,
        boost=boost,
        fireplace=ExecutableStub(outcomes=(FIREPLACE_LIT,)) if fireplace is None else fireplace,
        rooms=rooms,
        phases=PhaseReadoutStub(phases=PHASES),
        language=language,
    )


@pytest.fixture
def f_office_override() -> ExecutableStub[OverrideRoomInputDTO, OverrideRoomOutputDTO]:
    return ExecutableStub(
        outcomes=(OverrideRoomOutputDTO(room='office', name='Кабинет', target=Decimal('21.5'), until=UNTIL),),
    )


@pytest.fixture
def f_office_runner(
    f_guests: ExecutableStub[SetGuestsInputDTO, SetGuestsOutputDTO],
    f_office_override: ExecutableStub[OverrideRoomInputDTO, OverrideRoomOutputDTO],
    f_clear: ExecutableStub[ClearRoomOverrideInputDTO, ClearRoomOverrideOutputDTO],
    f_boost: ExecutableStub[BoostZonesInputDTO, BoostZonesOutputDTO],
) -> ClimateCommandRunner:
    rooms = RoomStatusSourceStub(readings=([BEDROOM, HEATED_OFFICE], [BEDROOM, HEATED_OFFICE_AFTER]))
    return _runner(f_guests, f_office_override, f_clear, f_boost, rooms)


@pytest.fixture
def f_heated_office_status_runner(
    f_guests: ExecutableStub[SetGuestsInputDTO, SetGuestsOutputDTO],
    f_override: ExecutableStub[OverrideRoomInputDTO, OverrideRoomOutputDTO],
    f_clear: ExecutableStub[ClearRoomOverrideInputDTO, ClearRoomOverrideOutputDTO],
    f_boost: ExecutableStub[BoostZonesInputDTO, BoostZonesOutputDTO],
) -> ClimateCommandRunner:
    return _runner(f_guests, f_override, f_clear, f_boost, RoomStatusSourceStub(readings=([HEATED_OFFICE],)))


@pytest.fixture
def f_guests() -> ExecutableStub[SetGuestsInputDTO, SetGuestsOutputDTO]:
    return ExecutableStub(outcomes=(SetGuestsOutputDTO(until=GUESTS_LEAVE),))


@pytest.fixture
def f_override() -> ExecutableStub[OverrideRoomInputDTO, OverrideRoomOutputDTO]:
    return ExecutableStub(
        outcomes=(
            OverrideRoomOutputDTO(
                room='open-space',
                name='Гостиная, кухня и прихожая',
                target=Decimal('23.0'),
                until=UNTIL,
            ),
        ),
    )


@pytest.fixture
def f_clear() -> ExecutableStub[ClearRoomOverrideInputDTO, ClearRoomOverrideOutputDTO]:
    return ExecutableStub(outcomes=(ClearRoomOverrideOutputDTO(cleared=True),))


@pytest.fixture
def f_boost() -> ExecutableStub[BoostZonesInputDTO, BoostZonesOutputDTO]:
    return ExecutableStub(outcomes=(BoostZonesOutputDTO(zones=('bathroom-2f',), until=UNTIL),))


@pytest.fixture
def f_rooms() -> RoomStatusSourceStub:
    return RoomStatusSourceStub(readings=(HOUSE, HOUSE_AFTER))


@pytest.fixture
def f_runner(
    f_guests: ExecutableStub[SetGuestsInputDTO, SetGuestsOutputDTO],
    f_override: ExecutableStub[OverrideRoomInputDTO, OverrideRoomOutputDTO],
    f_clear: ExecutableStub[ClearRoomOverrideInputDTO, ClearRoomOverrideOutputDTO],
    f_boost: ExecutableStub[BoostZonesInputDTO, BoostZonesOutputDTO],
    f_rooms: RoomStatusSourceStub,
) -> ClimateCommandRunner:
    return _runner(f_guests, f_override, f_clear, f_boost, f_rooms)


@pytest.fixture
def f_guest_office_runner(
    f_guests: ExecutableStub[SetGuestsInputDTO, SetGuestsOutputDTO],
    f_override: ExecutableStub[OverrideRoomInputDTO, OverrideRoomOutputDTO],
    f_clear: ExecutableStub[ClearRoomOverrideInputDTO, ClearRoomOverrideOutputDTO],
    f_boost: ExecutableStub[BoostZonesInputDTO, BoostZonesOutputDTO],
) -> ClimateCommandRunner:
    rooms = RoomStatusSourceStub(
        readings=([replace(HEATED_OFFICE, guest_room=True)], [replace(HEATED_OFFICE_AFTER, guest_room=True)]),
    )
    return _runner(f_guests, f_override, f_clear, f_boost, rooms)


@pytest.fixture
def f_status_runner(
    f_guests: ExecutableStub[SetGuestsInputDTO, SetGuestsOutputDTO],
    f_override: ExecutableStub[OverrideRoomInputDTO, OverrideRoomOutputDTO],
    f_clear: ExecutableStub[ClearRoomOverrideInputDTO, ClearRoomOverrideOutputDTO],
    f_boost: ExecutableStub[BoostZonesInputDTO, BoostZonesOutputDTO],
) -> ClimateCommandRunner:
    return _runner(f_guests, f_override, f_clear, f_boost, RoomStatusSourceStub(readings=(GUESTS_HOUSE,)))


@pytest.fixture
def f_clockless_guests() -> ExecutableStub[SetGuestsInputDTO, SetGuestsOutputDTO]:
    return ExecutableStub(outcomes=(ClockUntrustedError(),))


@pytest.fixture
def f_clockless_runner(
    f_clockless_guests: ExecutableStub[SetGuestsInputDTO, SetGuestsOutputDTO],
    f_override: ExecutableStub[OverrideRoomInputDTO, OverrideRoomOutputDTO],
    f_clear: ExecutableStub[ClearRoomOverrideInputDTO, ClearRoomOverrideOutputDTO],
    f_boost: ExecutableStub[BoostZonesInputDTO, BoostZonesOutputDTO],
    f_rooms: RoomStatusSourceStub,
) -> ClimateCommandRunner:
    return _runner(f_clockless_guests, f_override, f_clear, f_boost, f_rooms)


@pytest.fixture
def f_unread_after_runner(
    request: pytest.FixtureRequest,
    f_guests: ExecutableStub[SetGuestsInputDTO, SetGuestsOutputDTO],
    f_override: ExecutableStub[OverrideRoomInputDTO, OverrideRoomOutputDTO],
    f_clear: ExecutableStub[ClearRoomOverrideInputDTO, ClearRoomOverrideOutputDTO],
    f_boost: ExecutableStub[BoostZonesInputDTO, BoostZonesOutputDTO],
) -> ClimateCommandRunner:
    return _runner(f_guests, f_override, f_clear, f_boost, RoomStatusSourceStub(readings=request.param))


@pytest.fixture
def f_hostless_runner(
    f_guests: ExecutableStub[SetGuestsInputDTO, SetGuestsOutputDTO],
    f_override: ExecutableStub[OverrideRoomInputDTO, OverrideRoomOutputDTO],
    f_clear: ExecutableStub[ClearRoomOverrideInputDTO, ClearRoomOverrideOutputDTO],
    f_boost: ExecutableStub[BoostZonesInputDTO, BoostZonesOutputDTO],
) -> ClimateCommandRunner:
    rooms = RoomStatusSourceStub(readings=([replace(OPEN_SPACE, guest_room=False), BATHROOM],))
    return _runner(f_guests, f_override, f_clear, f_boost, rooms)


@pytest.fixture
def f_refused_runner(
    request: pytest.FixtureRequest,
    f_guests: ExecutableStub[SetGuestsInputDTO, SetGuestsOutputDTO],
    f_clear: ExecutableStub[ClearRoomOverrideInputDTO, ClearRoomOverrideOutputDTO],
    f_boost: ExecutableStub[BoostZonesInputDTO, BoostZonesOutputDTO],
    f_rooms: RoomStatusSourceStub,
) -> ClimateCommandRunner:
    return _runner(f_guests, ExecutableStub(outcomes=(request.param,)), f_clear, f_boost, f_rooms)


@pytest.fixture
def f_uncleared_runner(
    f_guests: ExecutableStub[SetGuestsInputDTO, SetGuestsOutputDTO],
    f_override: ExecutableStub[OverrideRoomInputDTO, OverrideRoomOutputDTO],
    f_boost: ExecutableStub[BoostZonesInputDTO, BoostZonesOutputDTO],
    f_rooms: RoomStatusSourceStub,
) -> ClimateCommandRunner:
    clear = ExecutableStub[ClearRoomOverrideInputDTO, ClearRoomOverrideOutputDTO](
        outcomes=(ClearRoomOverrideOutputDTO(cleared=False),),
    )
    return _runner(f_guests, f_override, clear, f_boost, f_rooms)


@pytest.fixture
def f_english_runner(
    f_guests: ExecutableStub[SetGuestsInputDTO, SetGuestsOutputDTO],
    f_override: ExecutableStub[OverrideRoomInputDTO, OverrideRoomOutputDTO],
    f_clear: ExecutableStub[ClearRoomOverrideInputDTO, ClearRoomOverrideOutputDTO],
    f_boost: ExecutableStub[BoostZonesInputDTO, BoostZonesOutputDTO],
    f_rooms: RoomStatusSourceStub,
) -> ClimateCommandRunner:
    return _runner(f_guests, f_override, f_clear, f_boost, f_rooms, language='en')


@pytest.fixture
def f_locked_boost() -> ExecutableStub[BoostZonesInputDTO, BoostZonesOutputDTO]:
    return ExecutableStub(outcomes=(LOCKED,))


@pytest.fixture
def f_locked_boost_runner(
    f_guests: ExecutableStub[SetGuestsInputDTO, SetGuestsOutputDTO],
    f_override: ExecutableStub[OverrideRoomInputDTO, OverrideRoomOutputDTO],
    f_clear: ExecutableStub[ClearRoomOverrideInputDTO, ClearRoomOverrideOutputDTO],
    f_locked_boost: ExecutableStub[BoostZonesInputDTO, BoostZonesOutputDTO],
    f_rooms: RoomStatusSourceStub,
) -> ClimateCommandRunner:
    return _runner(f_guests, f_override, f_clear, f_locked_boost, f_rooms)


def test_status_gives_the_guests_each_room_with_its_target_and_floors_and_the_phases(
    f_status_runner: ClimateCommandRunner,
) -> None:
    reply = f_status_runner.run(OwnerCommand(kind=CommandKind.STATUS), NOW, clock_trusted=True)

    assert reply == (
        'Гости до 14:00 завтра: Гостиная, кухня и прихожая в комфорте.\n'
        'Гостиная, кухня и прихожая: цель 22,0°, гости до 14:00 завтра\n'
        'Гостиная: воздух 21,4°, пол 27,5° (на термостате 28°)\n'
        'Кухня: воздух 21,9°, пол 26,0° (на термостате 28°)\n'
        'Прихожая: пол 24,5°, термостат выключен\n'
        'Санузел 2 этаж: цель 22,0°, комфорт до 23:30\n'
        'Санузел 2 этаж: пол 25,0° (на термостате 28°)\n'
        'Спальня: 18,8°, цель 19,5°, ночь\n'
        'Фазы: L1 12,4 А · L2 ≈8,4 А · L3 —'
    )


def test_guests_hold_the_guest_rooms_and_name_what_each_floor_does(
    f_runner: ClimateCommandRunner,
    f_guests: ExecutableStub[SetGuestsInputDTO, SetGuestsOutputDTO],
) -> None:
    reply = f_runner.run(OwnerCommand(kind=CommandKind.GUESTS, nights=1), NOW, clock_trusted=True)

    assert (f_guests.inputs, reply) == (
        [SetGuestsInputDTO(nights=1, now=NOW, clock_trusted=True, source=CommandSource.TELEGRAM)],
        f'Гости до 14:00 завтра: Гостиная, кухня и прихожая в комфорте.\n{OPEN_SPACE_FLOORS}',
    )


def test_guests_name_the_setpoint_the_source_of_a_guest_room_takes(f_guest_office_runner: ClimateCommandRunner) -> None:
    reply = f_guest_office_runner.run(OwnerCommand(kind=CommandKind.GUESTS, nights=1), NOW, clock_trusted=True)

    assert reply == (
        'Гости до 14:00 завтра: Кабинет в комфорте.\nКонвектор: кабинет: 22° — наблюдение, команда не уходит'
    )


@pytest.mark.parametrize('nights', [0, 4], ids=['none', 'more_than_three'])
def test_guests_for_nights_out_of_range_change_nothing(
    f_runner: ClimateCommandRunner,
    f_guests: ExecutableStub[SetGuestsInputDTO, SetGuestsOutputDTO],
    nights: int,
) -> None:
    reply = f_runner.run(OwnerCommand(kind=CommandKind.GUESTS, nights=nights), NOW, clock_trusted=True)

    assert (f_guests.inputs, reply) == ([], 'Гости: от 1 до 3 ночей.')


def test_guests_on_an_untrusted_clock_wait_for_it(
    f_clockless_runner: ClimateCommandRunner,
    f_clockless_guests: ExecutableStub[SetGuestsInputDTO, SetGuestsOutputDTO],
) -> None:
    reply = f_clockless_runner.run(OwnerCommand(kind=CommandKind.GUESTS, nights=1), NOW, clock_trusted=False)

    assert (f_clockless_guests.inputs, reply) == (
        [SetGuestsInputDTO(nights=1, now=NOW, clock_trusted=False, source=CommandSource.TELEGRAM)],
        'Часы не синхронизированы: гостей можно включить, когда часы синхронизируются.',
    )


def test_guests_without_a_guest_room_change_nothing(
    f_hostless_runner: ClimateCommandRunner,
    f_guests: ExecutableStub[SetGuestsInputDTO, SetGuestsOutputDTO],
) -> None:
    reply = f_hostless_runner.run(OwnerCommand(kind=CommandKind.GUESTS, nights=1), NOW, clock_trusted=True)

    assert (f_guests.inputs, reply) == ([], 'Гостевых комнат нет: отметьте их в настройках.')


def test_guests_off_end_the_guests(
    f_runner: ClimateCommandRunner,
    f_guests: ExecutableStub[SetGuestsInputDTO, SetGuestsOutputDTO],
) -> None:
    reply = f_runner.run(OwnerCommand(kind=CommandKind.GUESTS_OFF), NOW, clock_trusted=True)

    assert (f_guests.inputs, reply) == (
        [SetGuestsInputDTO(nights=0, now=NOW, clock_trusted=True, source=CommandSource.TELEGRAM)],
        'Режим гостей выключен.',
    )


@pytest.mark.parametrize(
    ('command', 'change', 'value'),
    [
        (OwnerCommand(kind=CommandKind.WARMER, room=OPEN_SPACE_ROOM), OverrideChange.WARMER, None),
        (OwnerCommand(kind=CommandKind.COOLER, room=OPEN_SPACE_ROOM), OverrideChange.COOLER, None),
        (
            OwnerCommand(kind=CommandKind.SET, room=OPEN_SPACE_ROOM, value=Decimal('23.0')),
            OverrideChange.SET,
            Decimal('23.0'),
        ),
    ],
    ids=['warmer', 'cooler', 'set'],
)
def test_a_target_command_overrides_a_room_that_reads_the_air_and_names_each_floor_s_next_write(
    f_runner: ClimateCommandRunner,
    f_override: ExecutableStub[OverrideRoomInputDTO, OverrideRoomOutputDTO],
    command: OwnerCommand,
    change: OverrideChange,
    value: Decimal | None,
) -> None:
    reply = f_runner.run(command, NOW, clock_trusted=False)

    assert (f_override.inputs, reply) == (
        [
            OverrideRoomInputDTO(
                room='open-space',
                change=change,
                value=value,
                now=NOW,
                clock_trusted=False,
                source=CommandSource.TELEGRAM,
            ),
        ],
        f'Гостиная, кухня и прихожая: 23,0° до 18:40.\n{OPEN_SPACE_FLOORS}',
    )


def test_cold_in_a_room_with_floors_and_no_air_reading_boosts_its_floors(
    f_runner: ClimateCommandRunner,
    f_override: ExecutableStub[OverrideRoomInputDTO, OverrideRoomOutputDTO],
    f_boost: ExecutableStub[BoostZonesInputDTO, BoostZonesOutputDTO],
) -> None:
    reply = f_runner.run(OwnerCommand(kind=CommandKind.WARMER, room=BATHROOM_ROOM), NOW, clock_trusted=True)

    assert (f_override.inputs, f_boost.inputs, reply) == (
        [],
        [BoostZonesInputDTO(zones=('bathroom-2f',), now=NOW)],
        'Санузел 2 этаж: датчика воздуха нет — буст до 18:40.\nСанузел 2 этаж: пол 30° в ближайшую минуту',
    )


@pytest.mark.parametrize(
    ('command', 'expected'),
    [
        (
            OwnerCommand(kind=CommandKind.COOLER, room=BATHROOM_ROOM),
            'Санузел 2 этаж: датчика воздуха нет, работает только буст.',
        ),
        (
            OwnerCommand(kind=CommandKind.SET, room=BATHROOM_ROOM, value=Decimal('22')),
            'Санузел 2 этаж: датчика воздуха нет, работает только буст.',
        ),
        (OwnerCommand(kind=CommandKind.WARMER, room=BEDROOM_ROOM), 'Спальня: teplo пока нечем здесь греть.'),
        (OwnerCommand(kind=CommandKind.BOOST, room=BEDROOM_ROOM), 'Спальня: здесь нет тёплого пола.'),
    ],
    ids=['cooler_without_air', 'set_without_air', 'warmer_with_nothing_to_heat', 'boost_without_a_floor'],
)
def test_a_command_a_room_cannot_take_is_answered_and_changes_nothing(
    f_runner: ClimateCommandRunner,
    f_override: ExecutableStub[OverrideRoomInputDTO, OverrideRoomOutputDTO],
    f_boost: ExecutableStub[BoostZonesInputDTO, BoostZonesOutputDTO],
    command: OwnerCommand,
    expected: str,
) -> None:
    reply = f_runner.run(command, NOW, clock_trusted=True)

    assert (f_override.inputs, f_boost.inputs, reply) == ([], [], expected)


def test_boost_boosts_every_floor_of_the_room(
    f_runner: ClimateCommandRunner,
    f_boost: ExecutableStub[BoostZonesInputDTO, BoostZonesOutputDTO],
) -> None:
    reply = f_runner.run(OwnerCommand(kind=CommandKind.BOOST, room=OPEN_SPACE_ROOM), NOW, clock_trusted=True)

    assert (f_boost.inputs, reply) == (
        [BoostZonesInputDTO(zones=('living-room', 'kitchen', 'hallway'), now=NOW)],
        f'Гостиная, кухня и прихожая: буст до 18:40.\n{OPEN_SPACE_FLOORS}',
    )


def test_boost_the_database_refuses_is_answered_busy_after_one_try_for_every_floor_of_the_room(
    f_locked_boost_runner: ClimateCommandRunner,
    f_locked_boost: ExecutableStub[BoostZonesInputDTO, BoostZonesOutputDTO],
) -> None:
    reply = f_locked_boost_runner.run(
        OwnerCommand(kind=CommandKind.BOOST, room=OPEN_SPACE_ROOM),
        NOW,
        clock_trusted=True,
    )

    assert (f_locked_boost.inputs, reply) == (
        [BoostZonesInputDTO(zones=('living-room', 'kitchen', 'hallway'), now=NOW)],
        'База данных занята: повторите через минуту.',
    )


def test_auto_clears_the_override_and_names_the_target_of_the_schedule(
    f_runner: ClimateCommandRunner,
    f_clear: ExecutableStub[ClearRoomOverrideInputDTO, ClearRoomOverrideOutputDTO],
) -> None:
    reply = f_runner.run(OwnerCommand(kind=CommandKind.AUTO, room=OPEN_SPACE_ROOM), NOW, clock_trusted=True)

    assert (f_clear.inputs, reply) == (
        [ClearRoomOverrideInputDTO(room='open-space', now=NOW, source=CommandSource.TELEGRAM)],
        'Гостиная, кухня и прихожая: снова по расписанию, 22,0° до 23:30.\n'
        'Гостиная: пол уже на 28°\n'
        'Кухня: пол уже на 28°\n'
        'Прихожая: пол отключён управлением нагрузкой',
    )


def test_auto_without_an_override_says_so(f_uncleared_runner: ClimateCommandRunner) -> None:
    reply = f_uncleared_runner.run(OwnerCommand(kind=CommandKind.AUTO, room=OPEN_SPACE_ROOM), NOW, clock_trusted=True)

    assert reply == 'Гостиная, кухня и прихожая: ручной цели нет.'


@pytest.mark.parametrize(
    ('command', 'expected'),
    [
        (OwnerCommand(kind=CommandKind.HELP), HELP),
        (
            OwnerCommand(kind=CommandKind.UNKNOWN_ROOM, candidates=(OPEN_SPACE_ROOM, BATHROOM_ROOM, BEDROOM_ROOM)),
            'Не знаю такую комнату. Комнаты: Гостиная, кухня и прихожая; Санузел 2 этаж; Спальня',
        ),
        (
            OwnerCommand(kind=CommandKind.AMBIGUOUS, candidates=(BATHROOM_1F_ROOM, BATHROOM_ROOM)),
            'Какая комната: Санузел 1 этаж или Санузел 2 этаж?',
        ),
    ],
    ids=['help', 'unknown_room', 'ambiguous'],
)
def test_a_command_without_a_room_to_act_on_is_answered_in_words(
    f_runner: ClimateCommandRunner,
    command: OwnerCommand,
    expected: str,
) -> None:
    assert f_runner.run(command, NOW, clock_trusted=True) == expected


@pytest.mark.parametrize(
    ('f_refused_runner', 'expected'),
    [
        (
            SettingsRejectedError((SettingProblem(field='target', code='out_of_range'),)),
            'Цель — от 10° до 30°.',
        ),
        (OperationalError('database is locked'), 'База данных занята: повторите через минуту.'),
        (RoomNotFoundError('open-space'), f'Не знаю такую комнату. Комнаты: {ROOM_NAMES}'),
        (
            RoomNotAdjustableError('open-space'),
            'Гостиная, кухня и прихожая: датчика воздуха нет, работает только буст.',
        ),
    ],
    ids=['out_of_range', 'database_busy', 'room_gone', 'room_no_longer_reads_the_air'],
    indirect=['f_refused_runner'],
)
def test_a_refused_override_is_answered_in_words(f_refused_runner: ClimateCommandRunner, expected: str) -> None:
    reply = f_refused_runner.run(OwnerCommand(kind=CommandKind.COOLER, room=OPEN_SPACE_ROOM), NOW, clock_trusted=True)

    assert reply == expected


@pytest.mark.parametrize(
    ('f_unread_after_runner', 'command', 'expected'),
    [
        (
            (HOUSE, LOCKED),
            OwnerCommand(kind=CommandKind.WARMER, room=OPEN_SPACE_ROOM),
            'Гостиная, кухня и прихожая: 23,0° до 18:40.',
        ),
        (
            (HOUSE, LOCKED),
            OwnerCommand(kind=CommandKind.GUESTS, nights=1),
            'Гости до 14:00 завтра: Гостиная, кухня и прихожая в комфорте.',
        ),
        (
            (HOUSE, LOCKED),
            OwnerCommand(kind=CommandKind.BOOST, room=BATHROOM_ROOM),
            'Санузел 2 этаж: буст до 18:40.',
        ),
        (
            (LOCKED,),
            OwnerCommand(kind=CommandKind.AUTO, room=OPEN_SPACE_ROOM),
            'Гостиная, кухня и прихожая: снова по расписанию.',
        ),
    ],
    ids=['override', 'guests', 'boost', 'auto'],
    indirect=['f_unread_after_runner'],
)
def test_a_change_whose_floors_cannot_be_read_back_is_answered_with_what_it_did(
    f_unread_after_runner: ClimateCommandRunner,
    f_guests: ExecutableStub[SetGuestsInputDTO, SetGuestsOutputDTO],
    f_override: ExecutableStub[OverrideRoomInputDTO, OverrideRoomOutputDTO],
    f_clear: ExecutableStub[ClearRoomOverrideInputDTO, ClearRoomOverrideOutputDTO],
    f_boost: ExecutableStub[BoostZonesInputDTO, BoostZonesOutputDTO],
    command: OwnerCommand,
    expected: str,
) -> None:
    reply = f_unread_after_runner.run(command, NOW, clock_trusted=True)

    changes = len(f_guests.inputs) + len(f_override.inputs) + len(f_clear.inputs) + len(f_boost.inputs)
    assert (changes, reply) == (1, expected)


def test_replies_follow_the_configured_language(f_english_runner: ClimateCommandRunner) -> None:
    assert f_english_runner.run(OwnerCommand(kind=CommandKind.GUESTS_OFF), NOW, clock_trusted=True) == (
        'Guests mode is off.'
    )


@pytest.fixture
def f_fireplace(request: pytest.FixtureRequest) -> ExecutableStub[MarkFireplaceInputDTO, MarkFireplaceOutputDTO]:
    return ExecutableStub(outcomes=(request.param,))


@pytest.fixture
def f_fireplace_runner(
    f_guests: ExecutableStub[SetGuestsInputDTO, SetGuestsOutputDTO],
    f_override: ExecutableStub[OverrideRoomInputDTO, OverrideRoomOutputDTO],
    f_clear: ExecutableStub[ClearRoomOverrideInputDTO, ClearRoomOverrideOutputDTO],
    f_boost: ExecutableStub[BoostZonesInputDTO, BoostZonesOutputDTO],
    f_rooms: RoomStatusSourceStub,
    f_fireplace: ExecutableStub[MarkFireplaceInputDTO, MarkFireplaceOutputDTO],
) -> ClimateCommandRunner:
    return _runner(f_guests, f_override, f_clear, f_boost, f_rooms, fireplace=f_fireplace)


@pytest.mark.parametrize(
    ('f_fireplace', 'command', 'lit', 'expected'),
    [
        (FIREPLACE_LIT, CommandKind.FIREPLACE_LIT, True, 'Камин топится до 20:40.'),
        (
            MarkFireplaceOutputDTO(fireplaces=(FIREPLACE,), until=None, changed=True),
            CommandKind.FIREPLACE_OUT,
            False,
            'Камин погас.',
        ),
        (
            MarkFireplaceOutputDTO(fireplaces=(FIREPLACE,), until=None, changed=False),
            CommandKind.FIREPLACE_OUT,
            False,
            'Камин и так не отмечен.',
        ),
        (FireplaceNotFoundError(None), CommandKind.FIREPLACE_LIT, True, 'В доме нет камина.'),
        (LOCKED, CommandKind.FIREPLACE_OUT, False, 'База данных занята: повторите через минуту.'),
    ],
    ids=['lit', 'out', 'out_without_a_mark', 'no_fireplace', 'database_busy'],
    indirect=['f_fireplace'],
)
def test_fireplace_commands_mark_every_fireplace_of_the_house_and_say_what_they_did(
    f_fireplace_runner: ClimateCommandRunner,
    f_fireplace: ExecutableStub[MarkFireplaceInputDTO, MarkFireplaceOutputDTO],
    command: CommandKind,
    lit: bool,
    expected: str,
) -> None:
    reply = f_fireplace_runner.run(OwnerCommand(kind=command), NOW, clock_trusted=False)

    assert (f_fireplace.inputs, reply) == (
        [MarkFireplaceInputDTO(room=None, lit=lit, now=NOW, source=CommandSource.TELEGRAM)],
        expected,
    )


def test_cold_in_the_office_raises_its_target_and_names_what_the_convector_takes(
    f_office_runner: ClimateCommandRunner,
    f_office_override: ExecutableStub[OverrideRoomInputDTO, OverrideRoomOutputDTO],
) -> None:
    reply = f_office_runner.run(OwnerCommand(kind=CommandKind.WARMER, room=OFFICE_ROOM), NOW, clock_trusted=True)

    assert (f_office_override.inputs, reply) == (
        [
            OverrideRoomInputDTO(
                room='office',
                change=OverrideChange.WARMER,
                value=None,
                now=NOW,
                clock_trusted=True,
                source=CommandSource.TELEGRAM,
            ),
        ],
        'Кабинет: 21,5° до 18:40.\nКонвектор: кабинет: 22° — наблюдение, команда не уходит',
    )


def test_status_lists_a_room_heated_by_a_source_with_what_the_unit_reads(
    f_heated_office_status_runner: ClimateCommandRunner,
) -> None:
    reply = f_heated_office_status_runner.run(OwnerCommand(kind=CommandKind.STATUS), NOW, clock_trusted=True)

    assert reply == (
        'Кабинет: цель 19,5°, ночь\n'
        'Конвектор: кабинет: включён на 19°, у прибора 21°\n'
        'Фазы: L1 12,4 А · L2 ≈8,4 А · L3 —'
    )
