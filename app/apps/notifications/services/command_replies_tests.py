from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.utils import translation

from apps.climate.domain.drive import PendingKind, PendingWrite
from apps.climate.domain.room_intent import IntentReason, RoomIntent
from apps.climate.domain.room_status import RoomStatus, ZoneStatus
from apps.notifications.services.command_replies import (
    degrees,
    either_of,
    moment_text,
    phases_line,
    room_line,
    setpoint_degrees,
    zone_state_line,
    zone_write_line,
)

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
    ],
    ids=['next_write', 'within_a_minute', 'holds', 'shed', 'watching', 'unreached', 'heating_off', 'by_air'],
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
def test_phases_line_marks_a_stale_phase() -> None:
    assert phases_line((('l1', Decimal('12.4')), ('l2', None), ('l3', Decimal('3')))) == (
        'Фазы: L1 12,4 А · L2 — · L3 3,0 А'
    )


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
