from dataclasses import replace
from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from apps.climate.domain.backup_heat import BackupHeat
from apps.climate.domain.modes import CommandSource, ZoneKind
from apps.climate.domain.room_climate import RoomClimateSettings, RoomOverride
from apps.climate.domain.room_intent import (
    IntentReason,
    OverrideChange,
    RoomIntent,
    RoomIntents,
    guests_end,
    override_problems,
    override_target,
    resolve_room_intent,
    resolve_room_intents,
)
from apps.climate.domain.zone import AirTargets, ComfortWindow, FloorSetpoints, ZoneSettings
from apps.climate.domain.zone_intent import ForcedTarget, ZoneIntent
from apps.core.domain.settings import SettingProblem

MOSCOW = ZoneInfo('Europe/Moscow')
NIGHT = datetime(2026, 11, 4, 1, 0, tzinfo=MOSCOW)
MORNING = datetime(2026, 11, 4, 7, 0, tzinfo=MOSCOW)
COMFORT_AT = datetime(2026, 11, 4, 6, 30, tzinfo=MOSCOW)
COMFORT_ENDS = datetime(2026, 11, 4, 23, 30, tzinfo=MOSCOW)
GUESTS_LEAVE = datetime(2026, 11, 4, 14, 0, tzinfo=MOSCOW)
COMFORT = Decimal('22.0')
SETBACK = Decimal('19.5')
OPEN_SPACE = RoomClimateSettings(
    slug='open-space',
    name='Гостиная, кухня и прихожая',
    air_sensor_slug=None,
    targets=AirTargets(comfort=COMFORT, night=SETBACK),
    windows=tuple(ComfortWindow(weekday=weekday, start=time(6, 30), end=time(23, 30)) for weekday in range(7)),
    backup=BackupHeat(zone_slug=None, below=Decimal('19.0'), hysteresis=Decimal('0.30'), live=False),
    guest_room=True,
    override=None,
    adjustable=True,
)
OVERRIDE = RoomOverride(target=Decimal('23.0'), until=NIGHT + timedelta(hours=1), source=CommandSource.PANEL)
OVERRIDDEN = replace(OPEN_SPACE, override=OVERRIDE)
OVERRIDE_AT_NIGHT = ForcedTarget(target=Decimal('23.0'), beneath=SETBACK)
GUESTS_AT_NIGHT = ForcedTarget(target=COMFORT, beneath=SETBACK)
BEDROOM = replace(OPEN_SPACE, slug='bedroom', name='Спальня', guest_room=False, adjustable=False)
INTENTS = RoomIntents(
    rooms=(OVERRIDDEN, BEDROOM),
    by_room={
        'open-space': RoomIntent(
            room='open-space',
            reason=IntentReason.OVERRIDE,
            target=Decimal('23.0'),
            until=NIGHT + timedelta(hours=1),
            forced=(OVERRIDE_AT_NIGHT,),
        ),
        'bedroom': RoomIntent(
            room='bedroom',
            reason=IntentReason.SETBACK,
            target=SETBACK,
            until=COMFORT_AT,
            forced=(),
        ),
    },
)
KITCHEN = ZoneSettings(
    slug='kitchen',
    name='Кухня',
    kind=ZoneKind.AIR,
    thermostat_slug='thermostat-kitchen',
    air_sensor_slug='air-kitchen',
    heating_enabled=True,
    live_control=True,
    air_control_live=False,
    setpoints=FloorSetpoints(
        comfort=Decimal('28.0'),
        night=Decimal('22.0'),
        boost=Decimal('30.0'),
        idle=Decimal('18.0'),
        limit=Decimal('30.0'),
    ),
    air_targets=AirTargets(comfort=COMFORT, night=SETBACK),
    preheat_lead=timedelta(hours=3),
    windows=OPEN_SPACE.windows,
    boost_until=None,
    last_command_at=None,
    last_dry_run_at=None,
    last_air_dry_run_at=None,
    room_slug='open-space',
)


def _intent(
    reason: IntentReason,
    target: Decimal,
    until: datetime | None,
    *,
    forced: tuple[ForcedTarget, ...] = (),
) -> RoomIntent:
    return RoomIntent(room='open-space', reason=reason, target=target, until=until, forced=forced)


@pytest.mark.parametrize(
    ('room', 'now', 'clock_trusted', 'guests_until', 'expected'),
    [
        (
            OVERRIDDEN,
            NIGHT,
            False,
            GUESTS_LEAVE,
            _intent(
                IntentReason.OVERRIDE,
                Decimal('23.0'),
                NIGHT + timedelta(hours=1),
                forced=(ForcedTarget(target=Decimal('23.0'), beneath=COMFORT),),
            ),
        ),
        (OPEN_SPACE, NIGHT, False, GUESTS_LEAVE, _intent(IntentReason.CLOCK, COMFORT, None)),
        (
            OPEN_SPACE,
            NIGHT,
            True,
            GUESTS_LEAVE,
            _intent(IntentReason.GUESTS, COMFORT, GUESTS_LEAVE, forced=(GUESTS_AT_NIGHT,)),
        ),
        (
            replace(OPEN_SPACE, guest_room=False),
            NIGHT,
            True,
            GUESTS_LEAVE,
            _intent(IntentReason.SETBACK, SETBACK, COMFORT_AT),
        ),
        (
            OPEN_SPACE,
            NIGHT,
            True,
            NIGHT + timedelta(days=4, minutes=1),
            _intent(IntentReason.SETBACK, SETBACK, COMFORT_AT),
        ),
        (
            OPEN_SPACE,
            NIGHT,
            True,
            NIGHT,
            _intent(IntentReason.SETBACK, SETBACK, COMFORT_AT),
        ),
        (
            replace(OPEN_SPACE, override=replace(OVERRIDE, until=NIGHT + timedelta(hours=2, minutes=1))),
            NIGHT,
            True,
            None,
            _intent(IntentReason.SETBACK, SETBACK, COMFORT_AT),
        ),
        (
            replace(OPEN_SPACE, override=replace(OVERRIDE, until=NIGHT)),
            NIGHT,
            True,
            None,
            _intent(IntentReason.SETBACK, SETBACK, COMFORT_AT),
        ),
        (
            OVERRIDDEN,
            NIGHT,
            True,
            GUESTS_LEAVE,
            _intent(
                IntentReason.OVERRIDE,
                Decimal('23.0'),
                NIGHT + timedelta(hours=1),
                forced=(GUESTS_AT_NIGHT, ForcedTarget(target=Decimal('23.0'), beneath=COMFORT)),
            ),
        ),
        (
            OVERRIDDEN,
            NIGHT,
            True,
            None,
            _intent(IntentReason.OVERRIDE, Decimal('23.0'), NIGHT + timedelta(hours=1), forced=(OVERRIDE_AT_NIGHT,)),
        ),
        (
            replace(OPEN_SPACE, override=replace(OVERRIDE, until=MORNING + timedelta(hours=1))),
            MORNING,
            True,
            None,
            _intent(
                IntentReason.OVERRIDE,
                Decimal('23.0'),
                MORNING + timedelta(hours=1),
                forced=(ForcedTarget(target=Decimal('23.0'), beneath=COMFORT),),
            ),
        ),
        (OPEN_SPACE, MORNING, True, None, _intent(IntentReason.COMFORT, COMFORT, COMFORT_ENDS)),
        (OPEN_SPACE, NIGHT, True, None, _intent(IntentReason.SETBACK, SETBACK, COMFORT_AT)),
        (
            replace(OPEN_SPACE, windows=()),
            NIGHT,
            True,
            None,
            _intent(IntentReason.SETBACK, SETBACK, None),
        ),
    ],
    ids=[
        'override_beats_an_untrusted_clock',
        'untrusted_clock_beats_guests',
        'guests_hold_a_guest_room_at_comfort',
        'guests_only_in_a_guest_room',
        'guests_beyond_four_days_ignored',
        'guests_gone',
        'override_ending_more_than_two_hours_ahead_ignored',
        'override_over',
        'override_beats_guests_and_steps_from_them',
        'override_steps_from_the_setback',
        'override_steps_from_the_comfort_target',
        'comfort_window_until_its_end',
        'outside_the_windows_setback_until_the_next_comfort_start',
        'room_without_windows_stays_in_setback',
    ],
)
def test_room_intent_takes_the_first_rule_that_applies(
    room: RoomClimateSettings,
    now: datetime,
    clock_trusted: bool,
    guests_until: datetime | None,
    expected: RoomIntent,
) -> None:
    assert resolve_room_intent(room, now=now, clock_trusted=clock_trusted, guests_until=guests_until) == expected


def test_room_intents_resolve_every_room_with_the_same_guests() -> None:
    intents = resolve_room_intents((OPEN_SPACE, BEDROOM), now=NIGHT, clock_trusted=True, guests_until=GUESTS_LEAVE)

    assert (intents.rooms, intents.of('open-space'), intents.of('bedroom'), intents.of('attic')) == (
        (OPEN_SPACE, BEDROOM),
        _intent(IntentReason.GUESTS, COMFORT, GUESTS_LEAVE, forced=(GUESTS_AT_NIGHT,)),
        RoomIntent(room='bedroom', reason=IntentReason.SETBACK, target=SETBACK, until=COMFORT_AT, forced=()),
        None,
    )


@pytest.mark.parametrize(
    ('zone', 'backed_up', 'expected'),
    [
        (KITCHEN, frozenset(), ZoneIntent(forced=(OVERRIDE_AT_NIGHT,), backup=False, learns=False)),
        (KITCHEN, frozenset({'kitchen'}), ZoneIntent(forced=(OVERRIDE_AT_NIGHT,), backup=True, learns=False)),
        (replace(KITCHEN, room_slug='bedroom'), frozenset(), ZoneIntent(forced=(), backup=False, learns=True)),
        (replace(KITCHEN, room_slug='pantry'), frozenset(), ZoneIntent(forced=(), backup=False, learns=True)),
        (replace(KITCHEN, room_slug=None), frozenset({'kitchen'}), ZoneIntent(forced=(), backup=True, learns=True)),
    ],
    ids=[
        'override_forces_its_target',
        'backed_up_and_forced',
        'schedule_forces_nothing',
        'room_without_a_room_climate',
        'zone_without_a_room',
    ],
)
def test_zone_intent_forces_only_override_and_guests(
    zone: ZoneSettings,
    backed_up: frozenset[str],
    expected: ZoneIntent,
) -> None:
    assert INTENTS.zone_intent(zone, backed_up=backed_up) == expected


@pytest.mark.parametrize(
    ('intent', 'change', 'value', 'expected'),
    [
        (_intent(IntentReason.COMFORT, COMFORT, None), OverrideChange.UP, None, Decimal('22.5')),
        (_intent(IntentReason.COMFORT, COMFORT, None), OverrideChange.DOWN, None, Decimal('21.5')),
        (
            _intent(
                IntentReason.OVERRIDE,
                Decimal('22.5'),
                MORNING,
                forced=(ForcedTarget(target=Decimal('22.5'), beneath=COMFORT),),
            ),
            OverrideChange.UP,
            None,
            Decimal('23.0'),
        ),
        (_intent(IntentReason.SETBACK, SETBACK, None), OverrideChange.WARMER, None, COMFORT),
        (_intent(IntentReason.COMFORT, COMFORT, None), OverrideChange.WARMER, None, Decimal('23.0')),
        (_intent(IntentReason.COMFORT, COMFORT, None), OverrideChange.COOLER, None, Decimal('21.0')),
        (
            _intent(IntentReason.SETBACK, SETBACK, None),
            OverrideChange.SET,
            Decimal('21.25'),
            Decimal('21.3'),
        ),
        (_intent(IntentReason.SETBACK, SETBACK, None), OverrideChange.SET, None, SETBACK),
    ],
    ids=[
        'up_half_a_degree',
        'down_half_a_degree',
        'up_from_an_override',
        'warmer_at_least_to_comfort',
        'warmer_by_a_degree_above_comfort',
        'cooler_by_a_degree',
        'set_rounded_to_tenths',
        'set_without_a_value_keeps_the_target',
    ],
)
def test_override_target_steps(
    intent: RoomIntent,
    change: OverrideChange,
    value: Decimal | None,
    expected: Decimal,
) -> None:
    target = override_target(intent, OPEN_SPACE.targets, change, value)

    assert (target, str(target)) == (expected, str(expected.quantize(Decimal('0.1'))))


@pytest.mark.parametrize(
    ('target', 'problems'),
    [
        (Decimal('9.9'), (SettingProblem(field='target', code='out_of_range'),)),
        (Decimal('10.0'), ()),
        (Decimal('30.0'), ()),
        (Decimal('30.1'), (SettingProblem(field='target', code='out_of_range'),)),
    ],
    ids=['below_ten', 'ten', 'thirty', 'above_thirty'],
)
def test_override_problems_refuse_targets_outside_10_to_30(
    target: Decimal,
    problems: tuple[SettingProblem, ...],
) -> None:
    assert override_problems(target) == problems


@pytest.mark.parametrize(
    ('now', 'nights', 'end_hour', 'expected'),
    [
        (datetime(2026, 11, 4, 5, 59, tzinfo=MOSCOW), 1, 14, datetime(2026, 11, 4, 14, 0, tzinfo=MOSCOW)),
        (datetime(2026, 11, 4, 6, 0, tzinfo=MOSCOW), 1, 14, datetime(2026, 11, 5, 14, 0, tzinfo=MOSCOW)),
        (datetime(2026, 11, 4, 13, 0, tzinfo=MOSCOW), 1, 14, datetime(2026, 11, 5, 14, 0, tzinfo=MOSCOW)),
        (datetime(2026, 11, 4, 22, 30, tzinfo=MOSCOW), 2, 14, datetime(2026, 11, 6, 14, 0, tzinfo=MOSCOW)),
        (datetime(2026, 11, 4, 1, 0, tzinfo=MOSCOW), 3, 12, datetime(2026, 11, 6, 12, 0, tzinfo=MOSCOW)),
        (datetime(2026, 11, 4, 5, 0, tzinfo=MOSCOW), 1, 3, datetime(2026, 11, 5, 3, 0, tzinfo=MOSCOW)),
    ],
    ids=[
        'before_six_ends_today',
        'from_six_ends_tomorrow',
        'an_hour_before_the_end_hour_still_ends_tomorrow',
        'two_nights',
        'three_nights_before_six',
        'end_hour_already_past_today_moves_to_tomorrow',
    ],
)
def test_guests_end_today_before_six_and_tomorrow_after(
    now: datetime,
    nights: int,
    end_hour: int,
    expected: datetime,
) -> None:
    assert guests_end(now, nights, end_hour) == expected
