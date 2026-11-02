from dataclasses import replace
from decimal import Decimal

import pytest

from apps.climate.domain.floor_policy import ThermostatTarget
from apps.climate.domain.second_floor_guard import (
    SecondFloorGuardSettings,
    apply_second_floor_guard,
    decide_second_floor_guard,
)
from apps.climate.domain.zone import FloorSetpoints

SETTINGS = SecondFloorGuardSettings(
    zone_slug='bathroom-2f',
    threshold=Decimal('19.0'),
    hysteresis=Decimal('0.3'),
    live=False,
)
SETPOINTS = FloorSetpoints(
    comfort=Decimal('28.0'),
    night=Decimal('22.0'),
    boost=Decimal('30.0'),
    idle=Decimal('18.0'),
    limit=Decimal('30.0'),
)


@pytest.fixture
def f_unguarded_settings() -> SecondFloorGuardSettings:
    return replace(SETTINGS, zone_slug=None)


@pytest.mark.parametrize(
    ('active', 'air', 'expected'),
    [
        (False, Decimal('18.9'), True),
        (False, Decimal('19.0'), False),
        (False, Decimal('19.2'), False),
        (True, Decimal('18.9'), True),
        (True, Decimal('19.0'), True),
        (True, Decimal('19.2'), True),
        (True, Decimal('19.3'), False),
        (True, None, False),
        (False, None, False),
    ],
    ids=[
        'turns_on_below_threshold',
        'stays_off_at_threshold',
        'stays_off_within_hysteresis',
        'stays_on_below_threshold',
        'stays_on_at_threshold',
        'stays_on_within_hysteresis',
        'releases_at_threshold_plus_hysteresis',
        'lost_sensor_releases',
        'lost_sensor_keeps_off',
    ],
)
def test_guard_turns_on_below_threshold_and_releases_above_hysteresis(
    active: bool,
    air: Decimal | None,
    expected: bool,
) -> None:
    assert decide_second_floor_guard(active, air, SETTINGS) is expected


def test_guard_without_guarded_zone_stays_off(f_unguarded_settings: SecondFloorGuardSettings) -> None:
    assert decide_second_floor_guard(True, Decimal('17.0'), f_unguarded_settings) is False


@pytest.mark.parametrize(
    ('setpoint', 'expected'),
    [
        (Decimal('22.0'), Decimal('28.0')),
        (Decimal('18.0'), Decimal('28.0')),
        (Decimal('28.0'), Decimal('28.0')),
        (Decimal('30.0'), Decimal('30.0')),
    ],
    ids=['raises_night', 'raises_idle', 'keeps_comfort', 'keeps_boost'],
)
def test_guard_only_raises_zone_to_comfort_floor_setpoint(setpoint: Decimal, expected: Decimal) -> None:
    target = apply_second_floor_guard(ThermostatTarget(power=True, setpoint=setpoint), SETPOINTS)

    assert target == ThermostatTarget(power=True, setpoint=expected)


def test_guard_leaves_disabled_zone_off() -> None:
    target = apply_second_floor_guard(ThermostatTarget(power=False, setpoint=None), SETPOINTS)

    assert target == ThermostatTarget(power=False, setpoint=None)
