from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.utils import translation

from apps.climate.domain.drive import PendingKind, PendingWrite
from apps.climate.domain.heat_sources import HeatSourceKind
from apps.climate.domain.room_intent import IntentReason, RoomIntent
from apps.climate.domain.room_status import RoomStatus, SourceStatus, ZoneStatus
from apps.devices.domain.readings import ClimateMode
from apps.notifications.domain.alerts import PhaseReading
from apps.notifications.services.command_replies import (
    FIREPLACE_LIT,
    FIREPLACE_NOT_MARKED,
    FIREPLACE_OUT,
    HELP,
    NO_FIREPLACE,
    degrees,
    either_of,
    moment_text,
    phases_line,
    room_line,
    setpoint_degrees,
    source_state_line,
    source_write_line,
    zone_state_line,
    zone_write_line,
)
from apps.presence.domain.occupancy import Occupancy

MOSCOW = ZoneInfo('Europe/Moscow')
NOW = datetime(2026, 11, 4, 16, 40, tzinfo=MOSCOW)
KITCHEN = ZoneStatus(
    slug='kitchen',
    name='Кухня',
    available=True,
    air=Decimal('21.4'),
    floor_temperature=Decimal('27.5'),
    setpoint=Decimal('28.0'),
    power=True,
    air_path=False,
    floor_target=Decimal('30.0'),
    pending=PendingWrite(kind=PendingKind.NEXT_WRITE, at=NOW + timedelta(minutes=12)),
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
    zones=(KITCHEN,),
)
OFFICE = RoomStatus(
    slug='office',
    name='Кабинет',
    air=None,
    intent=RoomIntent(
        room='office',
        reason=IntentReason.COMFORT,
        target=Decimal('21.5'),
        until=datetime(2026, 11, 4, 23, 45, tzinfo=MOSCOW),
        forced=(),
    ),
    adjustable=True,
    guest_room=False,
    zones=(),
    occupancy=Occupancy.VACANT,
)
FOLLOWING_OFFICE = replace(OFFICE, follows_occupancy=True)
VACANT_INTENT = RoomIntent(room='office', reason=IntentReason.VACANT, target=Decimal('19.0'), until=None, forced=())

CONVECTOR = SourceStatus(
    slug='convector-office',
    name='Конвектор: кабинет',
    kind=HeatSourceKind.HEATER,
    room='office',
    device='heater-office',
    priority=1,
    live=True,
    available=True,
    error_code=None,
    error=None,
    on=True,
    setpoint=Decimal(19),
    level='High',
    temperature=Decimal(21),
    target_on=True,
    target_setpoint=Decimal(22),
    commands_live=True,
    held_until=None,
    shed=False,
    pending=PendingWrite(kind=PendingKind.NEXT_WRITE, at=NOW + timedelta(minutes=12)),
)
AIR_CONDITIONER = replace(
    CONVECTOR,
    slug='ac-bedroom',
    name='Кондиционер: спальня',
    kind=HeatSourceKind.AIR_CONDITIONER,
    room='bedroom',
    device='ac-bedroom',
    setpoint=Decimal(24),
    level=None,
    temperature=Decimal('18.5'),
    target_setpoint=Decimal('20.5'),
    pending=None,
    mode=ClimateMode.COOL,
)


@pytest.fixture
def russian() -> Iterator[None]:
    with translation.override('ru'):
        yield


@pytest.fixture
def english() -> Iterator[None]:
    with translation.override('en'):
        yield


@pytest.mark.usefixtures('russian')
@pytest.mark.parametrize(
    ('at', 'expected'),
    [
        (datetime(2026, 11, 4, 18, 40, tzinfo=MOSCOW), '18:40'),
        (datetime(2026, 11, 5, 0, 10, tzinfo=MOSCOW), '00:10 завтра'),
        (datetime(2026, 11, 4, 21, 10, tzinfo=UTC), '00:10 завтра'),
        (datetime(2026, 11, 6, 14, 0, tzinfo=MOSCOW), '14:00 6 ноября'),
    ],
    ids=['today', 'tomorrow', 'tomorrow_in_house_time', 'another_day'],
)
def test_moment_text_names_the_day_only_when_it_is_not_today(at: datetime, expected: str) -> None:
    assert moment_text(at, NOW) == expected


@pytest.mark.usefixtures('russian')
def test_degrees_give_targets_and_readings_in_tenths_and_setpoints_whole_when_they_are() -> None:
    assert (
        degrees(Decimal('23')),
        degrees(Decimal('18.84')),
        setpoint_degrees(Decimal('30.0')),
        setpoint_degrees(Decimal('28.5')),
    ) == ('23,0', '18,8', '30', '28,5')


@pytest.mark.usefixtures('russian')
def test_either_of_asks_between_the_names() -> None:
    assert either_of(['Санузел 1 этаж', 'Санузел 2 этаж']) == 'Санузел 1 этаж или Санузел 2 этаж'


@pytest.mark.usefixtures('russian')
@pytest.mark.parametrize(
    ('zone', 'expected'),
    [
        (KITCHEN, 'Кухня: пол 30° не раньше 16:52'),
        (
            replace(KITCHEN, pending=PendingWrite(kind=PendingKind.NEXT_WRITE, at=NOW)),
            'Кухня: пол 30° в ближайшую минуту',
        ),
        (replace(KITCHEN, pending=PendingWrite(kind=PendingKind.HOLDS, at=None)), 'Кухня: пол уже на 30°'),
        (
            replace(KITCHEN, pending=PendingWrite(kind=PendingKind.HELD, at=None)),
            'Кухня: пол отключён управлением нагрузкой',
        ),
        (
            replace(KITCHEN, pending=PendingWrite(kind=PendingKind.WATCHING, at=None)),
            'Кухня: наблюдение, команда не уйдёт',
        ),
        (replace(KITCHEN, pending=PendingWrite(kind=PendingKind.UNREACHED, at=None)), 'Кухня: нет связи с термостатом'),
        (replace(KITCHEN, floor_target=None), 'Кухня: отопление выключено'),
        (replace(KITCHEN, air_path=True, floor_target=None, pending=None), 'Кухня: по воздуху'),
        (
            replace(KITCHEN, pending=PendingWrite(kind=PendingKind.HANDS_OFF, at=NOW + timedelta(hours=2))),
            'Кухня: изменён на термостате, teplo не трогает до 18:40',
        ),
        (
            replace(
                KITCHEN,
                air_path=True,
                floor_target=None,
                pending=PendingWrite(kind=PendingKind.HANDS_OFF, at=NOW + timedelta(hours=1)),
            ),
            'Кухня: изменён на термостате, teplo не трогает до 17:40',
        ),
    ],
    ids=[
        'next_write',
        'within_a_minute',
        'holds',
        'shed',
        'watching',
        'unreached',
        'heating_off',
        'by_air',
        'held_by_hand',
        'held_by_hand_by_air',
    ],
)
def test_zone_write_line_names_the_setpoint_and_when_it_reaches_the_floor(zone: ZoneStatus, expected: str) -> None:
    assert zone_write_line(zone, NOW) == expected


@pytest.mark.usefixtures('russian')
@pytest.mark.parametrize(
    ('zone', 'expected'),
    [
        (KITCHEN, 'Кухня: воздух 21,4°, пол 27,5° (на термостате 28°)'),
        (replace(KITCHEN, air=None), 'Кухня: пол 27,5° (на термостате 28°)'),
        (replace(KITCHEN, power=False), 'Кухня: пол 27,5°, термостат выключен'),
        (
            replace(KITCHEN, available=False, floor_temperature=None, setpoint=None, power=None),
            'Кухня: нет связи с термостатом',
        ),
    ],
    ids=['with_air', 'without_air', 'thermostat_off', 'unreached'],
)
def test_zone_state_line_gives_the_readings(zone: ZoneStatus, expected: str) -> None:
    assert zone_state_line(zone) == expected


@pytest.mark.usefixtures('russian')
@pytest.mark.parametrize(
    ('room', 'expected'),
    [
        (OPEN_SPACE, 'Гостиная, кухня и прихожая: цель 22,0°, комфорт до 23:30'),
        (
            replace(
                OPEN_SPACE,
                air=Decimal('21.6'),
                intent=replace(OPEN_SPACE.intent, reason=IntentReason.OVERRIDE, target=Decimal('23.0')),
            ),
            'Гостиная, кухня и прихожая: 21,6°, цель 23,0°, вручную до 23:30',
        ),
        (
            replace(
                OPEN_SPACE,
                intent=replace(
                    OPEN_SPACE.intent,
                    reason=IntentReason.GUESTS,
                    until=datetime(2026, 11, 5, 14, 0, tzinfo=MOSCOW),
                ),
            ),
            'Гостиная, кухня и прихожая: цель 22,0°, гости до 14:00 завтра',
        ),
        (
            replace(OPEN_SPACE, intent=replace(OPEN_SPACE.intent, reason=IntentReason.SETBACK, until=None)),
            'Гостиная, кухня и прихожая: цель 22,0°, ночь',
        ),
        (
            replace(OPEN_SPACE, intent=replace(OPEN_SPACE.intent, reason=IntentReason.CLOCK, until=None)),
            'Гостиная, кухня и прихожая: цель 22,0°, часы не сверены',
        ),
    ],
    ids=['comfort', 'override_with_air', 'guests', 'night_without_a_window', 'untrusted_clock'],
)
def test_room_line_gives_the_target_and_why(room: RoomStatus, expected: str) -> None:
    assert room_line(room, NOW) == expected


@pytest.mark.usefixtures('russian')
@pytest.mark.parametrize(
    ('room', 'expected'),
    [
        (OFFICE, 'Кабинет: цель 21,5°, комфорт до 23:45; никого (наблюдение)'),
        (
            replace(OFFICE, occupancy=Occupancy.UNKNOWN),
            'Кабинет: цель 21,5°, комфорт до 23:45; нет данных о присутствии (наблюдение)',
        ),
        (replace(FOLLOWING_OFFICE, intent=VACANT_INTENT), 'Кабинет: цель 19,0°, никого'),
        (
            replace(
                FOLLOWING_OFFICE,
                occupancy=Occupancy.OCCUPIED,
                intent=replace(VACANT_INTENT, reason=IntentReason.OCCUPIED, target=Decimal('21.5')),
            ),
            'Кабинет: цель 21,5°, занято',
        ),
        (
            replace(FOLLOWING_OFFICE, occupancy=Occupancy.UNKNOWN),
            'Кабинет: цель 21,5°, комфорт до 23:45; нет данных о присутствии',
        ),
        (
            replace(
                FOLLOWING_OFFICE, intent=replace(OFFICE.intent, reason=IntentReason.OVERRIDE, target=Decimal('22.5'))
            ),
            'Кабинет: цель 22,5°, вручную до 23:45; никого',
        ),
    ],
    ids=['watching', 'watching_without_data', 'vacant', 'occupied', 'live_without_data', 'override_over_occupancy'],
)
def test_room_line_names_the_occupancy_of_a_room_with_a_presence_source(room: RoomStatus, expected: str) -> None:
    assert room_line(room, NOW) == expected


@pytest.mark.usefixtures('russian')
@pytest.mark.parametrize(
    ('l2', 'expected'),
    [
        (PhaseReading(phase='l2', amps=None, estimated=False), 'Фазы: L1 12,4 А · L2 — · L3 3,0 А'),
        (PhaseReading(phase='l2', amps=Decimal('12.42'), estimated=True), 'Фазы: L1 12,4 А · L2 ≈12,4 А · L3 3,0 А'),
    ],
    ids=['stale_phase', 'estimated_phase'],
)
def test_phases_line_marks_a_stale_and_an_estimated_phase(l2: PhaseReading, expected: str) -> None:
    readings = (
        PhaseReading(phase='l1', amps=Decimal('12.4'), estimated=False),
        l2,
        PhaseReading(phase='l3', amps=Decimal('3'), estimated=False),
    )

    assert phases_line(readings) == expected


@pytest.mark.usefixtures('english')
def test_replies_follow_the_active_language() -> None:
    assert (
        moment_text(datetime(2026, 11, 5, 0, 10, tzinfo=MOSCOW), NOW),
        zone_write_line(KITCHEN, NOW),
        room_line(OPEN_SPACE, NOW),
    ) == (
        '00:10 tomorrow',
        'Кухня: floor 30° no sooner than 16:52',
        'Гостиная, кухня и прихожая: target 22.0°, comfort until 23:30',
    )


@pytest.mark.usefixtures('russian')
def test_fireplace_replies_and_the_command_list_name_the_fireplace() -> None:
    assert (
        str(FIREPLACE_LIT) % {'until': '22:10'},
        str(FIREPLACE_OUT),
        str(FIREPLACE_NOT_MARKED),
        str(NO_FIREPLACE),
        str(HELP).splitlines()[-2],
    ) == (
        'Камин топится до 22:10.',
        'Камин погас.',
        'Камин и так не отмечен.',
        'В доме нет камина.',
        'топлю камин — камин топится 4 часа; камин погас — снять отметку',
    )


@pytest.mark.usefixtures('russian')
@pytest.mark.parametrize(
    ('source', 'expected'),
    [
        (CONVECTOR, 'Конвектор: кабинет: 22° не раньше 16:52'),
        (
            replace(CONVECTOR, pending=PendingWrite(kind=PendingKind.NEXT_WRITE, at=NOW)),
            'Конвектор: кабинет: 22° в ближайшую минуту',
        ),
        (
            replace(CONVECTOR, pending=PendingWrite(kind=PendingKind.WATCHING, at=None)),
            'Конвектор: кабинет: 22° — наблюдение, команда не уходит',
        ),
        (
            replace(CONVECTOR, pending=PendingWrite(kind=PendingKind.HELD, at=None)),
            'Конвектор: кабинет: выключен управлением нагрузкой',
        ),
        (
            replace(CONVECTOR, pending=PendingWrite(kind=PendingKind.HANDS_OFF, at=NOW + timedelta(hours=2))),
            'Конвектор: кабинет: изменён на приборе, teplo не трогает до 18:40',
        ),
        (replace(CONVECTOR, pending=PendingWrite(kind=PendingKind.HOLDS, at=None)), 'Конвектор: кабинет: уже 22°'),
        (
            replace(
                CONVECTOR, target_on=False, target_setpoint=None, pending=PendingWrite(kind=PendingKind.HOLDS, at=None)
            ),
            'Конвектор: кабинет: уже выключен',
        ),
        (
            replace(CONVECTOR, target_on=False, target_setpoint=None),
            'Конвектор: кабинет: выключение не раньше 16:52',
        ),
        (
            replace(CONVECTOR, pending=PendingWrite(kind=PendingKind.UNREACHED, at=None)),
            'Конвектор: кабинет: нет связи',
        ),
        (replace(CONVECTOR, pending=None), None),
        (
            replace(
                AIR_CONDITIONER,
                mode=ClimateMode.AUTO,
                pending=PendingWrite(kind=PendingKind.NEXT_WRITE, at=NOW + timedelta(minutes=12)),
            ),
            'Кондиционер: спальня: 20,5° не раньше 16:52',
        ),
        (AIR_CONDITIONER, None),
    ],
    ids=[
        'next_write',
        'within_a_minute',
        'watching',
        'shed',
        'held_by_hand',
        'holds',
        'already_off',
        'switch_off',
        'unreached',
        'not_driven',
        'taken_back_from_auto',
        'cooled_by_the_owner',
    ],
)
def test_source_write_line_names_the_setpoint_and_when_it_reaches_the_unit(
    source: SourceStatus,
    expected: str | None,
) -> None:
    assert source_write_line(source, NOW) == expected


@pytest.mark.usefixtures('russian')
@pytest.mark.parametrize(
    ('source', 'expected'),
    [
        (CONVECTOR, 'Конвектор: кабинет: включён на 19°, у прибора 21°'),
        (replace(CONVECTOR, on=False), 'Конвектор: кабинет: выключен, у прибора 21°'),
        (replace(CONVECTOR, available=False), 'Конвектор: кабинет: нет связи'),
        (
            replace(AIR_CONDITIONER, mode=ClimateMode.HEAT, setpoint=Decimal('20.5'), temperature=None),
            'Кондиционер: спальня: включён на 20,5°',
        ),
        (replace(AIR_CONDITIONER, mode=ClimateMode.OFF, on=False, temperature=None), 'Кондиционер: спальня: выключен'),
        (AIR_CONDITIONER, 'Кондиционер: спальня: охлаждение, 24°, teplo не трогает'),
        (replace(AIR_CONDITIONER, mode=ClimateMode.AUTO), 'Кондиционер: спальня: авто, 24°'),
        (replace(AIR_CONDITIONER, mode=ClimateMode.OTHER), 'Кондиционер: спальня: другой режим, 24°'),
        (
            replace(AIR_CONDITIONER, mode=ClimateMode.OFF, on=False, locked_out=True, outdoor=Decimal('-17.0')),
            'Кондиционер: спальня: выключен, у прибора 18,5°; блокировка морозом, на улице -17°',
        ),
        (
            replace(AIR_CONDITIONER, mode=ClimateMode.HEAT, setpoint=Decimal('20.5'), locked_out=True, outdoor=None),
            'Кондиционер: спальня: включён на 20,5°, у прибора 18,5°; блокировка: нет показаний с улицы',
        ),
    ],
    ids=[
        'on',
        'off',
        'unreached',
        'on_without_its_own_temperature',
        'off_without_its_own_temperature',
        'cooled_by_the_owner',
        'auto',
        'another_mode',
        'frost_lockout',
        'locked_out_without_an_outdoor_reading',
    ],
)
def test_source_state_line_gives_what_the_unit_reads(source: SourceStatus, expected: str) -> None:
    assert source_state_line(source) == expected
