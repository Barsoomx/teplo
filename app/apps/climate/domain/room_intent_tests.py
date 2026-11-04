from dataclasses import replace
from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from apps.climate.domain.backup_heat import BackupHeat
from apps.climate.domain.heat_sources import FireplaceRecord
from apps.climate.domain.modes import CommandSource, ZoneKind
from apps.climate.domain.room_climate import RoomClimateSettings, RoomOverride
from apps.climate.domain.room_intent import (
    MAX_GUEST_NIGHTS,
    IntentReason,
    OverrideChange,
    RoomIntent,
    RoomIntents,
    guests_end,
    occupancy_holds,
    override_problems,
    override_target,
    resolve_room_intent,
    resolve_room_intents,
)
from apps.climate.domain.zone import AirTargets, ComfortWindow, FloorSetpoints, ZoneSettings
from apps.climate.domain.zone_intent import ForcedTarget, ZoneIntent
from apps.core.domain.settings import SettingProblem
from apps.presence.domain.occupancy import Occupancy

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
    now=NIGHT,
)
INTENTS_HOLDING_BY_HAND = replace(INTENTS, manual_hold_live=True)
FIREPLACE = FireplaceRecord(
    slug='fireplace',
    name='Камин',
    room_slug='open-space',
    room_name='Гостиная, кухня и прихожая',
    live=False,
    lit_until=NIGHT + timedelta(hours=3),
)
STOVE = replace(FIREPLACE, slug='stove', name='Печь', room_slug='bedroom', room_name='Спальня', lit_until=None)
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


HELD_BY_HAND = replace(KITCHEN, room_slug='bedroom', manual_until=NIGHT + timedelta(hours=1))


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
    ('zone', 'heated', 'learns'),
    [
        (replace(KITCHEN, room_slug='bedroom'), frozenset({'bedroom'}), False),
        (replace(KITCHEN, room_slug='bedroom'), frozenset({'open-space'}), True),
        (replace(KITCHEN, room_slug=None), frozenset({'open-space'}), True),
    ],
    ids=['room_with_external_heat', 'another_room_heated', 'zone_without_a_room'],
)
def test_zone_of_a_room_with_external_heat_learns_nothing(
    zone: ZoneSettings,
    heated: frozenset[str],
    learns: bool,
) -> None:
    assert INTENTS.zone_intent(zone, backed_up=frozenset(), heated=heated) == ZoneIntent(
        forced=(),
        backup=False,
        learns=learns,
    )


@pytest.mark.parametrize(
    ('intents', 'zone', 'expected'),
    [
        (
            INTENTS_HOLDING_BY_HAND,
            HELD_BY_HAND,
            ZoneIntent(forced=(), backup=False, learns=False, hands_off=True),
        ),
        (
            INTENTS_HOLDING_BY_HAND,
            replace(HELD_BY_HAND, room_slug=None),
            ZoneIntent(forced=(), backup=False, learns=False, hands_off=True),
        ),
        (
            INTENTS_HOLDING_BY_HAND,
            replace(HELD_BY_HAND, room_slug='open-space'),
            ZoneIntent(forced=(OVERRIDE_AT_NIGHT,), backup=False, learns=False, hands_off=True),
        ),
        (INTENTS, HELD_BY_HAND, ZoneIntent(forced=(), backup=False, learns=True)),
        (
            INTENTS_HOLDING_BY_HAND,
            replace(HELD_BY_HAND, manual_until=NIGHT),
            ZoneIntent(forced=(), backup=False, learns=True),
        ),
    ],
    ids=['held_with_the_gate_on', 'held_in_no_room', 'held_under_an_override', 'gate_off', 'hold_over'],
)
def test_zone_held_by_hand_is_left_alone_and_learns_nothing_only_with_its_gate_on(
    intents: RoomIntents,
    zone: ZoneSettings,
    expected: ZoneIntent,
) -> None:
    assert intents.zone_intent(zone, backed_up=frozenset()) == expected


def test_room_intents_carry_the_gate_of_the_manual_hold() -> None:
    intents = resolve_room_intents(
        (OPEN_SPACE,),
        now=NIGHT,
        clock_trusted=True,
        guests_until=None,
        manual_hold_live=True,
    )

    assert (intents.manual_hold_live, INTENTS.manual_hold_live) == (True, False)


@pytest.mark.parametrize(
    ('now', 'lit_rooms'),
    [
        (NIGHT, frozenset({'open-space'})),
        (NIGHT + timedelta(hours=3), frozenset()),
        (NIGHT - timedelta(hours=2), frozenset()),
    ],
    ids=['mark_counts', 'mark_ended', 'mark_beyond_four_hours_of_a_clock_set_back'],
)
def test_room_intents_name_the_rooms_whose_fireplace_mark_counts(now: datetime, lit_rooms: frozenset[str]) -> None:
    intents = resolve_room_intents(
        (OPEN_SPACE, BEDROOM),
        now=now,
        clock_trusted=False,
        guests_until=None,
        fireplaces=(FIREPLACE, STOVE),
    )

    assert (
        intents.fireplace('open-space'),
        intents.fireplace('bedroom'),
        intents.fireplace('office'),
        intents.lit_rooms(),
    ) == (FIREPLACE, STOVE, None, lit_rooms)


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
        (datetime(2026, 11, 4, 3, 0, tzinfo=MOSCOW), 2, 2, datetime(2026, 11, 6, 2, 0, tzinfo=MOSCOW)),
        (datetime(2026, 11, 4, 3, 0, tzinfo=MOSCOW), 3, 2, datetime(2026, 11, 7, 2, 0, tzinfo=MOSCOW)),
    ],
    ids=[
        'before_six_ends_today',
        'from_six_ends_tomorrow',
        'an_hour_before_the_end_hour_still_ends_tomorrow',
        'two_nights',
        'three_nights_before_six',
        'end_hour_already_past_today_moves_to_tomorrow',
        'two_nights_from_an_end_hour_already_past_today',
        'three_nights_from_an_end_hour_already_past_today',
    ],
)
def test_guests_end_today_before_six_and_tomorrow_after(
    now: datetime,
    nights: int,
    end_hour: int,
    expected: datetime,
) -> None:
    assert guests_end(now, nights, end_hour) == expected


@pytest.mark.parametrize(
    ('now', 'end_hour'),
    [
        (datetime(2026, 11, 4, 3, 0, tzinfo=MOSCOW), 2),
        (datetime(2026, 11, 4, 1, 0, tzinfo=MOSCOW), 2),
        (datetime(2026, 11, 4, 5, 59, tzinfo=MOSCOW), 14),
        (datetime(2026, 11, 4, 22, 30, tzinfo=MOSCOW), 14),
    ],
    ids=['end_hour_past_before_six', 'end_hour_ahead_before_six', 'before_six', 'evening'],
)
def test_guests_end_a_day_later_for_each_further_night(now: datetime, end_hour: int) -> None:
    ends = [guests_end(now, nights, end_hour) for nights in range(1, MAX_GUEST_NIGHTS + 1)]

    assert [end - ends[0] for end in ends] == [timedelta(days=nights) for nights in range(MAX_GUEST_NIGHTS)]


AFTERNOON = datetime(2026, 11, 4, 15, 0, tzinfo=MOSCOW)
OFFICE_OPENS = datetime(2026, 11, 4, 13, 0, tzinfo=MOSCOW)
OFFICE_CLOSES = datetime(2026, 11, 4, 23, 45, tzinfo=MOSCOW)
OFFICE_COMFORT = Decimal('21.5')
OFFICE_NIGHT = Decimal('19.0')
OFFICE = replace(
    OPEN_SPACE,
    slug='office',
    name='Кабинет',
    targets=AirTargets(comfort=OFFICE_COMFORT, night=OFFICE_NIGHT),
    windows=tuple(ComfortWindow(weekday=weekday, start=time(13, 0), end=time(23, 45)) for weekday in range(7)),
    guest_room=False,
    follows_occupancy=True,
)
WATCHED_OFFICE = replace(OFFICE, follows_occupancy=False)
GUEST_OFFICE = replace(OFFICE, guest_room=True)
OVERRIDDEN_OFFICE = replace(OFFICE, override=OVERRIDE)


def _office(
    reason: IntentReason,
    target: Decimal,
    until: datetime | None,
    *,
    forced: tuple[ForcedTarget, ...] = (),
) -> RoomIntent:
    return RoomIntent(room='office', reason=reason, target=target, until=until, forced=forced)


@pytest.mark.parametrize(
    ('room', 'now', 'clock_trusted', 'guests_until', 'occupancy', 'expected'),
    [
        (
            WATCHED_OFFICE,
            AFTERNOON,
            True,
            None,
            Occupancy.VACANT,
            _office(IntentReason.COMFORT, OFFICE_COMFORT, OFFICE_CLOSES),
        ),
        (OFFICE, AFTERNOON, True, None, Occupancy.OCCUPIED, _office(IntentReason.OCCUPIED, OFFICE_COMFORT, None)),
        (OFFICE, NIGHT, True, None, Occupancy.OCCUPIED, _office(IntentReason.OCCUPIED, OFFICE_COMFORT, None)),
        (OFFICE, AFTERNOON, True, None, Occupancy.VACANT, _office(IntentReason.VACANT, OFFICE_NIGHT, None)),
        (OFFICE, NIGHT, True, None, Occupancy.VACANT, _office(IntentReason.VACANT, OFFICE_NIGHT, None)),
        (
            OFFICE,
            AFTERNOON,
            True,
            None,
            Occupancy.UNKNOWN,
            _office(IntentReason.COMFORT, OFFICE_COMFORT, OFFICE_CLOSES),
        ),
        (OFFICE, NIGHT, True, None, Occupancy.UNKNOWN, _office(IntentReason.SETBACK, OFFICE_NIGHT, OFFICE_OPENS)),
        (OFFICE, NIGHT, False, None, Occupancy.VACANT, _office(IntentReason.VACANT, OFFICE_NIGHT, None)),
        (
            GUEST_OFFICE,
            NIGHT,
            True,
            GUESTS_LEAVE,
            Occupancy.VACANT,
            _office(
                IntentReason.GUESTS,
                OFFICE_COMFORT,
                GUESTS_LEAVE,
                forced=(ForcedTarget(target=OFFICE_COMFORT, beneath=OFFICE_NIGHT),),
            ),
        ),
        (GUEST_OFFICE, NIGHT, False, GUESTS_LEAVE, Occupancy.VACANT, _office(IntentReason.CLOCK, OFFICE_COMFORT, None)),
        (GUEST_OFFICE, NIGHT, True, None, Occupancy.VACANT, _office(IntentReason.VACANT, OFFICE_NIGHT, None)),
        (
            replace(OFFICE, has_floor=True),
            AFTERNOON,
            True,
            None,
            Occupancy.VACANT,
            _office(IntentReason.COMFORT, OFFICE_COMFORT, OFFICE_CLOSES),
        ),
        (
            OVERRIDDEN_OFFICE,
            NIGHT,
            True,
            None,
            Occupancy.OCCUPIED,
            _office(
                IntentReason.OVERRIDE,
                Decimal('23.0'),
                NIGHT + timedelta(hours=1),
                forced=(ForcedTarget(target=Decimal('23.0'), beneath=OFFICE_NIGHT),),
            ),
        ),
    ],
    ids=[
        'gate_off_ignores_occupancy',
        'occupied_holds_comfort_inside_the_windows',
        'occupied_holds_comfort_outside_the_windows',
        'vacant_holds_the_night_target_inside_the_windows',
        'vacant_holds_the_night_target_outside_the_windows',
        'unknown_follows_the_windows',
        'unknown_follows_the_setback',
        'untrusted_clock_keeps_following_occupancy',
        'staying_guests_beat_vacant_on_a_trusted_clock',
        'staying_guests_beat_vacant_on_an_untrusted_clock',
        'guest_room_without_guests_follows_occupancy',
        'room_with_a_floor_ignores_occupancy',
        'override_beats_occupancy_and_steps_over_the_floors_target',
    ],
)
def test_room_following_occupancy_holds_comfort_while_occupied_and_its_night_target_while_vacant(
    room: RoomClimateSettings,
    now: datetime,
    clock_trusted: bool,
    guests_until: datetime | None,
    occupancy: Occupancy,
    expected: RoomIntent,
) -> None:
    intent = resolve_room_intent(
        room,
        now=now,
        clock_trusted=clock_trusted,
        guests_until=guests_until,
        occupancy=occupancy,
    )

    assert intent == expected


@pytest.mark.parametrize(
    ('room', 'now', 'clock_trusted', 'guests_until'),
    [
        (OFFICE, AFTERNOON, True, None),
        (OFFICE, NIGHT, False, None),
        (GUEST_OFFICE, NIGHT, True, GUESTS_LEAVE),
        (OVERRIDDEN_OFFICE, NIGHT, True, None),
        (replace(OVERRIDDEN_OFFICE, guest_room=True), NIGHT, True, GUESTS_LEAVE),
    ],
    ids=['schedule', 'untrusted_clock', 'guests', 'override', 'override_over_guests'],
)
@pytest.mark.parametrize('occupancy', [Occupancy.OCCUPIED, Occupancy.VACANT])
def test_forced_targets_the_floors_read_never_carry_occupancy(
    room: RoomClimateSettings,
    now: datetime,
    clock_trusted: bool,
    guests_until: datetime | None,
    occupancy: Occupancy,
) -> None:
    known = resolve_room_intent(
        room, now=now, clock_trusted=clock_trusted, guests_until=guests_until, occupancy=occupancy
    )
    unknown = resolve_room_intent(room, now=now, clock_trusted=clock_trusted, guests_until=guests_until)

    assert known.forced == unknown.forced


@pytest.mark.parametrize(
    ('room', 'occupancy', 'guests_until', 'holds'),
    [
        (OFFICE, Occupancy.OCCUPIED, None, True),
        (OFFICE, Occupancy.VACANT, None, True),
        (OFFICE, Occupancy.UNKNOWN, None, False),
        (WATCHED_OFFICE, Occupancy.VACANT, None, False),
        (replace(OFFICE, has_floor=True), Occupancy.VACANT, None, False),
        (GUEST_OFFICE, Occupancy.VACANT, GUESTS_LEAVE, False),
        (GUEST_OFFICE, Occupancy.VACANT, None, True),
        (OFFICE, Occupancy.VACANT, GUESTS_LEAVE, True),
    ],
    ids=[
        'occupied',
        'vacant',
        'unknown',
        'gate_off',
        'room_with_a_floor',
        'guests_stay_in_a_guest_room',
        'guest_room_without_guests',
        'guests_stay_in_another_room',
    ],
)
def test_occupancy_holds_only_a_room_following_it_without_a_floor_and_without_staying_guests(
    room: RoomClimateSettings,
    occupancy: Occupancy,
    guests_until: datetime | None,
    holds: bool,
) -> None:
    assert occupancy_holds(room, occupancy, guests_until=guests_until, now=NIGHT) is holds


@pytest.mark.parametrize(
    ('room', 'occupancy', 'guests_until', 'would_be'),
    [
        (WATCHED_OFFICE, Occupancy.VACANT, None, _office(IntentReason.VACANT, OFFICE_NIGHT, None)),
        (WATCHED_OFFICE, Occupancy.OCCUPIED, None, _office(IntentReason.OCCUPIED, OFFICE_COMFORT, None)),
        (OFFICE, Occupancy.VACANT, None, None),
        (WATCHED_OFFICE, Occupancy.UNKNOWN, None, None),
        (replace(WATCHED_OFFICE, override=OVERRIDE), Occupancy.VACANT, None, None),
        (replace(WATCHED_OFFICE, guest_room=True), Occupancy.VACANT, GUESTS_LEAVE, None),
        (replace(WATCHED_OFFICE, has_floor=True), Occupancy.VACANT, None, None),
    ],
    ids=[
        'watched_and_vacant',
        'watched_and_occupied',
        'gate_on',
        'unknown',
        'override_counts',
        'guests_stay',
        'room_with_a_floor',
    ],
)
def test_room_watching_occupancy_has_the_intent_occupancy_would_give_it(
    room: RoomClimateSettings,
    occupancy: Occupancy,
    guests_until: datetime | None,
    would_be: RoomIntent | None,
) -> None:
    intents = resolve_room_intents(
        (room,),
        now=NIGHT,
        clock_trusted=True,
        guests_until=guests_until,
        occupancy={'office': occupancy},
    )

    assert intents.would_be_of('office') == would_be


def test_room_intents_expose_the_occupancy_they_were_resolved_with() -> None:
    intents = resolve_room_intents(
        (WATCHED_OFFICE, BEDROOM),
        now=NIGHT,
        clock_trusted=True,
        guests_until=None,
        occupancy={'office': Occupancy.VACANT},
    )

    assert (
        dict(intents.occupancy),
        intents.occupancy_of('office'),
        intents.occupancy_of('bedroom'),
        intents.would_be_of('bedroom'),
        intents.of('office'),
    ) == (
        {'office': Occupancy.VACANT},
        Occupancy.VACANT,
        None,
        None,
        _office(IntentReason.SETBACK, OFFICE_NIGHT, OFFICE_OPENS),
    )
