from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from apps.climate.domain.floor_policy import ThermostatTarget, decide_floor_target
from apps.climate.domain.modes import ScheduleMode, ZoneKind
from apps.climate.domain.zone import FloorSetpoints, ZoneSettings

NOW = datetime(2026, 10, 29, 2, 0, tzinfo=ZoneInfo('Europe/Moscow'))


@pytest.fixture
def f_zone() -> ZoneSettings:
    return ZoneSettings(
        slug='kitchen',
        name='Kitchen',
        kind=ZoneKind.AIR,
        thermostat_slug='thermostat-kitchen',
        heating_enabled=True,
        live_control=True,
        setpoints=FloorSetpoints(comfort=Decimal('28.0'), night=Decimal('22.0'), boost=Decimal('30.0')),
        preheat_lead=timedelta(hours=3),
        windows=(),
        boost_until=None,
        last_command_at=None,
    )


@pytest.fixture
def f_boosted_zone(f_zone: ZoneSettings) -> ZoneSettings:
    return replace(f_zone, boost_until=NOW + timedelta(hours=1))


@pytest.fixture
def f_expired_boost_zone(f_zone: ZoneSettings) -> ZoneSettings:
    return replace(f_zone, boost_until=NOW)


@pytest.fixture
def f_disabled_zone(f_zone: ZoneSettings) -> ZoneSettings:
    return replace(f_zone, heating_enabled=False)


@pytest.mark.parametrize(
    ('mode', 'clock_trusted', 'expected'),
    [
        (ScheduleMode.NIGHT, True, Decimal('22.0')),
        (ScheduleMode.PREHEAT, True, Decimal('28.0')),
        (ScheduleMode.COMFORT, True, Decimal('28.0')),
        (ScheduleMode.NIGHT, False, Decimal('28.0')),
    ],
)
def test_floor_target_follows_schedule_mode(
    f_zone: ZoneSettings,
    mode: ScheduleMode,
    clock_trusted: bool,
    expected: Decimal,
) -> None:
    target = decide_floor_target(f_zone, mode, NOW, clock_trusted=clock_trusted)

    assert target == ThermostatTarget(power=True, setpoint=expected)


def test_boost_overrides_night(f_boosted_zone: ZoneSettings) -> None:
    target = decide_floor_target(f_boosted_zone, ScheduleMode.NIGHT, NOW, clock_trusted=True)

    assert target == ThermostatTarget(power=True, setpoint=Decimal('30.0'))


def test_expired_boost_is_ignored(f_expired_boost_zone: ZoneSettings) -> None:
    target = decide_floor_target(f_expired_boost_zone, ScheduleMode.NIGHT, NOW, clock_trusted=True)

    assert target == ThermostatTarget(power=True, setpoint=Decimal('22.0'))


def test_disabled_zone_turns_thermostat_off(f_disabled_zone: ZoneSettings) -> None:
    target = decide_floor_target(f_disabled_zone, ScheduleMode.COMFORT, NOW, clock_trusted=True)

    assert target == ThermostatTarget(power=False, setpoint=None)
