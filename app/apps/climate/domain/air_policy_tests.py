from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from apps.climate.domain.air_policy import (
    AirRegulationSettings,
    decide_air_actuation,
    decide_air_state,
    resolve_air_target,
    resolve_control_path,
)
from apps.climate.domain.floor_policy import ThermostatTarget
from apps.climate.domain.modes import AirState, ControlPath, ScheduleMode, ZoneKind
from apps.climate.domain.zone import AirTargets, FloorSetpoints, ZoneSettings

NOW = datetime(2026, 11, 2, 7, 0, tzinfo=ZoneInfo('Europe/Moscow'))
TARGET = Decimal('22.0')
NIGHT_TARGET = Decimal('19.5')
START_OFFSET = Decimal('0.3')


@pytest.fixture
def f_zone() -> ZoneSettings:
    return ZoneSettings(
        slug='kitchen',
        name='Kitchen',
        kind=ZoneKind.AIR,
        thermostat_slug='thermostat-kitchen',
        air_sensor_slug='air-kitchen',
        heating_enabled=True,
        live_control=True,
        air_control_live=True,
        setpoints=FloorSetpoints(
            comfort=Decimal('28.0'),
            night=Decimal('22.0'),
            boost=Decimal('30.0'),
            idle=Decimal('18.0'),
            limit=Decimal('29.0'),
        ),
        air_targets=AirTargets(comfort=Decimal('22.0'), night=Decimal('19.5')),
        preheat_lead=timedelta(hours=3),
        windows=(),
        boost_until=None,
        last_command_at=None,
        last_dry_run_at=None,
        last_air_dry_run_at=None,
    )


@pytest.fixture
def f_regulation() -> AirRegulationSettings:
    return AirRegulationSettings(
        start_offset=START_OFFSET,
        initial_early_stop_offset=Decimal('0.3'),
        min_band=Decimal('0.2'),
    )


@pytest.fixture
def f_boosted_zone(f_zone: ZoneSettings) -> ZoneSettings:
    return replace(f_zone, boost_until=NOW + timedelta(hours=1))


@pytest.fixture
def f_disabled_zone(f_boosted_zone: ZoneSettings) -> ZoneSettings:
    return replace(f_boosted_zone, heating_enabled=False)


@pytest.mark.parametrize(
    ('kind', 'air_sensor_slug', 'air_control_live', 'air_available', 'expected'),
    [
        (ZoneKind.AIR, 'air-kitchen', True, True, ControlPath.AIR),
        (ZoneKind.AIR, 'air-kitchen', False, True, ControlPath.AIR_DRY_RUN),
        (ZoneKind.AIR, 'air-kitchen', True, False, ControlPath.FLOOR),
        (ZoneKind.AIR, 'air-kitchen', False, False, ControlPath.FLOOR),
        (ZoneKind.AIR, None, True, True, ControlPath.FLOOR),
        (ZoneKind.FLOOR, 'air-kitchen', True, True, ControlPath.FLOOR),
    ],
    ids=[
        'live_air_zone',
        'air_zone_in_dry_run',
        'live_air_zone_with_lost_sensor',
        'dry_run_air_zone_with_lost_sensor',
        'air_zone_without_sensor',
        'floor_zone',
    ],
)
def test_control_path_is_air_only_for_an_air_zone_with_an_available_sensor(
    f_zone: ZoneSettings,
    kind: ZoneKind,
    air_sensor_slug: str | None,
    air_control_live: bool,
    air_available: bool,
    expected: ControlPath,
) -> None:
    zone = replace(f_zone, kind=kind, air_sensor_slug=air_sensor_slug, air_control_live=air_control_live)

    assert resolve_control_path(zone, air_available=air_available) is expected


@pytest.mark.parametrize(
    ('mode', 'clock_trusted', 'expected'),
    [
        (ScheduleMode.NIGHT, True, Decimal('19.5')),
        (ScheduleMode.PREHEAT, True, Decimal('22.0')),
        (ScheduleMode.COMFORT, True, Decimal('22.0')),
        (ScheduleMode.NIGHT, False, Decimal('22.0')),
    ],
)
def test_air_target_follows_schedule_mode(
    f_zone: ZoneSettings,
    mode: ScheduleMode,
    clock_trusted: bool,
    expected: Decimal,
) -> None:
    assert resolve_air_target(f_zone, mode, clock_trusted=clock_trusted) == expected


@pytest.mark.parametrize(
    ('current', 'air', 'early_stop_offset', 'expected'),
    [
        (None, Decimal('21.6'), Decimal('0.3'), AirState.HEAT),
        (None, Decimal('21.7'), Decimal('0.3'), AirState.IDLE),
        (AirState.IDLE, Decimal('21.6'), Decimal('0.1'), AirState.HEAT),
        (AirState.IDLE, Decimal('21.8'), Decimal('0.1'), AirState.IDLE),
        (AirState.HEAT, Decimal('21.8'), Decimal('0.1'), AirState.HEAT),
        (AirState.HEAT, Decimal('21.9'), Decimal('0.1'), AirState.IDLE),
        (AirState.IDLE, Decimal('21.6'), Decimal('0.5'), AirState.HEAT),
        (AirState.HEAT, Decimal('21.4'), Decimal('0.5'), AirState.HEAT),
        (AirState.HEAT, Decimal('21.5'), Decimal('0.5'), AirState.IDLE),
    ],
    ids=[
        'entering_below_start_heats',
        'entering_at_start_threshold_idles',
        'idle_below_start_heats',
        'idle_between_thresholds_stays_idle',
        'heat_between_thresholds_keeps_heating',
        'heat_stops_at_early_stop_threshold',
        'start_keeps_its_offset_when_early_stop_offset_is_larger',
        'heat_continues_below_larger_early_stop_threshold',
        'heat_stops_at_larger_early_stop_threshold',
    ],
)
def test_air_state_starts_and_stops_on_its_thresholds(
    current: AirState | None,
    air: Decimal,
    early_stop_offset: Decimal,
    expected: AirState,
) -> None:
    state = decide_air_state(
        current,
        air,
        TARGET,
        start_offset=START_OFFSET,
        early_stop_offset=early_stop_offset,
    )

    assert state is expected


@pytest.mark.parametrize(
    ('learned', 'start_offset', 'expected'),
    [
        (None, Decimal('0.3'), Decimal('0.1')),
        (Decimal('0.05'), Decimal('0.3'), Decimal('0.05')),
        (Decimal('0.45'), Decimal('0.3'), Decimal('0.1')),
        (Decimal('0.45'), Decimal('1.0'), Decimal('0.45')),
        (None, Decimal('0.1'), Decimal('-0.1')),
    ],
    ids=[
        'initial_offset_held_to_the_band',
        'smaller_learned_offset_kept',
        'larger_learned_offset_held_to_the_band',
        'wide_start_offset_leaves_the_learned_offset',
        'start_offset_narrower_than_the_band_stops_above_the_target',
    ],
)
def test_early_stop_offset_in_effect_stays_the_minimum_band_below_the_start_offset(
    f_regulation: AirRegulationSettings,
    learned: Decimal | None,
    start_offset: Decimal,
    expected: Decimal,
) -> None:
    regulation = replace(f_regulation, start_offset=start_offset)

    assert regulation.early_stop_offset(learned) == expected


@pytest.mark.parametrize('learned', [None, Decimal('0.45')], ids=['initial_offset', 'learned_offset_above_the_start'])
@pytest.mark.parametrize(
    ('current', 'air', 'expected'),
    [
        (AirState.IDLE, Decimal('19.1'), AirState.HEAT),
        (AirState.HEAT, Decimal('19.3'), AirState.HEAT),
        (AirState.HEAT, Decimal('19.4'), AirState.IDLE),
        (AirState.IDLE, Decimal('19.3'), AirState.IDLE),
    ],
    ids=['starts_below_the_band', 'keeps_heating_in_the_band', 'stops_at_the_top_of_the_band', 'idles_in_the_band'],
)
def test_air_state_keeps_the_minimum_band_whatever_the_zone_learned(
    f_regulation: AirRegulationSettings,
    learned: Decimal | None,
    current: AirState,
    air: Decimal,
    expected: AirState,
) -> None:
    state = decide_air_state(
        current,
        air,
        NIGHT_TARGET,
        start_offset=f_regulation.start_offset,
        early_stop_offset=f_regulation.early_stop_offset(learned),
    )

    assert state is expected


@pytest.mark.parametrize(
    ('state', 'expected'),
    [
        (AirState.HEAT, ThermostatTarget(power=True, setpoint=Decimal('29.0'))),
        (AirState.IDLE, ThermostatTarget(power=True, setpoint=Decimal('18.0'))),
    ],
)
def test_air_actuation_maps_heat_to_floor_limit_and_idle_to_idle_setpoint(
    f_zone: ZoneSettings,
    state: AirState,
    expected: ThermostatTarget,
) -> None:
    assert decide_air_actuation(f_zone, state, NOW) == expected


@pytest.mark.parametrize('state', [AirState.HEAT, AirState.IDLE])
def test_air_actuation_holds_boost_setpoint_while_boosted(f_boosted_zone: ZoneSettings, state: AirState) -> None:
    assert decide_air_actuation(f_boosted_zone, state, NOW) == ThermostatTarget(power=True, setpoint=Decimal('30.0'))


def test_air_actuation_turns_disabled_zone_off(f_disabled_zone: ZoneSettings) -> None:
    assert decide_air_actuation(f_disabled_zone, AirState.HEAT, NOW) == ThermostatTarget(power=False, setpoint=None)
