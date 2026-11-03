from dataclasses import replace
from decimal import Decimal

import pytest

from apps.climate.domain.backup_heat import BackupHeat, apply_backup_heat, decide_backup_heat
from apps.climate.domain.floor_policy import ThermostatTarget
from apps.climate.domain.zone import FloorSetpoints

BACKUP = BackupHeat(
    zone_slug='bathroom-2f',
    below=Decimal('19.0'),
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
def f_backup_without_a_zone() -> BackupHeat:
    return replace(BACKUP, zone_slug=None)


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
        'turns_on_below_the_threshold',
        'stays_off_at_the_threshold',
        'stays_off_within_the_hysteresis',
        'stays_on_below_the_threshold',
        'stays_on_at_the_threshold',
        'stays_on_within_the_hysteresis',
        'releases_at_the_threshold_plus_the_hysteresis',
        'lost_sensor_releases',
        'lost_sensor_keeps_off',
    ],
)
def test_backup_heat_turns_on_below_the_threshold_and_releases_above_the_hysteresis(
    active: bool,
    air: Decimal | None,
    expected: bool,
) -> None:
    assert decide_backup_heat(active, air, BACKUP) is expected


def test_backup_heat_without_a_backup_zone_stays_off(f_backup_without_a_zone: BackupHeat) -> None:
    assert decide_backup_heat(True, Decimal('17.0'), f_backup_without_a_zone) is False


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
def test_backup_heat_only_raises_its_zone_to_the_comfort_floor_setpoint(setpoint: Decimal, expected: Decimal) -> None:
    target = apply_backup_heat(ThermostatTarget(power=True, setpoint=setpoint), SETPOINTS)

    assert target == ThermostatTarget(power=True, setpoint=expected)


def test_backup_heat_leaves_a_disabled_zone_off() -> None:
    target = apply_backup_heat(ThermostatTarget(power=False, setpoint=None), SETPOINTS)

    assert target == ThermostatTarget(power=False, setpoint=None)
