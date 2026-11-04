from dataclasses import replace
from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from apps.climate.domain.air_plan import AirPlan, plan_air
from apps.climate.domain.air_policy import AirRegulationSettings
from apps.climate.domain.control import ClimateControl
from apps.climate.domain.floor_policy import ThermostatTarget
from apps.climate.domain.learning import HeatUpModel, ZoneLearningRecord
from apps.climate.domain.modes import AirState, ScheduleMode, ZoneKind
from apps.climate.domain.optimal_start import OptimalStartSettings, PreheatStart
from apps.climate.domain.sensors import ClimateSensors
from apps.climate.domain.zone import AirTargets, ComfortWindow, FloorSetpoints, ZoneSettings
from apps.climate.domain.zone_intent import ForcedTarget

MOSCOW = ZoneInfo('Europe/Moscow')
COMFORT_AT = datetime(2026, 11, 2, 6, 30, tzinfo=MOSCOW)
MORNING = datetime(2026, 11, 2, 7, 0, tzinfo=MOSCOW)
NIGHT = datetime(2026, 11, 2, 1, 0, tzinfo=MOSCOW)
OUTDOOR = Decimal('-3.4')
CONTROL = ClimateControl(
    sensors=ClimateSensors(outdoor_slug='outdoor'),
    regulation=AirRegulationSettings(
        start_offset=Decimal('0.3'),
        initial_early_stop_offset=Decimal('0.3'),
        min_band=Decimal('0.2'),
    ),
    optimal_start=OptimalStartSettings(lead_cap=timedelta(hours=4), mornings_needed=3),
)
UNLEARNED = ZoneLearningRecord.unlearned()
LEARNED = ZoneLearningRecord(
    early_stop_offset=None,
    heat_up=HeatUpModel(dead_time=timedelta(minutes=40), rates={-5: Decimal('0.800')}, mornings=3),
)
LEARNED_LEAD = timedelta(hours=2, minutes=40)
HEAT = ThermostatTarget(power=True, setpoint=Decimal('29.0'))
IDLE = ThermostatTarget(power=True, setpoint=Decimal('18.0'))


def _plan(
    zone: ZoneSettings,
    now: datetime,
    air: str,
    *,
    current: AirState | None = None,
    latched: PreheatStart | None = None,
    learning: ZoneLearningRecord = UNLEARNED,
    clock_trusted: bool = True,
    dry_run: bool = False,
    forced: tuple[ForcedTarget, ...] = (),
) -> AirPlan:
    return plan_air(
        zone,
        current,
        latched,
        air=Decimal(air),
        outdoor=OUTDOOR,
        learning=learning,
        control=CONTROL,
        now=now,
        clock_trusted=clock_trusted,
        dry_run=dry_run,
        forced=forced,
    )


def _over(target: str, beneath: str) -> ForcedTarget:
    return ForcedTarget(target=Decimal(target), beneath=Decimal(beneath))


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
        windows=tuple(ComfortWindow(weekday=weekday, start=time(6, 30), end=time(23, 30)) for weekday in range(7)),
        boost_until=None,
        last_command_at=None,
        last_dry_run_at=None,
        last_air_dry_run_at=None,
    )


@pytest.fixture
def f_boosted_zone(f_zone: ZoneSettings) -> ZoneSettings:
    return replace(f_zone, boost_until=MORNING + timedelta(hours=1))


@pytest.fixture
def f_latched() -> PreheatStart:
    return PreheatStart(comfort_at=COMFORT_AT, lead=LEARNED_LEAD)


def test_plan_heats_below_start_threshold_in_comfort(f_zone: ZoneSettings) -> None:
    assert _plan(f_zone, MORNING, '21.6') == AirPlan(
        air=Decimal('21.6'),
        preheat=None,
        schedule_lead=timedelta(hours=3),
        mode=ScheduleMode.COMFORT,
        target=Decimal('22.0'),
        early_stop_offset=Decimal('0.1'),
        state=AirState.HEAT,
        thermostat=HEAT,
    )


def test_plan_idles_at_night_air_target(f_zone: ZoneSettings) -> None:
    assert _plan(f_zone, NIGHT, '19.4') == AirPlan(
        air=Decimal('19.4'),
        preheat=None,
        schedule_lead=timedelta(hours=3),
        mode=ScheduleMode.NIGHT,
        target=Decimal('19.5'),
        early_stop_offset=Decimal('0.1'),
        state=AirState.IDLE,
        thermostat=IDLE,
    )


@pytest.mark.parametrize(
    ('now', 'clock_trusted', 'forced', 'air', 'expected'),
    [
        (NIGHT, True, _over('22.5', '19.5'), '19.4', (ScheduleMode.NIGHT, Decimal('22.5'), AirState.HEAT, HEAT)),
        (MORNING, True, _over('21.0', '22.0'), '21.6', (ScheduleMode.COMFORT, Decimal('21.0'), AirState.IDLE, IDLE)),
        (NIGHT, False, _over('20.0', '22.0'), '21.0', (ScheduleMode.NIGHT, Decimal('20.0'), AirState.IDLE, IDLE)),
        (
            COMFORT_AT - timedelta(hours=3),
            True,
            _over('20.0', '19.5'),
            '20.4',
            (ScheduleMode.PREHEAT, Decimal('22.0'), AirState.HEAT, HEAT),
        ),
    ],
    ids=[
        'above_the_night_target',
        'below_the_comfort_target',
        'on_an_untrusted_clock',
        'step_up_from_the_setback_keeps_the_preheat_target',
    ],
)
def test_forced_target_replaces_the_scheduled_target_it_moves_the_room_s_way(
    f_zone: ZoneSettings,
    now: datetime,
    clock_trusted: bool,
    forced: ForcedTarget,
    air: str,
    expected: tuple[ScheduleMode, Decimal, AirState, ThermostatTarget],
) -> None:
    plan = _plan(f_zone, now, air, clock_trusted=clock_trusted, forced=(forced,))

    assert (plan.mode, plan.target, plan.state, plan.thermostat) == expected


def test_plan_holds_comfort_air_target_and_held_preheat_while_clock_untrusted(
    f_zone: ZoneSettings,
    f_latched: PreheatStart,
) -> None:
    plan = _plan(f_zone, NIGHT, '21.0', latched=f_latched, clock_trusted=False)

    assert (plan.preheat, plan.target, plan.state) == (f_latched, Decimal('22.0'), AirState.HEAT)


def test_plan_holds_no_new_preheat_while_clock_untrusted(f_zone: ZoneSettings) -> None:
    plan = _plan(f_zone, COMFORT_AT - LEARNED_LEAD, '20.4', learning=LEARNED, clock_trusted=False)

    assert plan.preheat is None


@pytest.mark.parametrize(
    ('now', 'mode', 'preheat'),
    [
        (COMFORT_AT - timedelta(hours=3, minutes=1), ScheduleMode.NIGHT, None),
        (
            COMFORT_AT - timedelta(hours=3),
            ScheduleMode.PREHEAT,
            PreheatStart(comfort_at=COMFORT_AT, lead=timedelta(hours=3)),
        ),
    ],
    ids=['before_fixed_lead', 'at_fixed_lead'],
)
def test_unlearned_zone_preheats_on_its_fixed_lead(
    f_zone: ZoneSettings,
    now: datetime,
    mode: ScheduleMode,
    preheat: PreheatStart | None,
) -> None:
    plan = _plan(f_zone, now, '20.4')

    assert (plan.mode, plan.preheat) == (mode, preheat)


@pytest.mark.parametrize(
    ('now', 'mode', 'preheat', 'state'),
    [
        (COMFORT_AT - LEARNED_LEAD - timedelta(minutes=1), ScheduleMode.NIGHT, None, AirState.IDLE),
        (
            COMFORT_AT - LEARNED_LEAD,
            ScheduleMode.PREHEAT,
            PreheatStart(comfort_at=COMFORT_AT, lead=LEARNED_LEAD),
            AirState.HEAT,
        ),
    ],
    ids=['before_optimal_start', 'at_optimal_start'],
)
def test_learned_zone_preheats_on_optimal_lead(
    f_zone: ZoneSettings,
    now: datetime,
    mode: ScheduleMode,
    preheat: PreheatStart | None,
    state: AirState,
) -> None:
    plan = _plan(f_zone, now, '20.4', learning=LEARNED)

    assert (plan.mode, plan.preheat, plan.lead, plan.state) == (
        mode,
        preheat,
        None if preheat is None else LEARNED_LEAD,
        state,
    )


@pytest.mark.parametrize(
    ('dry_run', 'preheat'),
    [(False, None), (True, PreheatStart(comfort_at=COMFORT_AT, lead=LEARNED_LEAD))],
    ids=['air_mode_waits_for_optimal_start', 'dry_run_holds_optimal_start_from_floor_mode_preheat'],
)
def test_dry_run_plan_holds_later_optimal_start_once_floor_mode_preheats(
    f_zone: ZoneSettings,
    dry_run: bool,
    preheat: PreheatStart | None,
) -> None:
    plan = _plan(f_zone, COMFORT_AT - timedelta(hours=3), '20.4', learning=LEARNED, dry_run=dry_run)

    assert (plan.mode, plan.preheat, plan.lead, plan.state) == (ScheduleMode.NIGHT, preheat, None, AirState.IDLE)


def test_latched_preheat_holds_while_room_warms(f_zone: ZoneSettings, f_latched: PreheatStart) -> None:
    plan = _plan(
        f_zone,
        COMFORT_AT - timedelta(hours=1, minutes=20),
        '21.6',
        current=AirState.HEAT,
        latched=f_latched,
        learning=LEARNED,
    )

    assert (plan.mode, plan.preheat, plan.schedule_lead, plan.target, plan.state) == (
        ScheduleMode.PREHEAT,
        f_latched,
        LEARNED_LEAD,
        Decimal('22.0'),
        AirState.HEAT,
    )


@pytest.mark.parametrize(
    ('learning', 'schedule_lead'),
    [(UNLEARNED, timedelta(hours=3)), (LEARNED, LEARNED_LEAD)],
    ids=['fixed_lead', 'optimal_lead'],
)
def test_plan_schedules_the_night_on_the_lead_of_the_minute(
    f_zone: ZoneSettings,
    learning: ZoneLearningRecord,
    schedule_lead: timedelta,
) -> None:
    plan = _plan(f_zone, NIGHT, '20.4', learning=learning)

    assert (plan.mode, plan.preheat, plan.schedule_lead) == (ScheduleMode.NIGHT, None, schedule_lead)


@pytest.mark.parametrize(
    ('learning', 'early_stop_offset', 'state'),
    [
        (UNLEARNED, Decimal('0.1'), AirState.IDLE),
        (replace(UNLEARNED, early_stop_offset=Decimal('0.05')), Decimal('0.05'), AirState.HEAT),
        (replace(UNLEARNED, early_stop_offset=Decimal('0.45')), Decimal('0.1'), AirState.IDLE),
    ],
    ids=[
        'initial_offset_held_to_the_band_stops',
        'learned_smaller_offset_keeps_heating',
        'learned_larger_offset_held_to_the_band_stops',
    ],
)
def test_plan_stops_heat_on_learned_early_stop_offset_held_to_the_minimum_band(
    f_zone: ZoneSettings,
    learning: ZoneLearningRecord,
    early_stop_offset: Decimal,
    state: AirState,
) -> None:
    plan = _plan(f_zone, MORNING, '21.9', current=AirState.HEAT, learning=learning)

    assert (plan.early_stop_offset, plan.state) == (early_stop_offset, state)


def test_boosted_plan_holds_boost_setpoint(f_boosted_zone: ZoneSettings) -> None:
    plan = _plan(f_boosted_zone, MORNING, '22.5')

    assert (plan.state, plan.thermostat) == (AirState.IDLE, ThermostatTarget(power=True, setpoint=Decimal('30.0')))
