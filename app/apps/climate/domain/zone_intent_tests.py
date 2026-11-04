from dataclasses import replace
from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from apps.climate.domain.floor_policy import ThermostatTarget
from apps.climate.domain.modes import ScheduleMode, ZoneKind
from apps.climate.domain.zone import AirTargets, ComfortWindow, FloorSetpoints, ZoneSettings
from apps.climate.domain.zone_intent import (
    FloorDecision,
    ForcedTarget,
    decide_floor_mode,
    forced_air_mode,
    forced_air_target,
)

MOSCOW = ZoneInfo('Europe/Moscow')
NIGHT = datetime(2026, 11, 2, 1, 0, tzinfo=MOSCOW)
PREHEAT = datetime(2026, 11, 2, 4, 0, tzinfo=MOSCOW)
MORNING = datetime(2026, 11, 2, 7, 0, tzinfo=MOSCOW)
ZONE = ZoneSettings(
    slug='living-room',
    name='Гостиная',
    kind=ZoneKind.AIR,
    thermostat_slug='thermostat-living-room',
    air_sensor_slug='air-living-room',
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
    air_targets=AirTargets(comfort=Decimal('22.0'), night=Decimal('19.5')),
    preheat_lead=timedelta(hours=3),
    windows=tuple(ComfortWindow(weekday=weekday, start=time(6, 30), end=time(23, 30)) for weekday in range(7)),
    boost_until=None,
    last_command_at=None,
    last_dry_run_at=None,
    last_air_dry_run_at=None,
    room_slug='open-space',
)
COMFORT = ThermostatTarget(power=True, setpoint=Decimal('28.0'))
NIGHT_SETPOINT = ThermostatTarget(power=True, setpoint=Decimal('22.0'))
BOOST = ThermostatTarget(power=True, setpoint=Decimal('30.0'))
OFF = ThermostatTarget(power=False, setpoint=None)
GUESTS_AT_NIGHT = ForcedTarget(target=Decimal('22.0'), beneath=Decimal('19.5'))


def _over(target: str, beneath: str) -> ForcedTarget:
    return ForcedTarget(target=Decimal(target), beneath=Decimal(beneath))


@pytest.mark.parametrize(
    ('zone', 'forced', 'now', 'clock_trusted', 'expected'),
    [
        (ZONE, (_over('20.0', '19.5'),), NIGHT, True, FloorDecision(mode=ScheduleMode.COMFORT, target=COMFORT)),
        (ZONE, (_over('22.5', '22.0'),), MORNING, True, FloorDecision(mode=ScheduleMode.COMFORT, target=BOOST)),
        (ZONE, (_over('22.5', '19.5'),), PREHEAT, True, FloorDecision(mode=ScheduleMode.PREHEAT, target=BOOST)),
        (
            ZONE,
            (_over('21.5', '22.0'),),
            MORNING,
            True,
            FloorDecision(mode=ScheduleMode.NIGHT, target=NIGHT_SETPOINT),
        ),
        (ZONE, (_over('20.0', '19.5'),), PREHEAT, True, FloorDecision(mode=ScheduleMode.PREHEAT, target=COMFORT)),
        (
            ZONE,
            (_over('19.0', '19.5'),),
            PREHEAT,
            True,
            FloorDecision(mode=ScheduleMode.NIGHT, target=NIGHT_SETPOINT),
        ),
        (ZONE, (_over('19.0', '19.5'),), NIGHT, True, FloorDecision(mode=ScheduleMode.NIGHT, target=NIGHT_SETPOINT)),
        (ZONE, (_over('22.0', '22.0'),), MORNING, True, FloorDecision(mode=ScheduleMode.COMFORT, target=COMFORT)),
        (ZONE, (_over('19.5', '19.5'),), NIGHT, True, FloorDecision(mode=ScheduleMode.NIGHT, target=NIGHT_SETPOINT)),
        (ZONE, (GUESTS_AT_NIGHT,), NIGHT, True, FloorDecision(mode=ScheduleMode.COMFORT, target=COMFORT)),
        (ZONE, (GUESTS_AT_NIGHT,), PREHEAT, True, FloorDecision(mode=ScheduleMode.PREHEAT, target=COMFORT)),
        (
            ZONE,
            (GUESTS_AT_NIGHT, _over('21.5', '22.0')),
            NIGHT,
            True,
            FloorDecision(mode=ScheduleMode.NIGHT, target=NIGHT_SETPOINT),
        ),
        (
            ZONE,
            (GUESTS_AT_NIGHT, _over('22.5', '22.0')),
            NIGHT,
            True,
            FloorDecision(mode=ScheduleMode.COMFORT, target=BOOST),
        ),
        (
            replace(ZONE, boost_until=MORNING + timedelta(hours=1)),
            (_over('19.0', '22.0'),),
            MORNING,
            True,
            FloorDecision(mode=ScheduleMode.COMFORT, target=BOOST),
        ),
        (
            replace(ZONE, heating_enabled=False),
            (_over('23.0', '19.5'),),
            NIGHT,
            True,
            FloorDecision(mode=ScheduleMode.NIGHT, target=OFF),
        ),
        (ZONE, (), NIGHT, True, FloorDecision(mode=ScheduleMode.NIGHT, target=NIGHT_SETPOINT)),
        (ZONE, (), NIGHT, False, FloorDecision(mode=ScheduleMode.NIGHT, target=COMFORT)),
        (ZONE, (_over('22.5', '22.0'),), NIGHT, False, FloorDecision(mode=ScheduleMode.COMFORT, target=BOOST)),
        (
            ZONE,
            (_over('21.0', '22.0'),),
            NIGHT,
            False,
            FloorDecision(mode=ScheduleMode.NIGHT, target=NIGHT_SETPOINT),
        ),
        (ZONE, (_over('22.0', '22.0'),), NIGHT, False, FloorDecision(mode=ScheduleMode.COMFORT, target=COMFORT)),
    ],
    ids=[
        'night_and_above_goes_to_comfort',
        'comfort_and_above_goes_to_boost',
        'preheat_and_above_both_goes_to_boost',
        'comfort_and_below_goes_to_night',
        'step_up_from_the_setback_keeps_a_preheat',
        'preheat_and_below_both_goes_to_night',
        'night_and_below_stays_at_night',
        'equal_in_comfort_keeps_comfort',
        'equal_at_night_keeps_night',
        'guests_hold_comfort_at_night',
        'guests_leave_a_preheat_as_it_is',
        'step_down_from_guests_goes_to_night',
        'step_up_from_guests_goes_to_boost',
        'boost_keeps_its_setpoint',
        'heating_off_stays_off',
        'nothing_forced_keeps_the_schedule',
        'nothing_forced_on_an_untrusted_clock_holds_comfort',
        'untrusted_clock_and_above_goes_to_boost',
        'untrusted_clock_and_below_goes_to_night',
        'untrusted_clock_and_equal_holds_comfort',
    ],
)
def test_forced_target_moves_a_floor_one_setpoint_only_the_way_it_moves_the_room(
    zone: ZoneSettings,
    forced: tuple[ForcedTarget, ...],
    now: datetime,
    clock_trusted: bool,
    expected: FloorDecision,
) -> None:
    assert decide_floor_mode(zone, forced, now, clock_trusted=clock_trusted) == expected


@pytest.mark.parametrize(
    ('own', 'forced', 'expected'),
    [
        (Decimal('19.5'), (_over('20.0', '19.5'),), Decimal('20.0')),
        (Decimal('22.0'), (_over('20.0', '19.5'),), Decimal('22.0')),
        (Decimal('22.0'), (_over('19.0', '19.5'),), Decimal('19.0')),
        (Decimal('22.0'), (_over('21.5', '22.0'),), Decimal('21.5')),
        (Decimal('22.0'), (_over('22.0', '22.0'),), Decimal('22.0')),
        (Decimal('19.5'), (GUESTS_AT_NIGHT,), Decimal('22.0')),
        (Decimal('19.5'), (GUESTS_AT_NIGHT, _over('21.5', '22.0')), Decimal('21.5')),
        (Decimal('19.5'), (), Decimal('19.5')),
    ],
    ids=[
        'step_up_at_night',
        'step_up_from_the_setback_keeps_a_preheat_target',
        'step_down_from_the_setback_lowers_a_preheat_target',
        'step_down_in_comfort',
        'equal_keeps_the_target',
        'guests_at_night',
        'step_down_from_guests',
        'nothing_forced',
    ],
)
def test_forced_air_target_replaces_a_zone_s_own_target_only_the_way_it_moves_the_room(
    own: Decimal,
    forced: tuple[ForcedTarget, ...],
    expected: Decimal,
) -> None:
    assert forced_air_target(own, forced) == expected


@pytest.mark.parametrize(
    ('forced', 'mode'),
    [
        (Decimal('23.0'), ScheduleMode.COMFORT),
        (Decimal('19.6'), ScheduleMode.COMFORT),
        (Decimal('19.5'), ScheduleMode.NIGHT),
        (Decimal('18.0'), ScheduleMode.NIGHT),
    ],
    ids=['well_above_night', 'just_above_night', 'at_night', 'below_night'],
)
def test_forced_air_mode_is_comfort_above_the_night_target(forced: Decimal, mode: ScheduleMode) -> None:
    assert forced_air_mode(ZONE, forced) is mode
