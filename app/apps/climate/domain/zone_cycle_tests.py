from dataclasses import replace
from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from apps.climate.domain.air_policy import AirRegulationSettings
from apps.climate.domain.control import ClimateControl
from apps.climate.domain.events import AirControlDryRun, OptimalStartDryRun
from apps.climate.domain.floor_policy import ThermostatTarget
from apps.climate.domain.learning import AirSample, HeatUpModel, Morning, ZoneLearningRecord
from apps.climate.domain.memory import ZoneMemory
from apps.climate.domain.modes import AirState, ControlPath, ScheduleMode, ZoneCycleOutcome, ZoneKind
from apps.climate.domain.observation import StopWatch
from apps.climate.domain.optimal_start import OptimalStartSettings, PreheatStart
from apps.climate.domain.sensors import ClimateSensors
from apps.climate.domain.zone import AirTargets, ComfortWindow, FloorSetpoints, ZoneSettings
from apps.climate.domain.zone_cycle import (
    CycleConditions,
    ZoneConditions,
    ZoneDecision,
    ZoneFollowUp,
    decide_zone,
    follow_zone,
    hold_air_dry_run,
)
from apps.climate.domain.zone_intent import NO_INTENT, ForcedTarget, ZoneIntent

MOSCOW = ZoneInfo('Europe/Moscow')
COMFORT_AT = datetime(2026, 11, 2, 6, 30, tzinfo=MOSCOW)
FIXED_START = datetime(2026, 11, 2, 3, 30, tzinfo=MOSCOW)
OPTIMAL_START = datetime(2026, 11, 2, 3, 50, tzinfo=MOSCOW)
MORNING = datetime(2026, 11, 2, 7, 0, tzinfo=MOSCOW)
AIR_MODE_STOP = datetime(2026, 11, 2, 5, 40, tzinfo=MOSCOW)
NIGHT = datetime(2026, 11, 2, 1, 0, tzinfo=MOSCOW)
PREHEAT_WRITE = datetime(2026, 11, 2, 2, 40, tzinfo=MOSCOW)
SENSOR_BACK = datetime(2026, 11, 2, 2, 46, tzinfo=MOSCOW)
OUTDOOR = Decimal('-3.4')
TARGET = Decimal('22.0')
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
SLOW_LEARNED = ZoneLearningRecord(
    early_stop_offset=None,
    heat_up=HeatUpModel(dead_time=timedelta(minutes=40), rates={-5: Decimal('0.500')}, mornings=3),
)
COMFORT = ThermostatTarget(power=True, setpoint=Decimal('28.0'))
HEAT = ThermostatTarget(power=True, setpoint=Decimal('29.0'))
IDLE = ThermostatTarget(power=True, setpoint=Decimal('18.0'))
BOOST = ThermostatTarget(power=True, setpoint=Decimal('30.0'))


def _cycle(now: datetime, *, clock_trusted: bool = True) -> CycleConditions:
    return CycleConditions(now=now, clock_trusted=clock_trusted, control=CONTROL, outdoor=OUTDOOR)


def _conditions(
    air: str | None,
    *,
    learning: ZoneLearningRecord = UNLEARNED,
    intent: ZoneIntent = NO_INTENT,
    writes_live: bool = True,
) -> ZoneConditions:
    return ZoneConditions(
        air=None if air is None else Decimal(air),
        learning=learning,
        intent=intent,
        writes_live=writes_live,
    )


def _forced(target: str, *, beneath: str = '22.0', backup: bool = False) -> ZoneIntent:
    return ZoneIntent(
        forced=(ForcedTarget(target=Decimal(target), beneath=Decimal(beneath)),),
        backup=backup,
        learns=False,
    )


def _follow(
    zone: ZoneSettings,
    memory: ZoneMemory,
    conditions: ZoneConditions,
    cycle: CycleConditions,
    outcome: ZoneCycleOutcome,
) -> ZoneFollowUp:
    return follow_zone(zone, memory, decide_zone(zone, memory, conditions, cycle), outcome, conditions, cycle)


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
        air_control_live=False,
        setpoints=FloorSetpoints(
            comfort=Decimal('28.0'),
            night=Decimal('22.0'),
            boost=Decimal('30.0'),
            idle=Decimal('18.0'),
            limit=Decimal('29.0'),
        ),
        air_targets=AirTargets(comfort=TARGET, night=Decimal('19.5')),
        preheat_lead=timedelta(hours=3),
        windows=tuple(ComfortWindow(weekday=weekday, start=time(6, 30), end=time(23, 30)) for weekday in range(7)),
        boost_until=None,
        last_command_at=None,
        last_dry_run_at=None,
        last_air_dry_run_at=None,
    )


@pytest.fixture
def f_live_zone(f_zone: ZoneSettings) -> ZoneSettings:
    return replace(f_zone, air_control_live=True)


@pytest.fixture
def f_boosted_zone(f_zone: ZoneSettings) -> ZoneSettings:
    return replace(f_zone, boost_until=MORNING + timedelta(hours=1))


@pytest.fixture
def f_floor_zone(f_zone: ZoneSettings) -> ZoneSettings:
    return replace(f_zone, slug='bathroom-2f', kind=ZoneKind.FLOOR, air_sensor_slug=None)


@pytest.fixture
def f_heating_memory() -> ZoneMemory:
    return ZoneMemory(path=ControlPath.AIR, mode=ScheduleMode.COMFORT, air_state=AirState.HEAT, heat_target=TARGET)


@pytest.fixture
def f_journaled_memory() -> ZoneMemory:
    return ZoneMemory(
        path=ControlPath.AIR_DRY_RUN,
        mode=ScheduleMode.COMFORT,
        air_state=AirState.HEAT,
        would_be_target=HEAT,
    )


@pytest.fixture
def f_boost_journaled_memory(f_journaled_memory: ZoneMemory) -> ZoneMemory:
    return replace(f_journaled_memory, would_be_target=BOOST)


@pytest.fixture
def f_idle_under_boost_memory(f_boost_journaled_memory: ZoneMemory) -> ZoneMemory:
    return replace(f_boost_journaled_memory, air_state=AirState.IDLE)


@pytest.fixture
def f_boost_ended_zone(f_zone: ZoneSettings) -> ZoneSettings:
    return replace(f_zone, boost_until=MORNING, last_air_dry_run_at=MORNING - timedelta(hours=2))


@pytest.fixture
def f_watched_memory() -> ZoneMemory:
    return ZoneMemory(
        path=ControlPath.AIR,
        mode=ScheduleMode.COMFORT,
        air_state=AirState.IDLE,
        heat_target=TARGET,
        stop=StopWatch(stopped_at=MORNING, target=TARGET, offset=Decimal('0.1'), peak=Decimal('22.3')),
    )


@pytest.fixture
def f_latched_memory() -> ZoneMemory:
    return ZoneMemory(
        path=ControlPath.AIR_DRY_RUN,
        mode=ScheduleMode.NIGHT,
        air_state=AirState.IDLE,
        would_be_target=IDLE,
        preheat=PreheatStart(comfort_at=COMFORT_AT, lead=timedelta(hours=2, minutes=40)),
    )


@pytest.fixture
def f_preheating_memory() -> ZoneMemory:
    return ZoneMemory(
        path=ControlPath.AIR,
        mode=ScheduleMode.PREHEAT,
        air_state=AirState.HEAT,
        heat_target=TARGET,
        preheat=PreheatStart(comfort_at=COMFORT_AT, lead=timedelta(hours=2, minutes=40)),
    )


@pytest.fixture
def f_held_back_preheat_memory() -> ZoneMemory:
    return ZoneMemory(
        path=ControlPath.AIR,
        mode=ScheduleMode.PREHEAT,
        air_state=AirState.IDLE,
        preheat=PreheatStart(comfort_at=COMFORT_AT, lead=timedelta(hours=4)),
    )


@pytest.fixture
def f_morning() -> Morning:
    return Morning(
        comfort_at=COMFORT_AT,
        target=TARGET,
        outdoor=OUTDOOR,
        samples=(AirSample(at=FIXED_START, air=Decimal('20.0')),),
    )


@pytest.fixture
def f_preheating_morning_memory(f_preheating_memory: ZoneMemory, f_morning: Morning) -> ZoneMemory:
    return replace(f_preheating_memory, morning=f_morning)


def test_zone_without_air_reading_runs_floor_mode(f_live_zone: ZoneSettings) -> None:
    decision = decide_zone(f_live_zone, ZoneMemory(), _conditions(None), _cycle(MORNING))

    assert decision == ZoneDecision(path=ControlPath.FLOOR, plan=None, mode=ScheduleMode.COMFORT, target=COMFORT)


def test_air_zone_in_dry_run_keeps_floor_mode_on_its_fixed_lead(f_zone: ZoneSettings) -> None:
    decision = decide_zone(f_zone, ZoneMemory(), _conditions('20.4', learning=LEARNED), _cycle(FIXED_START))

    would_be_mode = None if decision.plan is None else decision.plan.mode
    assert (decision.path, decision.mode, decision.target, would_be_mode) == (
        ControlPath.AIR_DRY_RUN,
        ScheduleMode.PREHEAT,
        COMFORT,
        ScheduleMode.NIGHT,
    )


def test_live_air_zone_drives_air_actuation(f_live_zone: ZoneSettings) -> None:
    decision = decide_zone(f_live_zone, ZoneMemory(), _conditions('21.5'), _cycle(MORNING))

    assert (decision.path, decision.mode, decision.target) == (ControlPath.AIR, ScheduleMode.COMFORT, HEAT)


@pytest.mark.parametrize(
    ('intent', 'target'),
    [
        (ZoneIntent(forced=(), backup=True, learns=True), COMFORT),
        (NO_INTENT, ThermostatTarget(power=True, setpoint=Decimal('22.0'))),
    ],
    ids=['backup_heat_raises_night_to_comfort', 'without_backup_heat_night_stays'],
)
def test_backup_heat_raises_its_zone_to_its_comfort_floor_setpoint(
    f_floor_zone: ZoneSettings,
    intent: ZoneIntent,
    target: ThermostatTarget,
) -> None:
    decision = decide_zone(f_floor_zone, ZoneMemory(), _conditions(None, intent=intent), _cycle(NIGHT))

    assert decision.target == target


@pytest.mark.parametrize(
    ('intent', 'target'),
    [
        (_forced('21.0', backup=True), COMFORT),
        (_forced('21.0'), ThermostatTarget(power=True, setpoint=Decimal('22.0'))),
    ],
    ids=['backup_heat_raises_it_back_to_comfort', 'without_backup_heat_it_stays_at_night'],
)
def test_backup_raises_a_floor_an_override_stepped_down(
    f_floor_zone: ZoneSettings,
    intent: ZoneIntent,
    target: ThermostatTarget,
) -> None:
    decision = decide_zone(f_floor_zone, ZoneMemory(), _conditions(None, intent=intent), _cycle(MORNING))

    assert (decision.mode, decision.target) == (ScheduleMode.NIGHT, target)


@pytest.mark.parametrize(
    ('air', 'forced', 'expected'),
    [
        ('21.9', '23.0', (ScheduleMode.COMFORT, Decimal('23.0'), AirState.HEAT, HEAT)),
        ('19.4', '19.0', (ScheduleMode.NIGHT, Decimal('19.0'), AirState.IDLE, IDLE)),
    ],
    ids=['above_the_schedule_heats_in_comfort', 'below_the_night_target_idles_at_night'],
)
def test_forced_target_drives_air_mode_and_its_mode(
    f_live_zone: ZoneSettings,
    air: str,
    forced: str,
    expected: tuple[ScheduleMode, Decimal, AirState, ThermostatTarget],
) -> None:
    decision = decide_zone(f_live_zone, ZoneMemory(), _conditions(air, intent=_forced(forced)), _cycle(MORNING))

    plan = decision.plan
    assert (
        decision.path,
        (decision.mode, None if plan is None else plan.target, None if plan is None else plan.state, decision.target),
    ) == (ControlPath.AIR, expected)


def test_forced_target_reaches_air_mode_in_dry_run_while_floor_mode_drives_the_thermostat(
    f_zone: ZoneSettings,
) -> None:
    decision = decide_zone(f_zone, ZoneMemory(), _conditions('21.9', intent=_forced('23.0')), _cycle(MORNING))

    plan = decision.plan
    assert (
        decision.path,
        decision.mode,
        decision.target,
        None if plan is None else (plan.target, plan.state, plan.thermostat),
    ) == (ControlPath.AIR_DRY_RUN, ScheduleMode.COMFORT, BOOST, (Decimal('23.0'), AirState.HEAT, HEAT))


def test_forced_target_withholds_the_morning_a_preheat_would_start(f_zone: ZoneSettings) -> None:
    memory = ZoneMemory(path=ControlPath.AIR_DRY_RUN, mode=ScheduleMode.NIGHT)

    follow_up = _follow(
        f_zone,
        memory,
        _conditions('20.0', intent=_forced('22.0', beneath='19.5')),
        _cycle(FIXED_START),
        ZoneCycleOutcome.APPLIED,
    )

    assert (follow_up.memory.mode, follow_up.memory.morning) == (ScheduleMode.PREHEAT, None)


def test_forced_target_drops_a_morning_in_progress(f_zone: ZoneSettings, f_morning: Morning) -> None:
    memory = ZoneMemory(path=ControlPath.AIR_DRY_RUN, mode=ScheduleMode.PREHEAT, morning=f_morning)

    follow_up = _follow(
        f_zone,
        memory,
        _conditions('20.4', intent=_forced('22.0', beneath='19.5')),
        _cycle(FIXED_START + timedelta(hours=1)),
        ZoneCycleOutcome.UNCHANGED,
    )

    assert (follow_up.morning, follow_up.memory.morning) == (None, None)


def test_forced_target_starts_no_early_stop_watch(f_live_zone: ZoneSettings, f_heating_memory: ZoneMemory) -> None:
    follow_up = _follow(
        f_live_zone,
        f_heating_memory,
        _conditions('21.9', intent=_forced('22.0')),
        _cycle(MORNING),
        ZoneCycleOutcome.APPLIED,
    )

    assert (follow_up.memory.air_state, follow_up.memory.stop, follow_up.stop) == (AirState.IDLE, None, None)


def test_forced_target_drops_an_early_stop_watch(f_live_zone: ZoneSettings, f_watched_memory: ZoneMemory) -> None:
    follow_up = _follow(
        f_live_zone,
        f_watched_memory,
        _conditions('21.9', intent=_forced('22.0')),
        _cycle(MORNING + timedelta(minutes=30)),
        ZoneCycleOutcome.UNCHANGED,
    )

    assert (follow_up.memory.stop, follow_up.stop) == (None, None)


@pytest.mark.parametrize(
    ('outcome', 'state'),
    [
        (ZoneCycleOutcome.APPLIED, AirState.HEAT),
        (ZoneCycleOutcome.UNCHANGED, AirState.HEAT),
        (ZoneCycleOutcome.DRY_RUN, AirState.HEAT),
        (ZoneCycleOutcome.THROTTLED, None),
        (ZoneCycleOutcome.WAITING, None),
        (ZoneCycleOutcome.FAILED, None),
        (ZoneCycleOutcome.DEVICE_UNAVAILABLE, None),
    ],
)
def test_live_air_state_follows_only_a_thermostat_that_follows(
    f_live_zone: ZoneSettings,
    outcome: ZoneCycleOutcome,
    state: AirState | None,
) -> None:
    follow_up = _follow(f_live_zone, ZoneMemory(), _conditions('21.5'), _cycle(MORNING), outcome)

    assert (follow_up.memory.path, follow_up.memory.air_state, follow_up.memory.heat_target) == (
        ControlPath.AIR,
        state,
        TARGET,
    )


def test_applied_stop_of_heat_starts_stop_watch(f_live_zone: ZoneSettings, f_heating_memory: ZoneMemory) -> None:
    follow_up = _follow(
        f_live_zone,
        f_heating_memory,
        _conditions('21.9'),
        _cycle(MORNING),
        ZoneCycleOutcome.APPLIED,
    )

    assert (follow_up.memory.air_state, follow_up.memory.stop) == (
        AirState.IDLE,
        StopWatch(stopped_at=MORNING, target=TARGET, offset=Decimal('0.1'), peak=Decimal('21.9')),
    )


def test_stop_written_in_dry_run_starts_no_stop_watch(f_live_zone: ZoneSettings, f_heating_memory: ZoneMemory) -> None:
    follow_up = _follow(
        f_live_zone,
        f_heating_memory,
        _conditions('21.9', writes_live=False),
        _cycle(MORNING),
        ZoneCycleOutcome.DRY_RUN,
    )

    assert (follow_up.memory.air_state, follow_up.memory.stop) == (AirState.IDLE, None)


def test_stop_watch_hands_over_its_peak_two_hours_after_stop(
    f_live_zone: ZoneSettings,
    f_watched_memory: ZoneMemory,
) -> None:
    follow_up = _follow(
        f_live_zone,
        f_watched_memory,
        _conditions('21.9'),
        _cycle(MORNING + timedelta(hours=2, minutes=1)),
        ZoneCycleOutcome.UNCHANGED,
    )

    assert (follow_up.stop, follow_up.memory.stop) == (
        StopWatch(stopped_at=MORNING, target=TARGET, offset=Decimal('0.1'), peak=Decimal('22.3')),
        None,
    )


def test_air_dry_run_journals_first_decision(f_zone: ZoneSettings) -> None:
    follow_up = _follow(f_zone, ZoneMemory(), _conditions('21.5'), _cycle(MORNING), ZoneCycleOutcome.UNCHANGED)

    assert (follow_up.air_dry_run, follow_up.memory.would_be_target, follow_up.memory.air_state) == (
        AirControlDryRun(
            zone='kitchen',
            air=Decimal('21.5'),
            target=TARGET,
            mode=ScheduleMode.COMFORT,
            state=AirState.HEAT,
            previous_state=None,
            power=True,
            setpoint=Decimal('29.0'),
            start_offset=Decimal('0.3'),
            early_stop_offset=Decimal('0.1'),
            lead_minutes=None,
            occurred_at=MORNING,
        ),
        HEAT,
        AirState.HEAT,
    )


def test_air_dry_run_journals_would_be_thermostat_change_beside_state_it_keeps(
    f_boosted_zone: ZoneSettings,
    f_journaled_memory: ZoneMemory,
) -> None:
    follow_up = _follow(
        f_boosted_zone,
        f_journaled_memory,
        _conditions('21.5'),
        _cycle(MORNING),
        ZoneCycleOutcome.APPLIED,
    )

    assert follow_up.air_dry_run == AirControlDryRun(
        zone='kitchen',
        air=Decimal('21.5'),
        target=TARGET,
        mode=ScheduleMode.COMFORT,
        state=AirState.HEAT,
        previous_state=AirState.HEAT,
        power=True,
        setpoint=Decimal('30.0'),
        start_offset=Decimal('0.3'),
        early_stop_offset=Decimal('0.1'),
        lead_minutes=None,
        occurred_at=MORNING,
    )


def test_air_dry_run_journals_nothing_while_decision_holds(
    f_zone: ZoneSettings,
    f_journaled_memory: ZoneMemory,
) -> None:
    follow_up = _follow(
        f_zone,
        f_journaled_memory,
        _conditions('21.6'),
        _cycle(MORNING),
        ZoneCycleOutcome.UNCHANGED,
    )

    assert (follow_up.air_dry_run, follow_up.memory.would_be_target) == (None, HEAT)


@pytest.mark.parametrize(
    ('since_journaled', 'state'),
    [(timedelta(minutes=10), AirState.HEAT), (timedelta(minutes=30), AirState.IDLE)],
    ids=['within_thirty_minutes', 'after_thirty_minutes'],
)
def test_air_dry_run_change_waits_thirty_minutes_after_last_one(
    f_zone: ZoneSettings,
    f_journaled_memory: ZoneMemory,
    since_journaled: timedelta,
    state: AirState,
) -> None:
    zone = replace(f_zone, last_air_dry_run_at=MORNING - since_journaled)

    follow_up = _follow(zone, f_journaled_memory, _conditions('22.0'), _cycle(MORNING), ZoneCycleOutcome.UNCHANGED)

    assert (follow_up.memory.air_state, follow_up.air_dry_run is not None) == (state, state is AirState.IDLE)


@pytest.fixture
def f_idle_journaled_follow_up(f_zone: ZoneSettings, f_journaled_memory: ZoneMemory) -> ZoneFollowUp:
    return _follow(f_zone, f_journaled_memory, _conditions('22.0'), _cycle(MORNING), ZoneCycleOutcome.UNCHANGED)


def test_held_air_dry_run_change_drops_its_entry_and_keeps_what_the_zone_followed(
    f_idle_journaled_follow_up: ZoneFollowUp,
    f_journaled_memory: ZoneMemory,
) -> None:
    held = hold_air_dry_run(f_idle_journaled_follow_up, f_journaled_memory)

    assert (f_idle_journaled_follow_up.air_dry_run is None, held.air_dry_run, held.memory) == (
        False,
        None,
        f_journaled_memory,
    )


def test_air_dry_run_change_waits_for_available_thermostat(
    f_zone: ZoneSettings,
    f_journaled_memory: ZoneMemory,
) -> None:
    follow_up = _follow(
        f_zone,
        f_journaled_memory,
        _conditions('22.0'),
        _cycle(MORNING),
        ZoneCycleOutcome.DEVICE_UNAVAILABLE,
    )

    assert (follow_up.air_dry_run, follow_up.memory.air_state) == (None, AirState.HEAT)


@pytest.mark.parametrize(
    ('outcome', 'state'),
    [(ZoneCycleOutcome.UNCHANGED, AirState.IDLE), (ZoneCycleOutcome.DEVICE_UNAVAILABLE, AirState.HEAT)],
    ids=['thermostat_available', 'thermostat_unavailable'],
)
def test_air_dry_run_state_change_that_keeps_boost_target_is_not_journaled(
    f_boosted_zone: ZoneSettings,
    f_boost_journaled_memory: ZoneMemory,
    outcome: ZoneCycleOutcome,
    state: AirState,
) -> None:
    follow_up = _follow(f_boosted_zone, f_boost_journaled_memory, _conditions('21.9'), _cycle(MORNING), outcome)

    assert (follow_up.air_dry_run, follow_up.memory.air_state, follow_up.memory.would_be_target) == (
        None,
        state,
        BOOST,
    )


def test_air_dry_run_journals_idle_setpoint_of_state_it_follows_once_boost_ends(
    f_boost_ended_zone: ZoneSettings,
    f_idle_under_boost_memory: ZoneMemory,
) -> None:
    follow_up = _follow(
        f_boost_ended_zone,
        f_idle_under_boost_memory,
        _conditions('21.8'),
        _cycle(MORNING),
        ZoneCycleOutcome.APPLIED,
    )

    assert (follow_up.air_dry_run, follow_up.memory.would_be_target) == (
        AirControlDryRun(
            zone='kitchen',
            air=Decimal('21.8'),
            target=TARGET,
            mode=ScheduleMode.COMFORT,
            state=AirState.IDLE,
            previous_state=AirState.IDLE,
            power=True,
            setpoint=Decimal('18.0'),
            start_offset=Decimal('0.3'),
            early_stop_offset=Decimal('0.1'),
            lead_minutes=None,
            occurred_at=MORNING,
        ),
        IDLE,
    )


@pytest.fixture
def f_floor_mode_memory(f_zone: ZoneSettings, f_journaled_memory: ZoneMemory) -> ZoneMemory:
    return _follow(f_zone, f_journaled_memory, _conditions(None), _cycle(MORNING), ZoneCycleOutcome.APPLIED).memory


@pytest.fixture
def f_boosted_floor_mode_memory(f_boosted_zone: ZoneSettings, f_journaled_memory: ZoneMemory) -> ZoneMemory:
    return _follow(
        f_boosted_zone,
        f_journaled_memory,
        _conditions(None),
        _cycle(MORNING),
        ZoneCycleOutcome.APPLIED,
    ).memory


@pytest.mark.parametrize(
    ('outcome', 'would_be_target'),
    [
        (ZoneCycleOutcome.APPLIED, COMFORT),
        (ZoneCycleOutcome.UNCHANGED, COMFORT),
        (ZoneCycleOutcome.DRY_RUN, COMFORT),
        (ZoneCycleOutcome.THROTTLED, None),
        (ZoneCycleOutcome.WAITING, None),
        (ZoneCycleOutcome.DEVICE_UNAVAILABLE, None),
        (ZoneCycleOutcome.FAILED, None),
        (ZoneCycleOutcome.SHED, None),
    ],
)
def test_zone_leaving_air_dry_run_takes_what_floor_mode_wrote_as_its_would_be_target(
    f_zone: ZoneSettings,
    f_journaled_memory: ZoneMemory,
    outcome: ZoneCycleOutcome,
    would_be_target: ThermostatTarget | None,
) -> None:
    follow_up = _follow(f_zone, f_journaled_memory, _conditions(None), _cycle(MORNING), outcome)

    assert (follow_up.memory.path, follow_up.memory.would_be_target) == (ControlPath.FLOOR, would_be_target)


def test_floor_zone_keeps_no_would_be_target(f_floor_zone: ZoneSettings) -> None:
    follow_up = _follow(f_floor_zone, ZoneMemory(), _conditions(None), _cycle(MORNING), ZoneCycleOutcome.APPLIED)

    assert follow_up.memory == ZoneMemory(path=ControlPath.FLOOR, mode=ScheduleMode.COMFORT)


def test_air_dry_run_back_from_floor_mode_journals_the_change_from_what_floor_mode_wrote(
    f_zone: ZoneSettings,
    f_floor_mode_memory: ZoneMemory,
) -> None:
    now = MORNING + timedelta(minutes=1)

    follow_up = _follow(f_zone, f_floor_mode_memory, _conditions('21.5'), _cycle(now), ZoneCycleOutcome.UNCHANGED)

    assert (follow_up.air_dry_run, follow_up.memory.would_be_target, follow_up.memory.air_state) == (
        AirControlDryRun(
            zone='kitchen',
            air=Decimal('21.5'),
            target=TARGET,
            mode=ScheduleMode.COMFORT,
            state=AirState.HEAT,
            previous_state=None,
            power=True,
            setpoint=Decimal('29.0'),
            start_offset=Decimal('0.3'),
            early_stop_offset=Decimal('0.1'),
            lead_minutes=None,
            occurred_at=now,
        ),
        HEAT,
        AirState.HEAT,
    )


def test_air_dry_run_back_from_floor_mode_journals_nothing_floor_mode_already_wrote(
    f_boosted_zone: ZoneSettings,
    f_boosted_floor_mode_memory: ZoneMemory,
) -> None:
    follow_up = _follow(
        f_boosted_zone,
        f_boosted_floor_mode_memory,
        _conditions('21.5'),
        _cycle(MORNING + timedelta(minutes=1)),
        ZoneCycleOutcome.UNCHANGED,
    )

    assert (follow_up.air_dry_run, follow_up.memory.would_be_target, follow_up.memory.air_state) == (
        None,
        BOOST,
        AirState.HEAT,
    )


def test_air_dry_run_journals_optimal_start_when_preheat_would_begin(f_zone: ZoneSettings) -> None:
    memory = ZoneMemory(path=ControlPath.AIR_DRY_RUN, mode=ScheduleMode.PREHEAT)

    follow_up = _follow(
        f_zone,
        memory,
        _conditions('20.4', learning=LEARNED),
        _cycle(OPTIMAL_START),
        ZoneCycleOutcome.UNCHANGED,
    )

    assert (follow_up.optimal_start, follow_up.memory.preheat) == (
        OptimalStartDryRun(
            zone='kitchen',
            lead_minutes=160,
            start=OPTIMAL_START,
            fixed_start=FIXED_START,
            occurred_at=OPTIMAL_START,
        ),
        PreheatStart(comfort_at=COMFORT_AT, lead=timedelta(hours=2, minutes=40)),
    )


def test_air_dry_run_journals_later_optimal_start_when_floor_mode_preheat_begins(f_zone: ZoneSettings) -> None:
    memory = ZoneMemory(path=ControlPath.AIR_DRY_RUN, mode=ScheduleMode.NIGHT)

    follow_up = _follow(
        f_zone,
        memory,
        _conditions('20.4', learning=LEARNED),
        _cycle(FIXED_START),
        ZoneCycleOutcome.APPLIED,
    )

    assert (follow_up.optimal_start, follow_up.memory.preheat) == (
        OptimalStartDryRun(
            zone='kitchen',
            lead_minutes=160,
            start=OPTIMAL_START,
            fixed_start=FIXED_START,
            occurred_at=FIXED_START,
        ),
        PreheatStart(comfort_at=COMFORT_AT, lead=timedelta(hours=2, minutes=40)),
    )


def test_air_dry_run_journals_optimal_start_once_per_morning(
    f_zone: ZoneSettings,
    f_latched_memory: ZoneMemory,
) -> None:
    follow_up = _follow(
        f_zone,
        f_latched_memory,
        _conditions('21.0', learning=LEARNED),
        _cycle(OPTIMAL_START + timedelta(minutes=30)),
        ZoneCycleOutcome.UNCHANGED,
    )

    assert (follow_up.optimal_start, follow_up.memory.preheat) == (None, f_latched_memory.preheat)


def test_floor_mode_preheat_of_dry_run_zone_starts_morning(f_zone: ZoneSettings) -> None:
    memory = ZoneMemory(path=ControlPath.AIR_DRY_RUN, mode=ScheduleMode.NIGHT)

    follow_up = _follow(f_zone, memory, _conditions('20.0'), _cycle(FIXED_START), ZoneCycleOutcome.APPLIED)

    assert follow_up.memory.morning == Morning(
        comfort_at=COMFORT_AT,
        target=TARGET,
        outdoor=OUTDOOR,
        samples=(AirSample(at=FIXED_START, air=Decimal('20.0')),),
    )


@pytest.mark.parametrize(
    'outcome',
    [
        ZoneCycleOutcome.THROTTLED,
        ZoneCycleOutcome.WAITING,
        ZoneCycleOutcome.DEVICE_UNAVAILABLE,
        ZoneCycleOutcome.FAILED,
        ZoneCycleOutcome.SHED,
    ],
)
def test_preheat_the_thermostat_does_not_take_starts_no_morning(
    f_zone: ZoneSettings,
    outcome: ZoneCycleOutcome,
) -> None:
    memory = ZoneMemory(path=ControlPath.AIR_DRY_RUN, mode=ScheduleMode.NIGHT)

    follow_up = _follow(f_zone, memory, _conditions('20.0'), _cycle(FIXED_START), outcome)

    assert follow_up.memory.morning is None


def test_morning_is_dropped_when_its_thermostat_is_lost(f_zone: ZoneSettings, f_morning: Morning) -> None:
    memory = ZoneMemory(path=ControlPath.AIR_DRY_RUN, mode=ScheduleMode.PREHEAT, morning=f_morning)

    follow_up = _follow(
        f_zone,
        memory,
        _conditions('20.4'),
        _cycle(FIXED_START + timedelta(hours=1)),
        ZoneCycleOutcome.DEVICE_UNAVAILABLE,
    )

    assert (follow_up.morning, follow_up.memory.morning) == (None, None)


def test_morning_without_live_writes_is_not_observed(f_zone: ZoneSettings) -> None:
    memory = ZoneMemory(path=ControlPath.AIR_DRY_RUN, mode=ScheduleMode.NIGHT)

    follow_up = _follow(
        f_zone,
        memory,
        _conditions('20.0', writes_live=False),
        _cycle(FIXED_START),
        ZoneCycleOutcome.DRY_RUN,
    )

    assert follow_up.memory.morning is None


def test_mode_seen_on_untrusted_clock_starts_no_morning(f_zone: ZoneSettings) -> None:
    follow_up = _follow(
        f_zone,
        ZoneMemory(),
        _conditions('20.0'),
        _cycle(NIGHT, clock_trusted=False),
        ZoneCycleOutcome.APPLIED,
    )

    assert follow_up.memory.mode is None


@pytest.mark.parametrize(
    ('mode', 'outcome', 'expected'),
    [
        (ScheduleMode.COMFORT, ZoneCycleOutcome.APPLIED, ScheduleMode.NIGHT),
        (None, ZoneCycleOutcome.UNCHANGED, ScheduleMode.NIGHT),
        (ScheduleMode.NIGHT, ZoneCycleOutcome.DEVICE_UNAVAILABLE, ScheduleMode.NIGHT),
        (ScheduleMode.COMFORT, ZoneCycleOutcome.THROTTLED, ScheduleMode.COMFORT),
        (None, ZoneCycleOutcome.DEVICE_UNAVAILABLE, None),
        (None, ZoneCycleOutcome.FAILED, None),
        (None, ZoneCycleOutcome.WAITING, None),
        (None, ZoneCycleOutcome.SHED, None),
    ],
    ids=[
        'night_written',
        'night_already_held',
        'night_held_before_the_thermostat_went_silent',
        'comfort_kept_while_the_night_write_waits',
        'untrusted_comfort_kept_while_the_thermostat_is_unavailable',
        'untrusted_comfort_kept_while_the_night_write_fails',
        'untrusted_comfort_kept_while_the_night_waits_for_a_permit',
        'untrusted_comfort_kept_while_the_zone_is_shed',
    ],
)
def test_night_counts_once_its_thermostat_takes_it(
    f_zone: ZoneSettings,
    mode: ScheduleMode | None,
    outcome: ZoneCycleOutcome,
    expected: ScheduleMode | None,
) -> None:
    memory = ZoneMemory(path=ControlPath.AIR_DRY_RUN, mode=mode)

    follow_up = _follow(f_zone, memory, _conditions('20.0'), _cycle(NIGHT), outcome)

    assert follow_up.memory.mode is expected


@pytest.fixture
def comfort_held_on_untrusted_clock_memory(f_live_zone: ZoneSettings) -> ZoneMemory:
    return _follow(
        f_live_zone,
        ZoneMemory(),
        _conditions(None, learning=SLOW_LEARNED),
        _cycle(PREHEAT_WRITE - timedelta(minutes=30), clock_trusted=False),
        ZoneCycleOutcome.APPLIED,
    ).memory


@pytest.fixture
def f_night_its_thermostat_never_took_memory(
    f_live_zone: ZoneSettings,
    comfort_held_on_untrusted_clock_memory: ZoneMemory,
) -> ZoneMemory:
    return _follow(
        f_live_zone,
        comfort_held_on_untrusted_clock_memory,
        _conditions(None, learning=SLOW_LEARNED),
        _cycle(SENSOR_BACK - timedelta(minutes=2)),
        ZoneCycleOutcome.DEVICE_UNAVAILABLE,
    ).memory


def test_air_sensor_back_in_preheat_after_a_night_its_thermostat_never_took_starts_no_morning(
    f_live_zone: ZoneSettings,
    f_night_its_thermostat_never_took_memory: ZoneMemory,
) -> None:
    follow_up = _follow(
        f_live_zone,
        f_night_its_thermostat_never_took_memory,
        _conditions('20.0', learning=SLOW_LEARNED),
        _cycle(SENSOR_BACK),
        ZoneCycleOutcome.APPLIED,
    )

    assert (follow_up.memory.mode, follow_up.memory.morning, follow_up.memory.preheated_for) == (
        ScheduleMode.PREHEAT,
        None,
        COMFORT_AT,
    )


def test_finished_morning_is_handed_over_once(f_zone: ZoneSettings, f_morning: Morning) -> None:
    memory = ZoneMemory(path=ControlPath.AIR_DRY_RUN, mode=ScheduleMode.PREHEAT, morning=f_morning)

    follow_up = _follow(f_zone, memory, _conditions('21.9'), _cycle(COMFORT_AT), ZoneCycleOutcome.UNCHANGED)

    assert (follow_up.morning, follow_up.memory.morning) == (f_morning, None)


@pytest.mark.parametrize(
    'outcome',
    [ZoneCycleOutcome.UNCHANGED, ZoneCycleOutcome.APPLIED],
    ids=['thermostat_still_heating', 'thermostat_written_again'],
)
def test_air_sensor_back_in_preheat_starts_no_morning_for_a_comfort_start_already_preheated_for(
    f_live_zone: ZoneSettings,
    outcome: ZoneCycleOutcome,
) -> None:
    memory = ZoneMemory(
        path=ControlPath.FLOOR,
        mode=ScheduleMode.NIGHT,
        preheat=PreheatStart(comfort_at=COMFORT_AT, lead=timedelta(hours=4)),
        preheated_for=COMFORT_AT,
    )

    follow_up = _follow(
        f_live_zone,
        memory,
        _conditions('20.0', learning=SLOW_LEARNED),
        _cycle(SENSOR_BACK),
        outcome,
    )

    assert (follow_up.memory.mode, follow_up.memory.morning, follow_up.memory.preheated_for) == (
        ScheduleMode.PREHEAT,
        None,
        COMFORT_AT,
    )


def test_air_sensor_back_in_preheat_starts_morning_missed_at_its_start(f_live_zone: ZoneSettings) -> None:
    memory = ZoneMemory(
        path=ControlPath.FLOOR,
        mode=ScheduleMode.NIGHT,
        preheated_for=COMFORT_AT - timedelta(days=1),
    )

    follow_up = _follow(
        f_live_zone,
        memory,
        _conditions('20.0', learning=SLOW_LEARNED),
        _cycle(SENSOR_BACK),
        ZoneCycleOutcome.APPLIED,
    )

    assert (follow_up.memory.morning, follow_up.memory.preheated_for) == (
        Morning(
            comfort_at=COMFORT_AT,
            target=TARGET,
            outdoor=OUTDOOR,
            samples=(AirSample(at=SENSOR_BACK, air=Decimal('20.0')),),
        ),
        COMFORT_AT,
    )


def test_zone_entering_air_mode_into_preheat_its_thermostat_already_holds_starts_no_morning(
    f_live_zone: ZoneSettings,
) -> None:
    memory = ZoneMemory(path=ControlPath.FLOOR, mode=ScheduleMode.NIGHT)

    follow_up = _follow(
        f_live_zone,
        memory,
        _conditions('20.0', learning=SLOW_LEARNED),
        _cycle(SENSOR_BACK),
        ZoneCycleOutcome.UNCHANGED,
    )

    assert (follow_up.memory.mode, follow_up.memory.morning, follow_up.memory.preheated_for) == (
        ScheduleMode.PREHEAT,
        None,
        COMFORT_AT,
    )


@pytest.mark.parametrize(
    ('outcome', 'clock_trusted', 'preheated_for'),
    [
        (ZoneCycleOutcome.APPLIED, True, COMFORT_AT),
        (ZoneCycleOutcome.THROTTLED, True, None),
        (ZoneCycleOutcome.WAITING, True, None),
        (ZoneCycleOutcome.SHED, True, None),
        (ZoneCycleOutcome.APPLIED, False, None),
    ],
    ids=['thermostat_follows', 'write_waits_its_turn', 'start_waits_for_a_permit', 'zone_shed', 'clock_untrusted'],
)
def test_preheat_that_started_no_morning_is_noted_once_its_thermostat_follows_it(
    f_live_zone: ZoneSettings,
    f_held_back_preheat_memory: ZoneMemory,
    outcome: ZoneCycleOutcome,
    clock_trusted: bool,
    preheated_for: datetime | None,
) -> None:
    follow_up = _follow(
        f_live_zone,
        f_held_back_preheat_memory,
        _conditions('19.4', learning=SLOW_LEARNED),
        _cycle(PREHEAT_WRITE, clock_trusted=clock_trusted),
        outcome,
    )

    assert (follow_up.memory.morning, follow_up.memory.preheated_for) == (None, preheated_for)


def test_untrusted_clock_keeps_held_preheat(f_live_zone: ZoneSettings, f_preheating_memory: ZoneMemory) -> None:
    follow_up = _follow(
        f_live_zone,
        f_preheating_memory,
        _conditions('21.5', learning=LEARNED),
        _cycle(COMFORT_AT - timedelta(hours=1, minutes=30), clock_trusted=False),
        ZoneCycleOutcome.UNCHANGED,
    )

    assert (follow_up.memory.preheat, follow_up.memory.air_state) == (f_preheating_memory.preheat, AirState.HEAT)


def test_air_mode_stop_in_preheat_finishes_morning(
    f_live_zone: ZoneSettings,
    f_preheating_morning_memory: ZoneMemory,
    f_morning: Morning,
) -> None:
    follow_up = _follow(
        f_live_zone,
        f_preheating_morning_memory,
        _conditions('21.9', learning=LEARNED),
        _cycle(AIR_MODE_STOP),
        ZoneCycleOutcome.APPLIED,
    )

    assert (follow_up.morning, follow_up.memory.morning, follow_up.memory.air_state) == (
        replace(f_morning, samples=(*f_morning.samples, AirSample(at=AIR_MODE_STOP, air=Decimal('21.9')))),
        None,
        AirState.IDLE,
    )


@pytest.mark.parametrize(
    'outcome',
    [ZoneCycleOutcome.THROTTLED, ZoneCycleOutcome.WAITING],
    ids=['for_the_write_budget', 'for_a_start_permit'],
)
def test_air_mode_stop_waiting_its_turn_keeps_morning_going(
    f_live_zone: ZoneSettings,
    f_preheating_morning_memory: ZoneMemory,
    f_morning: Morning,
    outcome: ZoneCycleOutcome,
) -> None:
    follow_up = _follow(
        f_live_zone,
        f_preheating_morning_memory,
        _conditions('21.9', learning=LEARNED),
        _cycle(AIR_MODE_STOP),
        outcome,
    )

    assert (follow_up.morning, follow_up.memory.morning, follow_up.memory.air_state) == (
        None,
        replace(f_morning, samples=(*f_morning.samples, AirSample(at=AIR_MODE_STOP, air=Decimal('21.9')))),
        AirState.HEAT,
    )


def test_lost_air_sensor_keeps_latched_preheat_and_forgets_air_state(
    f_live_zone: ZoneSettings,
    f_watched_memory: ZoneMemory,
) -> None:
    memory = replace(f_watched_memory, preheat=PreheatStart(comfort_at=COMFORT_AT, lead=timedelta(hours=3)))

    follow_up = _follow(f_live_zone, memory, _conditions(None), _cycle(MORNING), ZoneCycleOutcome.APPLIED)

    assert follow_up.memory == ZoneMemory(
        path=ControlPath.FLOOR,
        mode=ScheduleMode.COMFORT,
        would_be_target=COMFORT,
        preheat=PreheatStart(comfort_at=COMFORT_AT, lead=timedelta(hours=3)),
    )


@pytest.mark.parametrize('outcome', [ZoneCycleOutcome.SHED, ZoneCycleOutcome.HANDS_OFF], ids=['shed', 'held_by_hand'])
def test_zone_left_alone_keeps_no_air_state_and_drops_its_morning_and_stop_watch(
    f_live_zone: ZoneSettings,
    f_preheating_morning_memory: ZoneMemory,
    outcome: ZoneCycleOutcome,
) -> None:
    memory = replace(
        f_preheating_morning_memory,
        preheated_for=COMFORT_AT,
        stop=StopWatch(
            stopped_at=AIR_MODE_STOP - timedelta(minutes=30),
            target=TARGET,
            offset=Decimal('0.1'),
            peak=Decimal('21.9'),
        ),
    )

    follow_up = _follow(
        f_live_zone,
        memory,
        _conditions('21.7', learning=LEARNED),
        _cycle(AIR_MODE_STOP),
        outcome,
    )

    assert follow_up == ZoneFollowUp(
        memory=ZoneMemory(
            path=ControlPath.AIR,
            mode=ScheduleMode.PREHEAT,
            preheat=PreheatStart(comfort_at=COMFORT_AT, lead=timedelta(hours=2, minutes=40)),
            preheated_for=COMFORT_AT,
        ),
        air_dry_run=None,
        optimal_start=None,
        morning=None,
        stop=None,
    )


@pytest.mark.parametrize('outcome', [ZoneCycleOutcome.SHED, ZoneCycleOutcome.HANDS_OFF], ids=['shed', 'held_by_hand'])
def test_zone_left_alone_in_air_dry_run_journals_nothing_and_keeps_its_would_be_target(
    f_zone: ZoneSettings,
    f_journaled_memory: ZoneMemory,
    outcome: ZoneCycleOutcome,
) -> None:
    follow_up = _follow(f_zone, f_journaled_memory, _conditions('22.0'), _cycle(MORNING), outcome)

    assert follow_up == ZoneFollowUp(
        memory=ZoneMemory(path=ControlPath.AIR_DRY_RUN, mode=ScheduleMode.COMFORT, would_be_target=HEAT),
        air_dry_run=None,
        optimal_start=None,
        morning=None,
        stop=None,
    )


@pytest.fixture
def f_reset_memory(f_heating_memory: ZoneMemory) -> ZoneMemory:
    return replace(f_heating_memory, resets=2)


@pytest.mark.parametrize(
    'outcome',
    [ZoneCycleOutcome.UNCHANGED, ZoneCycleOutcome.APPLIED, ZoneCycleOutcome.SHED],
)
def test_zone_keeps_the_reset_count_of_its_learning_through_a_cycle(
    f_live_zone: ZoneSettings,
    f_reset_memory: ZoneMemory,
    outcome: ZoneCycleOutcome,
) -> None:
    follow_up = _follow(f_live_zone, f_reset_memory, _conditions('21.5'), _cycle(MORNING), outcome)

    assert follow_up.memory.resets == 2
