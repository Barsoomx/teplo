from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from apps.climate.domain.change import ThermostatChange
from apps.climate.domain.drive import ZoneStart
from apps.climate.domain.events import (
    AirControlDryRun,
    OptimalStartDryRun,
    SecondFloorGuardDryRun,
    ZoneCommandFailed,
    ZoneCommandIssued,
)
from apps.climate.domain.memory import ZoneMemory
from apps.climate.domain.modes import AirState, ControlPath, ScheduleMode, ZoneCycleOutcome, ZoneKind
from apps.climate.domain.ports import LoadGate
from apps.climate.factories import ClimateSettingsFactory, ComfortPeriodFactory, ZoneFactory, ZoneLearningFactory
from apps.climate.models import ClimateSettings, Zone, ZoneLearning
from apps.climate.repositories.climate_settings import ClimateSettingsRepository
from apps.climate.repositories.zone import ZoneRepository
from apps.climate.repositories.zone_learning import ZoneLearningRepository
from apps.climate.services.control_book import ControlBook
from apps.climate.stubs import LoadGateStub
from apps.climate.usecases.run_heating_cycle import RunHeatingCycleInputDTO, RunHeatingCycleUseCase
from apps.core.domain.events import DomainEvent, EventDispatcher
from apps.core.stubs import MonotonicClockStub
from apps.devices.domain.capabilities import FloorThermostat
from apps.devices.domain.errors import DeviceUnavailableError
from apps.devices.domain.kinds import DeviceDriver, DeviceKind
from apps.devices.domain.readings import TemperatureReading, ThermostatMode, ThermostatState
from apps.devices.factories import DeviceFactory
from apps.devices.models import Device
from apps.devices.services.state_store import TemperatureStateStore, ThermostatStateStore
from apps.devices.stubs import FloorThermostatStub, ThermostatProviderStub
from apps.power.domain.control import LoadControl, PhaseThresholds
from apps.power.domain.events import StartWouldWaitDryRun
from apps.power.domain.guard import GuardLoads, GuardState, SeenReading
from apps.power.domain.load_status import thermostat_reading
from apps.power.domain.loads import LoadKind, LoadPhase, LoadRecord
from apps.power.domain.meter_watch import MeterWatch
from apps.power.domain.start_gate import StartRefusal
from apps.power.services.guard_book import GuardBook
from apps.power.services.guard_load_gate import GuardLoadGate

pytestmark = pytest.mark.django_db

MOSCOW = ZoneInfo('Europe/Moscow')
MORNING = datetime(2026, 10, 29, 7, 0, tzinfo=MOSCOW)
PREHEAT = datetime(2026, 10, 29, 4, 0, tzinfo=MOSCOW)
NIGHT = datetime(2026, 10, 29, 1, 0, tzinfo=MOSCOW)
COMFORT_AT = datetime(2026, 10, 29, 6, 30, tzinfo=MOSCOW)
FIXED_START = datetime(2026, 10, 29, 3, 30, tzinfo=MOSCOW)
OPTIMAL_START = datetime(2026, 10, 29, 3, 50, tzinfo=MOSCOW)
FRESH = timedelta(0)
MINUTE = timedelta(minutes=1)
UNAVAILABLE_AFTER = timedelta(minutes=10)
STALE = timedelta(minutes=11)
KITCHEN = 'thermostat-kitchen'
BATHROOM = 'thermostat-bathroom-2f'
LIVING_ROOM = 'thermostat-living-room'
HALF_HOUR = timedelta(minutes=30)
HOUSE_AIR = {'air-kitchen': '20.4', 'outdoor': '-3.4', 'air-second-floor': '18.5'}
MORNING_AIR = (
    (FIXED_START - MINUTE, '20.0'),
    (FIXED_START, '20.0'),
    (FIXED_START + timedelta(minutes=40), '20.1'),
    (FIXED_START + timedelta(hours=1, minutes=40), '20.9'),
    (FIXED_START + timedelta(hours=2, minutes=40), '21.7'),
    (COMFORT_AT - MINUTE, '21.9'),
)
AIR_MODE_MORNING_AIR = (
    (OPTIMAL_START - MINUTE, '20.4', '18.0'),
    (OPTIMAL_START, '20.4', '18.0'),
    (OPTIMAL_START + timedelta(minutes=40), '20.5', '30.0'),
    (OPTIMAL_START + timedelta(hours=1, minutes=40), '21.5', '30.0'),
)
AIR_MODE_STOP = OPTIMAL_START + timedelta(hours=1, minutes=52)
STOP_AIR = (
    (MORNING, '21.8', '30.0'),
    (MORNING + timedelta(hours=1), '22.3', '18.0'),
    (MORNING + timedelta(hours=2), '22.1', '18.0'),
)
LOAD_THRESHOLDS = PhaseThresholds(
    start_ceiling=Decimal('20.0'),
    shed=Decimal('24.0'),
    shed_hold=timedelta(seconds=3),
    restore=Decimal('18.0'),
    restore_hold=timedelta(minutes=2),
    shed_interval=timedelta(seconds=1),
    restore_interval=timedelta(minutes=1),
    start_interval=timedelta(seconds=30),
    meter_lost_after=timedelta(seconds=60),
)
KITCHEN_LOAD = LoadRecord(
    slug='kitchen',
    name='Кухня',
    kind=LoadKind.ZONE,
    zone_slug='kitchen',
    device_slug=KITCHEN,
    priority=5,
    phase=LoadPhase.L2,
    nominal_amps=Decimal('8.0'),
    learned=None,
    live=True,
    shed=None,
)
LIVING_ROOM_LOAD = replace(
    KITCHEN_LOAD,
    slug='living-room',
    name='Гостиная',
    zone_slug='living-room',
    device_slug=LIVING_ROOM,
    priority=7,
)
HALLWAY_LOAD = replace(
    KITCHEN_LOAD,
    slug='hallway',
    name='Коридор',
    zone_slug='hallway',
    device_slug='thermostat-hallway',
    priority=4,
)
FRESH_METER = MeterWatch(seen=True, stale_since=None)
UNSEEN_METER = MeterWatch(seen=False, stale_since=0.0)
HALLWAY_HEATING = SeenReading(
    polled_at=MORNING,
    reading=thermostat_reading(
        power=True,
        heating=True,
        setpoint=Decimal('28.0'),
        floor_temperature=Decimal('24.5'),
        read_at=-5.0,
    ),
)

House = Callable[[ThermostatStateStore, TemperatureStateStore], None]


def _state(
    setpoint: str,
    mode: ThermostatMode = ThermostatMode.HOLD,
    read_at: datetime = MORNING,
    heating: bool | None = None,
    power: bool = True,
) -> ThermostatState:
    return ThermostatState(
        power=power,
        mode=mode,
        setpoint=Decimal(setpoint),
        floor_temperature=Decimal('21.0'),
        heating=heating,
        read_at=read_at,
    )


def _reading(temperature: str) -> TemperatureReading:
    return TemperatureReading(temperature=Decimal(temperature), humidity=None, read_at=MORNING)


def _at(now: datetime, *, clock_trusted: bool = True) -> RunHeatingCycleInputDTO:
    return RunHeatingCycleInputDTO(now=now, clock_trusted=clock_trusted)


def _use_case(
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    thermostats: dict[str, FloorThermostat],
    book: ControlBook,
    dispatcher: EventDispatcher,
    *,
    dry_run: bool = False,
    gate: LoadGate | None = None,
) -> RunHeatingCycleUseCase:
    return RunHeatingCycleUseCase(
        zones=ZoneRepository(),
        climate_settings=ClimateSettingsRepository(),
        learning=ZoneLearningRepository(),
        store=store,
        temperatures=temperatures,
        provider=ThermostatProviderStub(thermostats=thermostats),
        book=book,
        gate=LoadGateStub(held=frozenset(), answers={}) if gate is None else gate,
        dry_run=dry_run,
        dispatcher=dispatcher,
    )


def _cycle(
    zone: Zone,
    state: ThermostatState,
    thermostat: FloorThermostat,
    dispatcher: EventDispatcher,
    *,
    dry_run: bool = False,
    reading_age: timedelta = FRESH,
) -> RunHeatingCycleUseCase:
    monotonic = MonotonicClockStub(now=0.0)
    store = ThermostatStateStore(unavailable_after=UNAVAILABLE_AFTER, clock=monotonic)
    store.record_state(zone.thermostat.slug, state)
    monotonic.advance(reading_age.total_seconds())
    return _use_case(
        store,
        TemperatureStateStore(unavailable_after=UNAVAILABLE_AFTER, clock=monotonic),
        {zone.thermostat.slug: thermostat},
        ControlBook(),
        dispatcher,
        dry_run=dry_run,
    )


def _sensor(slug: str) -> Device:
    return DeviceFactory.create(slug=slug, kind=DeviceKind.TEMPERATURE_SENSOR, driver=DeviceDriver.ESPHOME_TEMPERATURE)


def _air_zone(*, air_control_live: bool = False) -> Zone:
    zone = ZoneFactory.create(
        slug='kitchen',
        thermostat__slug=KITCHEN,
        air_sensor=_sensor('air-kitchen'),
        live_control=True,
        air_control_live=air_control_live,
    )
    ComfortPeriodFactory.create_batch(7, zone=zone, start=time(6, 30), end=time(23, 30))
    return zone


def _guard_zone() -> Zone:
    zone = ZoneFactory.create(
        slug='bathroom-2f',
        kind=ZoneKind.FLOOR,
        thermostat__slug=BATHROOM,
        live_control=True,
        comfort_floor_setpoint=Decimal('30.0'),
    )
    ComfortPeriodFactory.create_batch(7, zone=zone, start=time(6, 30), end=time(23, 30))
    return zone


def _house_settings(*, guard_live: bool = False) -> ClimateSettings:
    return ClimateSettingsFactory.create(
        outdoor_sensor=_sensor('outdoor'),
        second_floor_sensor=_sensor('air-second-floor'),
        second_floor_guard_zone=_guard_zone(),
        second_floor_guard_live=guard_live,
    )


def _learned(zone: Zone, *, mornings: int = 3, rate: str = '0.800') -> ZoneLearning:
    return ZoneLearningFactory.create(
        zone=zone,
        dead_time=timedelta(minutes=40),
        heat_up_rates={'-5': rate},
        mornings=mornings,
    )


def _read(temperatures: TemperatureStateStore, readings: dict[str, str]) -> None:
    for slug, temperature in readings.items():
        temperatures.record_state(slug, _reading(temperature))


def _watch_stop(
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    thermostats: dict[str, FloorThermostat],
    book: ControlBook,
    *,
    dry_run: bool,
) -> None:
    for at, air, setpoint in STOP_AIR:
        store.record_state(KITCHEN, _state(setpoint))
        temperatures.record_state('air-kitchen', _reading(air))
        _use_case(store, temperatures, thermostats, book, EventDispatcher(), dry_run=dry_run).execute(_at(at))


def _floor_zone(slug: str, thermostat: str, **fields: object) -> Zone:
    zone = ZoneFactory.create(slug=slug, thermostat__slug=thermostat, live_control=True, **fields)
    ComfortPeriodFactory.create_batch(7, zone=zone, start=time(6, 30), end=time(23, 30))
    return zone


def _shed_floor_zone(store: ThermostatStateStore, _temperatures: TemperatureStateStore) -> None:
    _floor_zone('kitchen', KITCHEN, last_command_at=MORNING - HALF_HOUR)
    store.record_state(KITCHEN, _state('22.0', power=False))


def _shed_air_zone(store: ThermostatStateStore, temperatures: TemperatureStateStore) -> None:
    Zone.objects.filter(pk=_air_zone(air_control_live=True).pk).update(last_command_at=MORNING - HALF_HOUR)
    store.record_state(KITCHEN, _state('28.0', power=False))
    temperatures.record_state('air-kitchen', _reading('21.5'))


def _shed_boosted_zone(store: ThermostatStateStore, _temperatures: TemperatureStateStore) -> None:
    _floor_zone('kitchen', KITCHEN, last_command_at=MORNING - HALF_HOUR, boost_until=MORNING + timedelta(hours=1))
    store.record_state(KITCHEN, _state('22.0', power=False))


def _shed_guarded_zone(store: ThermostatStateStore, temperatures: TemperatureStateStore) -> None:
    _house_settings(guard_live=True)
    Zone.objects.filter(slug='bathroom-2f').update(last_command_at=NIGHT - HALF_HOUR)
    store.record_state(BATHROOM, _state('22.0', power=False))
    temperatures.record_state('air-second-floor', _reading('18.9'))


def _idle_floor_zone(store: ThermostatStateStore, _temperatures: TemperatureStateStore) -> None:
    _floor_zone('kitchen', KITCHEN)
    store.record_state(KITCHEN, _state('22.0', heating=False))


def _idle_air_zone(store: ThermostatStateStore, temperatures: TemperatureStateStore) -> None:
    _air_zone(air_control_live=True)
    store.record_state(KITCHEN, _state('18.0', heating=False))
    temperatures.record_state('air-kitchen', _reading('21.5'))


def _idle_boosted_zone(store: ThermostatStateStore, _temperatures: TemperatureStateStore) -> None:
    _floor_zone('kitchen', KITCHEN, boost_until=MORNING + timedelta(hours=1))
    store.record_state(KITCHEN, _state('22.0', heating=False))


def _watch(
    book: GuardBook,
    *,
    management_live: bool,
    load_live: bool = True,
    meter: MeterWatch = FRESH_METER,
    l2: str = '10.0',
    readings: dict[str, SeenReading] | None = None,
) -> None:
    loads = GuardLoads(
        records=tuple(replace(record, live=load_live) for record in (HALLWAY_LOAD, KITCHEN_LOAD, LIVING_ROOM_LOAD)),
        control=LoadControl(live=management_live, thresholds=LOAD_THRESHOLDS, default_amps=Decimal('6.0')),
        live_zones=frozenset({'hallway', 'kitchen', 'living-room'}),
        meter='phase-meter',
        read_at=0.0,
    )
    started = GuardState.started(loads, 0.0)
    currents = (
        {LoadPhase.L1: Decimal('8.0'), LoadPhase.L2: Decimal(l2), LoadPhase.L3: Decimal('4.0')}
        if meter.stale_since is None
        else {}
    )
    book.remember(
        replace(
            started,
            phases=replace(started.phases, meter=meter, currents=currents, read_at=dict.fromkeys(currents, 0.0)),
            readings={} if readings is None else readings,
        ),
    )


@pytest.fixture
def f_zone() -> Zone:
    zone = ZoneFactory.create(slug='kitchen', thermostat__slug=KITCHEN, live_control=True)
    ComfortPeriodFactory.create_batch(7, zone=zone, start=time(6, 30), end=time(23, 30))
    return zone


@pytest.fixture
def f_thermostat() -> FloorThermostatStub:
    return FloorThermostatStub(state=None)


@pytest.fixture
def f_guard_thermostat() -> FloorThermostatStub:
    return FloorThermostatStub(state=None)


@pytest.fixture
def f_events() -> list[DomainEvent]:
    return []


@pytest.fixture
def dispatcher(f_events: list[DomainEvent]) -> EventDispatcher:
    event_dispatcher = EventDispatcher()
    for event_type in (
        ZoneCommandIssued,
        ZoneCommandFailed,
        AirControlDryRun,
        OptimalStartDryRun,
        SecondFloorGuardDryRun,
        StartWouldWaitDryRun,
    ):
        event_dispatcher.register(event_type, f_events.append)
    return event_dispatcher


@pytest.fixture
def clock() -> MonotonicClockStub:
    return MonotonicClockStub(now=0.0)


@pytest.fixture
def store(clock: MonotonicClockStub) -> ThermostatStateStore:
    return ThermostatStateStore(unavailable_after=UNAVAILABLE_AFTER, clock=clock)


@pytest.fixture
def temperatures(clock: MonotonicClockStub) -> TemperatureStateStore:
    return TemperatureStateStore(unavailable_after=UNAVAILABLE_AFTER, clock=clock)


@pytest.fixture
def book() -> ControlBook:
    return ControlBook()


@pytest.fixture
def f_cycle(f_zone: Zone, f_thermostat: FloorThermostatStub, dispatcher: EventDispatcher) -> RunHeatingCycleUseCase:
    return _cycle(f_zone, _state('22.0'), f_thermostat, dispatcher)


@pytest.fixture
def f_relay_cycle(
    request: pytest.FixtureRequest,
    f_zone: Zone,
    f_thermostat: FloorThermostatStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    return _cycle(f_zone, _state('22.0', heating=request.param), f_thermostat, dispatcher)


@pytest.fixture
def f_dry_run_cycle(
    f_zone: Zone,
    f_thermostat: FloorThermostatStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    return _cycle(f_zone, _state('22.0'), f_thermostat, dispatcher, dry_run=True)


@pytest.fixture
def f_observed_cycle(
    f_zone: Zone,
    f_thermostat: FloorThermostatStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    Zone.objects.filter(pk=f_zone.pk).update(live_control=False)
    return _cycle(f_zone, _state('22.0'), f_thermostat, dispatcher)


@pytest.fixture
def f_clock_behind_cycle(
    f_zone: Zone,
    f_thermostat: FloorThermostatStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    Zone.objects.filter(pk=f_zone.pk).update(last_command_at=NIGHT + timedelta(hours=2))
    return _cycle(f_zone, _state('22.0', read_at=NIGHT), f_thermostat, dispatcher)


@pytest.fixture
def f_boost_ahead_of_clock_cycle(
    f_zone: Zone,
    f_thermostat: FloorThermostatStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    Zone.objects.filter(pk=f_zone.pk).update(boost_until=NIGHT + timedelta(hours=5))
    return _cycle(f_zone, _state('22.0', read_at=NIGHT), f_thermostat, dispatcher)


@pytest.fixture
def f_program_mode_cycle(
    f_zone: Zone,
    f_thermostat: FloorThermostatStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    return _cycle(f_zone, _state('5.0', mode=ThermostatMode.SMART), f_thermostat, dispatcher)


@pytest.fixture
def f_recent_command_cycle(
    f_zone: Zone,
    f_thermostat: FloorThermostatStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    Zone.objects.filter(pk=f_zone.pk).update(last_command_at=MORNING - timedelta(minutes=10))
    return _cycle(f_zone, _state('22.0'), f_thermostat, dispatcher)


@pytest.fixture
def f_promoted_cycle(
    f_zone: Zone,
    f_thermostat: FloorThermostatStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    Zone.objects.filter(pk=f_zone.pk).update(last_dry_run_at=MORNING - timedelta(minutes=10))
    return _cycle(f_zone, _state('22.0'), f_thermostat, dispatcher)


@pytest.fixture
def f_recent_dry_run_cycle(
    f_zone: Zone,
    f_thermostat: FloorThermostatStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    Zone.objects.filter(pk=f_zone.pk).update(last_dry_run_at=MORNING - timedelta(minutes=10))
    return _cycle(f_zone, _state('22.0'), f_thermostat, dispatcher, dry_run=True)


@pytest.fixture
def f_stale_cycle(
    f_zone: Zone,
    f_thermostat: FloorThermostatStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    return _cycle(f_zone, _state('22.0'), f_thermostat, dispatcher, reading_age=timedelta(minutes=11))


@pytest.fixture
def f_failing_cycle(f_zone: Zone, dispatcher: EventDispatcher) -> RunHeatingCycleUseCase:
    thermostat = FloorThermostatStub(state=None, error=DeviceUnavailableError('timeout'))
    return _cycle(f_zone, _state('22.0'), thermostat, dispatcher)


def test_cycle_applies_comfort_setpoint(
    f_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
    f_events: list[DomainEvent],
    f_zone: Zone,
) -> None:
    output = f_cycle.execute(RunHeatingCycleInputDTO(now=MORNING, clock_trusted=True))

    assert (output.outcomes, f_thermostat.commands, f_events, Zone.objects.get(pk=f_zone.pk).last_command_at) == (
        {'kitchen': ZoneCycleOutcome.APPLIED},
        [('setpoint', Decimal('28.0'))],
        [
            ZoneCommandIssued(
                zone='kitchen',
                change=ThermostatChange(setpoint=Decimal('28.0')),
                dry_run=False,
                occurred_at=MORNING,
            ),
        ],
        MORNING,
    )


@pytest.mark.parametrize('f_relay_cycle', [True, False, None], ids=['heating', 'idle', 'unknown'], indirect=True)
def test_cycle_writes_the_same_whatever_the_relay_reads(
    f_relay_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
    f_events: list[DomainEvent],
    f_zone: Zone,
) -> None:
    output = f_relay_cycle.execute(RunHeatingCycleInputDTO(now=MORNING, clock_trusted=True))

    assert (output.outcomes, f_thermostat.commands, f_events, Zone.objects.get(pk=f_zone.pk).last_command_at) == (
        {'kitchen': ZoneCycleOutcome.APPLIED},
        [('setpoint', Decimal('28.0'))],
        [
            ZoneCommandIssued(
                zone='kitchen',
                change=ThermostatChange(setpoint=Decimal('28.0')),
                dry_run=False,
                occurred_at=MORNING,
            ),
        ],
        MORNING,
    )


def test_cycle_preheats_before_comfort(f_cycle: RunHeatingCycleUseCase, f_thermostat: FloorThermostatStub) -> None:
    f_cycle.execute(RunHeatingCycleInputDTO(now=PREHEAT, clock_trusted=True))

    assert f_thermostat.commands == [('setpoint', Decimal('28.0'))]


def test_cycle_leaves_matching_thermostat_alone(
    f_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
) -> None:
    output = f_cycle.execute(RunHeatingCycleInputDTO(now=NIGHT, clock_trusted=True))

    assert (output.outcomes, f_thermostat.commands) == ({'kitchen': ZoneCycleOutcome.UNCHANGED}, [])


def test_cycle_holds_comfort_when_clock_untrusted(
    f_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
) -> None:
    f_cycle.execute(RunHeatingCycleInputDTO(now=NIGHT, clock_trusted=False))

    assert f_thermostat.commands == [('setpoint', Decimal('28.0'))]


def test_cycle_holds_comfort_when_clock_fell_behind_last_command(
    f_clock_behind_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
) -> None:
    output = f_clock_behind_cycle.execute(RunHeatingCycleInputDTO(now=NIGHT, clock_trusted=False))

    assert (output.outcomes, f_thermostat.commands) == (
        {'kitchen': ZoneCycleOutcome.APPLIED},
        [('setpoint', Decimal('28.0'))],
    )


def test_cycle_ignores_boost_ending_beyond_boost_duration_after_clock_moved_back(
    f_boost_ahead_of_clock_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
) -> None:
    output = f_boost_ahead_of_clock_cycle.execute(RunHeatingCycleInputDTO(now=NIGHT, clock_trusted=False))

    assert (output.outcomes, f_thermostat.commands) == (
        {'kitchen': ZoneCycleOutcome.APPLIED},
        [('setpoint', Decimal('28.0'))],
    )


def test_dry_run_journals_without_writing(
    f_dry_run_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
    f_events: list[DomainEvent],
    f_zone: Zone,
) -> None:
    output = f_dry_run_cycle.execute(RunHeatingCycleInputDTO(now=MORNING, clock_trusted=True))

    zone = Zone.objects.get(pk=f_zone.pk)
    assert (output.outcomes, f_thermostat.commands, f_events, zone.last_dry_run_at, zone.last_command_at) == (
        {'kitchen': ZoneCycleOutcome.DRY_RUN},
        [],
        [
            ZoneCommandIssued(
                zone='kitchen',
                change=ThermostatChange(setpoint=Decimal('28.0')),
                dry_run=True,
                occurred_at=MORNING,
            ),
        ],
        MORNING,
        None,
    )


def test_zone_without_live_control_stays_in_dry_run(
    f_observed_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
    f_events: list[DomainEvent],
) -> None:
    output = f_observed_cycle.execute(RunHeatingCycleInputDTO(now=MORNING, clock_trusted=True))

    assert (output.outcomes, f_thermostat.commands, f_events) == (
        {'kitchen': ZoneCycleOutcome.DRY_RUN},
        [],
        [
            ZoneCommandIssued(
                zone='kitchen',
                change=ThermostatChange(setpoint=Decimal('28.0')),
                dry_run=True,
                occurred_at=MORNING,
            ),
        ],
    )


def test_cycle_switches_program_mode_to_hold(
    f_program_mode_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
) -> None:
    f_program_mode_cycle.execute(RunHeatingCycleInputDTO(now=NIGHT, clock_trusted=True))

    assert f_thermostat.commands == [('mode', ThermostatMode.HOLD), ('setpoint', Decimal('22.0'))]


def test_cycle_throttles_zone_commanded_recently(
    f_recent_command_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
) -> None:
    output = f_recent_command_cycle.execute(RunHeatingCycleInputDTO(now=MORNING, clock_trusted=True))

    assert (output.outcomes, f_thermostat.commands) == ({'kitchen': ZoneCycleOutcome.THROTTLED}, [])


def test_cycle_writes_at_once_when_zone_goes_live_after_dry_run(
    f_promoted_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
) -> None:
    output = f_promoted_cycle.execute(RunHeatingCycleInputDTO(now=MORNING, clock_trusted=True))

    assert (output.outcomes, f_thermostat.commands) == (
        {'kitchen': ZoneCycleOutcome.APPLIED},
        [('setpoint', Decimal('28.0'))],
    )


def test_dry_run_throttles_its_journal_like_live_writes(
    f_recent_dry_run_cycle: RunHeatingCycleUseCase,
    f_events: list[DomainEvent],
) -> None:
    output = f_recent_dry_run_cycle.execute(RunHeatingCycleInputDTO(now=MORNING, clock_trusted=True))

    assert (output.outcomes, f_events) == ({'kitchen': ZoneCycleOutcome.THROTTLED}, [])


def test_cycle_skips_unavailable_thermostat(
    f_stale_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
) -> None:
    output = f_stale_cycle.execute(RunHeatingCycleInputDTO(now=MORNING, clock_trusted=True))

    assert (output.outcomes, f_thermostat.commands) == ({'kitchen': ZoneCycleOutcome.DEVICE_UNAVAILABLE}, [])


def test_cycle_reports_failed_write(f_failing_cycle: RunHeatingCycleUseCase, f_events: list[DomainEvent]) -> None:
    output = f_failing_cycle.execute(RunHeatingCycleInputDTO(now=MORNING, clock_trusted=True))

    assert (output.outcomes, f_events) == (
        {'kitchen': ZoneCycleOutcome.FAILED},
        [
            ZoneCommandFailed(
                zone='kitchen',
                change=ThermostatChange(setpoint=Decimal('28.0')),
                error='timeout',
                error_code='device_unavailable',
                occurred_at=MORNING,
            ),
        ],
    )


@pytest.fixture
def f_flags_off_cycle(
    request: pytest.FixtureRequest,
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    f_thermostat: FloorThermostatStub,
    f_guard_thermostat: FloorThermostatStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    kitchen_setpoint, bathroom_setpoint = request.param
    _learned(_air_zone())
    _house_settings()
    store.record_state(KITCHEN, _state(kitchen_setpoint))
    store.record_state(BATHROOM, _state(bathroom_setpoint))
    _read(temperatures, HOUSE_AIR)
    return _use_case(store, temperatures, {KITCHEN: f_thermostat, BATHROOM: f_guard_thermostat}, book, dispatcher)


@pytest.fixture
def observed_thermostats(
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    f_thermostat: FloorThermostatStub,
    f_guard_thermostat: FloorThermostatStub,
) -> dict[str, FloorThermostat]:
    _learned(_air_zone())
    _house_settings()
    store.record_state(KITCHEN, _state('28.0'))
    store.record_state(BATHROOM, _state('30.0'))
    _read(temperatures, HOUSE_AIR)
    return {KITCHEN: f_thermostat, BATHROOM: f_guard_thermostat}


@pytest.fixture
def f_observed_air_cycle(
    observed_thermostats: dict[str, FloorThermostat],
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    return _use_case(store, temperatures, observed_thermostats, book, dispatcher)


@pytest.fixture
def f_journaled_air_cycle(
    observed_thermostats: dict[str, FloorThermostat],
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    _use_case(store, temperatures, observed_thermostats, book, EventDispatcher()).execute(_at(MORNING))
    return _use_case(store, temperatures, observed_thermostats, book, dispatcher)


@pytest.fixture
def f_warmed_air_cycle(
    f_journaled_air_cycle: RunHeatingCycleUseCase,
    temperatures: TemperatureStateStore,
) -> RunHeatingCycleUseCase:
    temperatures.record_state('air-kitchen', _reading('22.0'))
    return f_journaled_air_cycle


@pytest.fixture
def f_held_back_air_cycle(
    f_warmed_air_cycle: RunHeatingCycleUseCase,
    observed_thermostats: dict[str, FloorThermostat],
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
) -> RunHeatingCycleUseCase:
    _use_case(store, temperatures, observed_thermostats, book, EventDispatcher()).execute(
        _at(MORNING + timedelta(minutes=10)),
    )
    return f_warmed_air_cycle


@pytest.fixture
def night_journaled_thermostats(
    observed_thermostats: dict[str, FloorThermostat],
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
) -> dict[str, FloorThermostat]:
    _use_case(store, temperatures, observed_thermostats, book, EventDispatcher()).execute(
        _at(FIXED_START - timedelta(minutes=30)),
    )
    return observed_thermostats


@pytest.fixture
def f_optimal_start_dry_run_cycle(
    night_journaled_thermostats: dict[str, FloorThermostat],
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    return _use_case(store, temperatures, night_journaled_thermostats, book, dispatcher)


@pytest.fixture
def f_optimal_start_journaled_cycle(
    night_journaled_thermostats: dict[str, FloorThermostat],
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    _use_case(store, temperatures, night_journaled_thermostats, book, EventDispatcher()).execute(_at(OPTIMAL_START))
    return _use_case(store, temperatures, night_journaled_thermostats, book, dispatcher)


@pytest.fixture
def f_floor_preheat_cycle(
    night_journaled_thermostats: dict[str, FloorThermostat],
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    _use_case(store, temperatures, night_journaled_thermostats, book, EventDispatcher()).execute(_at(FIXED_START))
    temperatures.record_state('air-kitchen', _reading('20.8'))
    return _use_case(store, temperatures, night_journaled_thermostats, book, dispatcher)


@pytest.fixture
def f_live_air_cycle(
    request: pytest.FixtureRequest,
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    f_thermostat: FloorThermostatStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    air, setpoint = request.param
    _air_zone(air_control_live=True)
    store.record_state(KITCHEN, _state(setpoint))
    temperatures.record_state('air-kitchen', _reading(air))
    return _use_case(store, temperatures, {KITCHEN: f_thermostat}, book, dispatcher)


@pytest.fixture
def f_sensor_back_cycle(
    clock: MonotonicClockStub,
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    f_thermostat: FloorThermostatStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    ZoneLearningFactory.create(zone=_air_zone(air_control_live=True), early_stop_offset=Decimal('0.10'))
    thermostats: dict[str, FloorThermostat] = {KITCHEN: f_thermostat}
    store.record_state(KITCHEN, _state('28.0'))
    temperatures.record_state('air-kitchen', _reading('21.5'))
    _use_case(store, temperatures, thermostats, book, EventDispatcher()).execute(_at(MORNING))
    clock.advance(STALE.total_seconds())
    store.record_state(KITCHEN, _state('30.0'))
    _use_case(store, temperatures, thermostats, book, EventDispatcher()).execute(_at(MORNING + timedelta(minutes=30)))
    store.record_state(KITCHEN, _state('28.0'))
    temperatures.record_state('air-kitchen', _reading('21.8'))
    return _use_case(store, temperatures, thermostats, book, dispatcher)


@pytest.fixture
def f_optimal_start_cycle(
    request: pytest.FixtureRequest,
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    f_thermostat: FloorThermostatStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    mornings, rate, outdoor = request.param
    _learned(_air_zone(air_control_live=True), mornings=mornings, rate=rate)
    ClimateSettingsFactory.create(outdoor_sensor=_sensor('outdoor'))
    store.record_state(KITCHEN, _state('18.0'))
    temperatures.record_state('air-kitchen', _reading('19.4'))
    if outdoor is not None:
        temperatures.record_state('outdoor', _reading(outdoor))
    return _use_case(store, temperatures, {KITCHEN: f_thermostat}, book, dispatcher)


@pytest.fixture
def f_latched_preheat_cycle(
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    f_thermostat: FloorThermostatStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    _learned(_air_zone(air_control_live=True))
    ClimateSettingsFactory.create(outdoor_sensor=_sensor('outdoor'))
    thermostats: dict[str, FloorThermostat] = {KITCHEN: f_thermostat}
    store.record_state(KITCHEN, _state('18.0'))
    _read(temperatures, {'air-kitchen': '20.4', 'outdoor': '-3.4'})
    _use_case(store, temperatures, thermostats, book, EventDispatcher()).execute(_at(OPTIMAL_START))
    store.record_state(KITCHEN, _state('30.0'))
    temperatures.record_state('air-kitchen', _reading('21.6'))
    return _use_case(store, temperatures, thermostats, book, dispatcher)


@pytest.fixture
def f_clock_doubted_preheat_cycle(
    f_latched_preheat_cycle: RunHeatingCycleUseCase,
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    f_thermostat: FloorThermostatStub,
) -> RunHeatingCycleUseCase:
    temperatures.record_state('air-kitchen', _reading('21.5'))
    _use_case(store, temperatures, {KITCHEN: f_thermostat}, book, EventDispatcher()).execute(
        _at(COMFORT_AT - timedelta(hours=1, minutes=30), clock_trusted=False),
    )
    return f_latched_preheat_cycle


@pytest.fixture
def f_guard_cycle(
    request: pytest.FixtureRequest,
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    f_guard_thermostat: FloorThermostatStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    live, active, air, setpoint = request.param
    _house_settings(guard_live=live)
    store.record_state(BATHROOM, _state(setpoint))
    if air is not None:
        temperatures.record_state('air-second-floor', _reading(air))
    book.remember_guard(active)
    return _use_case(store, temperatures, {BATHROOM: f_guard_thermostat}, book, dispatcher)


@pytest.fixture
def morning_thermostats(
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    f_thermostat: FloorThermostatStub,
) -> dict[str, FloorThermostat]:
    _air_zone()
    ClimateSettingsFactory.create(outdoor_sensor=_sensor('outdoor'))
    thermostats: dict[str, FloorThermostat] = {KITCHEN: f_thermostat}
    store.record_state(KITCHEN, _state('28.0'))
    temperatures.record_state('outdoor', _reading('-3.4'))
    for at, air in MORNING_AIR:
        temperatures.record_state('air-kitchen', _reading(air))
        _use_case(store, temperatures, thermostats, book, EventDispatcher()).execute(_at(at))
    return thermostats


@pytest.fixture
def f_morning_cycle(
    morning_thermostats: dict[str, FloorThermostat],
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    return _use_case(store, temperatures, morning_thermostats, book, dispatcher)


@pytest.fixture
def f_air_mode_morning_cycle(
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    f_thermostat: FloorThermostatStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    _learned(_air_zone(air_control_live=True))
    ClimateSettingsFactory.create(outdoor_sensor=_sensor('outdoor'))
    thermostats: dict[str, FloorThermostat] = {KITCHEN: f_thermostat}
    temperatures.record_state('outdoor', _reading('-3.4'))
    for at, air, setpoint in AIR_MODE_MORNING_AIR:
        store.record_state(KITCHEN, _state(setpoint))
        temperatures.record_state('air-kitchen', _reading(air))
        _use_case(store, temperatures, thermostats, book, EventDispatcher()).execute(_at(at))
    temperatures.record_state('air-kitchen', _reading('21.7'))
    return _use_case(store, temperatures, thermostats, book, dispatcher)


@pytest.fixture
def f_failed_preheat_cycle(
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    _air_zone()
    ClimateSettingsFactory.create(outdoor_sensor=_sensor('outdoor'))
    thermostats: dict[str, FloorThermostat] = {
        KITCHEN: FloorThermostatStub(state=None, error=DeviceUnavailableError('timeout')),
    }
    store.record_state(KITCHEN, _state('22.0'))
    temperatures.record_state('outdoor', _reading('-3.4'))
    for at, air in MORNING_AIR:
        temperatures.record_state('air-kitchen', _reading(air))
        _use_case(store, temperatures, thermostats, book, EventDispatcher()).execute(_at(at))
    return _use_case(store, temperatures, thermostats, book, dispatcher)


@pytest.fixture
def f_learned_morning_cycle(
    morning_thermostats: dict[str, FloorThermostat],
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    _use_case(store, temperatures, morning_thermostats, book, EventDispatcher()).execute(_at(COMFORT_AT))
    return _use_case(store, temperatures, morning_thermostats, book, dispatcher)


@pytest.fixture
def stop_thermostats(
    store: ThermostatStateStore,
    book: ControlBook,
    f_thermostat: FloorThermostatStub,
) -> dict[str, FloorThermostat]:
    _air_zone(air_control_live=True)
    book.remember_zone(
        'kitchen',
        ZoneMemory(
            path=ControlPath.AIR,
            mode=ScheduleMode.COMFORT,
            air_state=AirState.HEAT,
            heat_target=Decimal('22.0'),
        ),
    )
    store.record_state(KITCHEN, _state('30.0'))
    return {KITCHEN: f_thermostat}


@pytest.fixture
def f_watched_stop_cycle(
    stop_thermostats: dict[str, FloorThermostat],
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    _watch_stop(store, temperatures, stop_thermostats, book, dry_run=False)
    return _use_case(store, temperatures, stop_thermostats, book, dispatcher)


@pytest.fixture
def f_learned_stop_cycle(
    f_watched_stop_cycle: RunHeatingCycleUseCase,
    stop_thermostats: dict[str, FloorThermostat],
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
) -> RunHeatingCycleUseCase:
    _use_case(store, temperatures, stop_thermostats, book, EventDispatcher()).execute(
        _at(MORNING + timedelta(hours=2, minutes=1)),
    )
    return f_watched_stop_cycle


@pytest.fixture
def f_dry_run_stop_cycle(
    stop_thermostats: dict[str, FloorThermostat],
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    _watch_stop(store, temperatures, stop_thermostats, book, dry_run=True)
    return _use_case(store, temperatures, stop_thermostats, book, dispatcher, dry_run=True)


@pytest.mark.parametrize(
    ('f_flags_off_cycle', 'now', 'kitchen', 'bathroom'),
    [
        (('28.0', '30.0'), NIGHT, Decimal('22.0'), Decimal('22.0')),
        (('22.0', '22.0'), FIXED_START + timedelta(minutes=15), Decimal('28.0'), Decimal('30.0')),
        (('22.0', '22.0'), MORNING, Decimal('28.0'), Decimal('30.0')),
    ],
    ids=['night', 'preheat_on_fixed_lead_before_optimal_start', 'comfort'],
    indirect=['f_flags_off_cycle'],
)
def test_cycle_with_air_control_and_guard_off_writes_floor_mode_setpoints(
    f_flags_off_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
    f_guard_thermostat: FloorThermostatStub,
    now: datetime,
    kitchen: Decimal,
    bathroom: Decimal,
) -> None:
    output = f_flags_off_cycle.execute(_at(now))

    assert (output.outcomes, f_thermostat.commands, f_guard_thermostat.commands) == (
        {'bathroom-2f': ZoneCycleOutcome.APPLIED, 'kitchen': ZoneCycleOutcome.APPLIED},
        [('setpoint', kitchen)],
        [('setpoint', bathroom)],
    )


def test_cycle_with_air_control_off_journals_would_be_air_state_and_guard(
    f_observed_air_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
    f_guard_thermostat: FloorThermostatStub,
    f_events: list[DomainEvent],
) -> None:
    output = f_observed_air_cycle.execute(_at(MORNING))

    assert (
        output.outcomes,
        f_thermostat.commands,
        f_guard_thermostat.commands,
        f_events,
        Zone.objects.get(slug='kitchen').last_air_dry_run_at,
    ) == (
        {'bathroom-2f': ZoneCycleOutcome.UNCHANGED, 'kitchen': ZoneCycleOutcome.UNCHANGED},
        [],
        [],
        [
            SecondFloorGuardDryRun(
                zone='bathroom-2f',
                active=True,
                air=Decimal('18.5'),
                setpoint=Decimal('30.0'),
                occurred_at=MORNING,
            ),
            AirControlDryRun(
                zone='kitchen',
                air=Decimal('20.4'),
                target=Decimal('22.0'),
                mode=ScheduleMode.COMFORT,
                state=AirState.HEAT,
                previous_state=None,
                power=True,
                setpoint=Decimal('30.0'),
                early_stop_offset=Decimal('0.30'),
                lead_minutes=None,
                occurred_at=MORNING,
            ),
        ],
        MORNING,
    )


def test_air_dry_run_journals_nothing_while_would_be_state_holds(
    f_journaled_air_cycle: RunHeatingCycleUseCase,
    f_events: list[DomainEvent],
) -> None:
    f_journaled_air_cycle.execute(_at(MORNING + MINUTE))

    assert f_events == []


def test_air_dry_run_holds_would_be_change_within_thirty_minutes(
    f_warmed_air_cycle: RunHeatingCycleUseCase,
    f_events: list[DomainEvent],
) -> None:
    f_warmed_air_cycle.execute(_at(MORNING + timedelta(minutes=10)))

    assert (f_events, Zone.objects.get(slug='kitchen').last_air_dry_run_at) == ([], MORNING)


def test_air_dry_run_journals_would_be_change_thirty_minutes_after_the_last(
    f_held_back_air_cycle: RunHeatingCycleUseCase,
    f_events: list[DomainEvent],
) -> None:
    now = MORNING + timedelta(minutes=30)

    f_held_back_air_cycle.execute(_at(now))

    assert (f_events, Zone.objects.get(slug='kitchen').last_air_dry_run_at) == (
        [
            AirControlDryRun(
                zone='kitchen',
                air=Decimal('22.0'),
                target=Decimal('22.0'),
                mode=ScheduleMode.COMFORT,
                state=AirState.IDLE,
                previous_state=AirState.HEAT,
                power=True,
                setpoint=Decimal('18.0'),
                early_stop_offset=Decimal('0.30'),
                lead_minutes=None,
                occurred_at=now,
            ),
        ],
        now,
    )


def test_air_dry_run_journals_optimal_start_it_would_use(
    f_optimal_start_dry_run_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
    f_events: list[DomainEvent],
) -> None:
    f_optimal_start_dry_run_cycle.execute(_at(OPTIMAL_START))

    assert (f_thermostat.commands, f_events) == (
        [('setpoint', Decimal('22.0'))],
        [
            OptimalStartDryRun(
                zone='kitchen',
                lead_minutes=160,
                start=OPTIMAL_START,
                fixed_start=FIXED_START,
                occurred_at=OPTIMAL_START,
            ),
            AirControlDryRun(
                zone='kitchen',
                air=Decimal('20.4'),
                target=Decimal('22.0'),
                mode=ScheduleMode.PREHEAT,
                state=AirState.HEAT,
                previous_state=AirState.IDLE,
                power=True,
                setpoint=Decimal('30.0'),
                early_stop_offset=Decimal('0.30'),
                lead_minutes=160,
                occurred_at=OPTIMAL_START,
            ),
        ],
    )


def test_air_dry_run_journals_optimal_start_once_per_morning(
    f_optimal_start_journaled_cycle: RunHeatingCycleUseCase,
    f_events: list[DomainEvent],
) -> None:
    f_optimal_start_journaled_cycle.execute(_at(OPTIMAL_START + MINUTE))

    assert f_events == []


def test_air_dry_run_journals_later_optimal_start_when_floor_mode_preheat_begins(
    f_optimal_start_dry_run_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
    f_events: list[DomainEvent],
) -> None:
    f_optimal_start_dry_run_cycle.execute(_at(FIXED_START))

    assert (f_thermostat.commands, f_events) == (
        [('setpoint', Decimal('22.0'))],
        [
            OptimalStartDryRun(
                zone='kitchen',
                lead_minutes=160,
                start=OPTIMAL_START,
                fixed_start=FIXED_START,
                occurred_at=FIXED_START,
            ),
        ],
    )


def test_air_dry_run_preheats_on_start_held_before_floor_mode_warmed_room(
    f_floor_preheat_cycle: RunHeatingCycleUseCase,
    f_events: list[DomainEvent],
) -> None:
    f_floor_preheat_cycle.execute(_at(OPTIMAL_START))

    assert f_events == [
        AirControlDryRun(
            zone='kitchen',
            air=Decimal('20.8'),
            target=Decimal('22.0'),
            mode=ScheduleMode.PREHEAT,
            state=AirState.HEAT,
            previous_state=AirState.IDLE,
            power=True,
            setpoint=Decimal('30.0'),
            early_stop_offset=Decimal('0.30'),
            lead_minutes=160,
            occurred_at=OPTIMAL_START,
        ),
    ]


@pytest.mark.parametrize(
    ('f_live_air_cycle', 'now', 'setpoint'),
    [
        (('21.5', '28.0'), MORNING, Decimal('30.0')),
        (('21.8', '28.0'), MORNING, Decimal('18.0')),
        (('19.1', '22.0'), NIGHT, Decimal('30.0')),
        (('19.4', '22.0'), NIGHT, Decimal('18.0')),
    ],
    ids=['comfort_heat_at_floor_limit', 'comfort_idle', 'night_heat_at_floor_limit', 'night_idle'],
    indirect=['f_live_air_cycle'],
)
def test_live_air_zone_drives_floor_between_limit_and_idle_setpoint(
    f_live_air_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
    f_events: list[DomainEvent],
    now: datetime,
    setpoint: Decimal,
) -> None:
    output = f_live_air_cycle.execute(_at(now))

    assert (output.outcomes, f_thermostat.commands, f_events) == (
        {'kitchen': ZoneCycleOutcome.APPLIED},
        [('setpoint', setpoint)],
        [
            ZoneCommandIssued(
                zone='kitchen',
                change=ThermostatChange(setpoint=setpoint),
                dry_run=False,
                occurred_at=now,
            ),
        ],
    )


@pytest.mark.parametrize('f_live_air_cycle', [('21.5', '18.0')], indirect=True)
def test_live_air_zone_holds_comfort_air_target_while_clock_untrusted(
    f_live_air_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
) -> None:
    f_live_air_cycle.execute(_at(NIGHT, clock_trusted=False))

    assert f_thermostat.commands == [('setpoint', Decimal('30.0'))]


def test_live_air_zone_falls_back_to_floor_mode_and_reenters_air_mode_idle(
    f_sensor_back_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
) -> None:
    output = f_sensor_back_cycle.execute(_at(MORNING + timedelta(hours=1)))

    assert (output.outcomes, f_thermostat.commands) == (
        {'kitchen': ZoneCycleOutcome.APPLIED},
        [('setpoint', Decimal('30.0')), ('setpoint', Decimal('28.0')), ('setpoint', Decimal('18.0'))],
    )


@pytest.mark.parametrize(
    ('f_optimal_start_cycle', 'now', 'commands'),
    [
        ((2, '0.800', '-3.4'), FIXED_START - MINUTE, []),
        ((2, '0.800', '-3.4'), FIXED_START, [('setpoint', Decimal('30.0'))]),
        ((3, '0.800', None), FIXED_START - MINUTE, []),
        ((3, '0.800', None), FIXED_START, [('setpoint', Decimal('30.0'))]),
        ((3, '0.800', '-3.4'), COMFORT_AT - timedelta(hours=3, minutes=56), []),
        ((3, '0.800', '-3.4'), COMFORT_AT - timedelta(hours=3, minutes=55), [('setpoint', Decimal('30.0'))]),
        ((3, '0.500', '-3.4'), COMFORT_AT - timedelta(hours=4, minutes=1), []),
        ((3, '0.500', '-3.4'), COMFORT_AT - timedelta(hours=4), [('setpoint', Decimal('30.0'))]),
    ],
    ids=[
        'fixed_lead_until_three_mornings_before_start',
        'fixed_lead_until_three_mornings_at_start',
        'fixed_lead_without_outdoor_reading_before_start',
        'fixed_lead_without_outdoor_reading_at_start',
        'learned_lead_before_start',
        'learned_lead_at_start',
        'capped_lead_before_start',
        'capped_lead_at_start',
    ],
    indirect=['f_optimal_start_cycle'],
)
def test_live_air_zone_starts_preheat_on_optimal_lead(
    f_optimal_start_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
    now: datetime,
    commands: list[tuple[str, object]],
) -> None:
    f_optimal_start_cycle.execute(_at(now))

    assert f_thermostat.commands == commands


def test_live_air_zone_keeps_preheat_started_while_room_warms(
    f_latched_preheat_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
) -> None:
    output = f_latched_preheat_cycle.execute(_at(COMFORT_AT - timedelta(hours=1, minutes=20)))

    assert (output.outcomes, f_thermostat.commands) == (
        {'kitchen': ZoneCycleOutcome.UNCHANGED},
        [('setpoint', Decimal('30.0'))],
    )


def test_live_air_zone_keeps_preheat_started_through_a_minute_of_doubted_clock(
    f_clock_doubted_preheat_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
) -> None:
    output = f_clock_doubted_preheat_cycle.execute(_at(COMFORT_AT - timedelta(hours=1, minutes=29)))

    assert (output.outcomes, f_thermostat.commands) == (
        {'kitchen': ZoneCycleOutcome.UNCHANGED},
        [('setpoint', Decimal('30.0'))],
    )


@pytest.mark.parametrize(
    ('f_guard_cycle', 'commands'),
    [
        ((True, False, '18.9', '22.0'), [('setpoint', Decimal('30.0'))]),
        ((True, True, '19.2', '30.0'), []),
        ((True, True, '19.3', '30.0'), [('setpoint', Decimal('22.0'))]),
        ((True, True, None, '30.0'), [('setpoint', Decimal('22.0'))]),
    ],
    ids=['turns_on_below_threshold', 'holds_within_hysteresis', 'releases_above_hysteresis', 'releases_on_lost_sensor'],
    indirect=['f_guard_cycle'],
)
def test_live_guard_raises_its_zone_to_comfort_floor_setpoint_with_hysteresis(
    f_guard_cycle: RunHeatingCycleUseCase,
    f_guard_thermostat: FloorThermostatStub,
    f_events: list[DomainEvent],
    commands: list[tuple[str, Decimal]],
) -> None:
    f_guard_cycle.execute(_at(NIGHT))

    assert (f_guard_thermostat.commands, f_events) == (
        commands,
        [
            ZoneCommandIssued(
                zone='bathroom-2f',
                change=ThermostatChange(setpoint=setpoint),
                dry_run=False,
                occurred_at=NIGHT,
            )
            for _, setpoint in commands
        ],
    )


@pytest.mark.parametrize(
    ('f_guard_cycle', 'events'),
    [
        (
            (False, False, '18.9', '22.0'),
            [
                SecondFloorGuardDryRun(
                    zone='bathroom-2f',
                    active=True,
                    air=Decimal('18.9'),
                    setpoint=Decimal('30.0'),
                    occurred_at=NIGHT,
                ),
            ],
        ),
        ((False, True, '19.2', '22.0'), []),
        (
            (False, True, '19.3', '22.0'),
            [
                SecondFloorGuardDryRun(
                    zone='bathroom-2f',
                    active=False,
                    air=Decimal('19.3'),
                    setpoint=Decimal('30.0'),
                    occurred_at=NIGHT,
                ),
            ],
        ),
        (
            (False, True, None, '22.0'),
            [
                SecondFloorGuardDryRun(
                    zone='bathroom-2f',
                    active=False,
                    air=None,
                    setpoint=Decimal('30.0'),
                    occurred_at=NIGHT,
                ),
            ],
        ),
    ],
    ids=['would_turn_on', 'would_hold_within_hysteresis', 'would_release', 'would_release_on_lost_sensor'],
    indirect=['f_guard_cycle'],
)
def test_guard_in_dry_run_journals_when_it_would_turn_on_or_off(
    f_guard_cycle: RunHeatingCycleUseCase,
    f_guard_thermostat: FloorThermostatStub,
    f_events: list[DomainEvent],
    events: list[DomainEvent],
) -> None:
    f_guard_cycle.execute(_at(NIGHT))

    assert (f_guard_thermostat.commands, f_events) == ([], events)


def test_floor_mode_preheat_teaches_air_zone_its_morning(f_morning_cycle: RunHeatingCycleUseCase) -> None:
    f_morning_cycle.execute(_at(COMFORT_AT))

    learning = ZoneLearning.objects.get(zone__slug='kitchen')
    assert (learning.dead_time, learning.heat_up_rates, learning.mornings) == (
        timedelta(minutes=40),
        {'-5': '0.777'},
        1,
    )


def test_air_mode_morning_learns_its_rate_until_air_mode_stops_heating(
    f_air_mode_morning_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
) -> None:
    f_air_mode_morning_cycle.execute(_at(AIR_MODE_STOP))

    learning = ZoneLearning.objects.get(zone__slug='kitchen')
    assert (f_thermostat.commands, learning.dead_time, learning.heat_up_rates, learning.mornings) == (
        [('setpoint', Decimal('30.0')), ('setpoint', Decimal('18.0'))],
        timedelta(minutes=40),
        {'-5': '0.860'},
        4,
    )


def test_preheat_whose_write_fails_teaches_no_morning(f_failed_preheat_cycle: RunHeatingCycleUseCase) -> None:
    f_failed_preheat_cycle.execute(_at(COMFORT_AT))

    assert not ZoneLearning.objects.exists()


def test_morning_is_learned_once(f_learned_morning_cycle: RunHeatingCycleUseCase) -> None:
    f_learned_morning_cycle.execute(_at(COMFORT_AT + MINUTE))

    assert ZoneLearning.objects.get(zone__slug='kitchen').mornings == 1


def test_stop_of_live_air_heat_teaches_early_stop_offset_from_two_hour_peak(
    f_watched_stop_cycle: RunHeatingCycleUseCase,
) -> None:
    f_watched_stop_cycle.execute(_at(MORNING + timedelta(hours=2, minutes=1)))

    assert ZoneLearning.objects.get(zone__slug='kitchen').early_stop_offset == Decimal('0.39')


def test_stop_is_learned_once(f_learned_stop_cycle: RunHeatingCycleUseCase) -> None:
    f_learned_stop_cycle.execute(_at(MORNING + timedelta(hours=2, minutes=2)))

    assert ZoneLearning.objects.get(zone__slug='kitchen').early_stop_offset == Decimal('0.39')


def test_stop_in_dry_run_teaches_nothing(f_dry_run_stop_cycle: RunHeatingCycleUseCase) -> None:
    f_dry_run_stop_cycle.execute(_at(MORNING + timedelta(hours=2, minutes=1)))

    assert not ZoneLearning.objects.exists()


@pytest.fixture
def f_living_room_thermostat() -> FloorThermostatStub:
    return FloorThermostatStub(state=None)


@pytest.fixture
def f_held_gate() -> LoadGateStub:
    return LoadGateStub(held=frozenset({'kitchen', 'bathroom-2f'}), answers={})


@pytest.fixture
def f_guard_book() -> GuardBook:
    return GuardBook()


@pytest.fixture
def f_gate_clock() -> MonotonicClockStub:
    return MonotonicClockStub(now=0.0)


@pytest.fixture
def load_gate(f_guard_book: GuardBook, f_gate_clock: MonotonicClockStub) -> GuardLoadGate:
    return GuardLoadGate(book=f_guard_book, clock=f_gate_clock)


@pytest.fixture
def f_held_cycle(
    request: pytest.FixtureRequest,
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    f_thermostat: FloorThermostatStub,
    f_guard_thermostat: FloorThermostatStub,
    f_held_gate: LoadGateStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    house: House = request.param
    house(store, temperatures)
    thermostats: dict[str, FloorThermostat] = {KITCHEN: f_thermostat, BATHROOM: f_guard_thermostat}
    return _use_case(store, temperatures, thermostats, book, dispatcher, gate=f_held_gate)


@pytest.fixture
def f_restored_cycle(f_thermostat: FloorThermostatStub, dispatcher: EventDispatcher) -> RunHeatingCycleUseCase:
    zone = _floor_zone('kitchen', KITCHEN, last_command_at=MORNING - timedelta(minutes=10))
    return _cycle(zone, _state('22.0', power=False), f_thermostat, dispatcher)


@pytest.fixture
def f_load_management_off_cycle(
    request: pytest.FixtureRequest,
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    f_thermostat: FloorThermostatStub,
    f_guard_book: GuardBook,
    load_gate: GuardLoadGate,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    house, management_live, load_live, meter, l2 = request.param
    house(store, temperatures)
    _watch(f_guard_book, management_live=management_live, load_live=load_live, meter=meter, l2=l2)
    return _use_case(store, temperatures, {KITCHEN: f_thermostat}, book, dispatcher, gate=load_gate)


@pytest.fixture
def f_live_start_cycle(
    request: pytest.FixtureRequest,
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    f_thermostat: FloorThermostatStub,
    f_guard_book: GuardBook,
    load_gate: GuardLoadGate,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    meter, l2, readings = request.param
    _idle_floor_zone(store, temperatures)
    _watch(f_guard_book, management_live=True, meter=meter, l2=l2, readings=readings)
    return _use_case(store, temperatures, {KITCHEN: f_thermostat}, book, dispatcher, gate=load_gate)


@pytest.fixture
def f_two_starts_cycle(
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    f_thermostat: FloorThermostatStub,
    f_living_room_thermostat: FloorThermostatStub,
    f_guard_book: GuardBook,
    load_gate: GuardLoadGate,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    _idle_floor_zone(store, temperatures)
    _floor_zone('living-room', LIVING_ROOM)
    store.record_state(LIVING_ROOM, _state('22.0', heating=False))
    _watch(f_guard_book, management_live=True)
    thermostats: dict[str, FloorThermostat] = {KITCHEN: f_thermostat, LIVING_ROOM: f_living_room_thermostat}
    return _use_case(store, temperatures, thermostats, book, dispatcher, gate=load_gate)


@pytest.mark.parametrize(
    ('f_held_cycle', 'now', 'zone', 'commands'),
    [
        (_shed_floor_zone, MORNING, 'kitchen', [('power', True), ('setpoint', Decimal('28.0'))]),
        (_shed_air_zone, MORNING, 'kitchen', [('power', True), ('setpoint', Decimal('30.0'))]),
        (_shed_boosted_zone, MORNING, 'kitchen', [('power', True), ('setpoint', Decimal('30.0'))]),
        (_shed_guarded_zone, NIGHT, 'bathroom-2f', [('power', True), ('setpoint', Decimal('30.0'))]),
    ],
    ids=['floor_mode', 'air_mode', 'boost', 'second_floor_guard'],
    indirect=['f_held_cycle'],
)
def test_shed_zone_is_held_on_every_path_and_gets_its_target_once_restored(
    f_held_cycle: RunHeatingCycleUseCase,
    f_held_gate: LoadGateStub,
    f_thermostat: FloorThermostatStub,
    f_guard_thermostat: FloorThermostatStub,
    f_events: list[DomainEvent],
    now: datetime,
    zone: str,
    commands: list[tuple[str, object]],
) -> None:
    held = f_held_cycle.execute(_at(now))
    held_writes = (f_thermostat.commands + f_guard_thermostat.commands, list(f_events))
    held_stamp = Zone.objects.get(slug=zone).last_command_at
    f_held_gate.release(zone)
    restored = f_held_cycle.execute(_at(now))

    assert (
        held.outcomes,
        held_writes,
        held_stamp,
        restored.outcomes,
        f_thermostat.commands + f_guard_thermostat.commands,
        Zone.objects.get(slug=zone).last_command_at,
        f_held_gate.asked,
    ) == (
        {zone: ZoneCycleOutcome.SHED},
        ([], []),
        now - HALF_HOUR,
        {zone: ZoneCycleOutcome.APPLIED},
        commands,
        now,
        [(), (ZoneStart(zone=zone, writes_live=True),)],
    )


def test_restored_zone_gets_its_target_no_sooner_than_thirty_minutes_after_its_shed(
    f_restored_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
) -> None:
    output = f_restored_cycle.execute(_at(MORNING))

    assert (output.outcomes, f_thermostat.commands) == ({'kitchen': ZoneCycleOutcome.THROTTLED}, [])


@pytest.mark.parametrize(
    ('f_load_management_off_cycle', 'setpoint'),
    [
        ((_idle_floor_zone, False, False, FRESH_METER, '13.0'), Decimal('28.0')),
        ((_idle_air_zone, False, False, FRESH_METER, '13.0'), Decimal('30.0')),
        ((_idle_boosted_zone, False, False, FRESH_METER, '13.0'), Decimal('30.0')),
        ((_idle_floor_zone, True, False, FRESH_METER, '13.0'), Decimal('28.0')),
        ((_idle_floor_zone, False, True, FRESH_METER, '13.0'), Decimal('28.0')),
    ],
    ids=['floor_mode', 'air_mode', 'boost', 'load_off', 'load_management_off'],
    indirect=['f_load_management_off_cycle'],
)
def test_start_that_would_wait_with_load_management_off_is_written_as_before_and_journaled(
    f_load_management_off_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
    f_guard_book: GuardBook,
    f_events: list[DomainEvent],
    setpoint: Decimal,
) -> None:
    output = f_load_management_off_cycle.execute(_at(MORNING))

    zone = Zone.objects.get(slug='kitchen')
    assert (
        output.outcomes,
        f_thermostat.commands,
        zone.last_command_at,
        zone.last_dry_run_at,
        f_events,
        f_guard_book.last_permit_at(),
    ) == (
        {'kitchen': ZoneCycleOutcome.APPLIED},
        [('setpoint', setpoint)],
        MORNING,
        None,
        [
            StartWouldWaitDryRun(
                load='kitchen',
                name='Кухня',
                phase=LoadPhase.L2,
                amps=Decimal('21.0'),
                load_amps=Decimal('8.0'),
                reason=StartRefusal.NO_HEADROOM,
                occurred_at=MORNING,
            ),
            ZoneCommandIssued(
                zone='kitchen',
                change=ThermostatChange(setpoint=setpoint),
                dry_run=False,
                occurred_at=MORNING,
            ),
        ],
        None,
    )


@pytest.mark.parametrize(
    ('f_load_management_off_cycle', 'setpoint'),
    [
        ((_idle_floor_zone, False, False, FRESH_METER, '10.0'), Decimal('28.0')),
        ((_idle_air_zone, False, False, FRESH_METER, '10.0'), Decimal('30.0')),
        ((_idle_boosted_zone, False, False, FRESH_METER, '10.0'), Decimal('30.0')),
        ((_idle_floor_zone, False, False, UNSEEN_METER, '10.0'), Decimal('28.0')),
        ((_idle_air_zone, False, False, UNSEEN_METER, '10.0'), Decimal('30.0')),
        ((_idle_boosted_zone, False, False, UNSEEN_METER, '10.0'), Decimal('30.0')),
    ],
    ids=[
        'floor_mode_with_room',
        'air_mode_with_room',
        'boost_with_room',
        'floor_mode_before_the_meter_reports',
        'air_mode_before_the_meter_reports',
        'boost_before_the_meter_reports',
    ],
    indirect=['f_load_management_off_cycle'],
)
def test_cycle_with_load_management_off_writes_stamps_and_journals_exactly_as_before(
    f_load_management_off_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
    f_guard_book: GuardBook,
    f_events: list[DomainEvent],
    setpoint: Decimal,
) -> None:
    output = f_load_management_off_cycle.execute(_at(MORNING))

    zone = Zone.objects.get(slug='kitchen')
    assert (
        output.outcomes,
        f_thermostat.commands,
        zone.last_command_at,
        zone.last_dry_run_at,
        zone.last_air_dry_run_at,
        f_events,
        f_guard_book.last_permit_at(),
    ) == (
        {'kitchen': ZoneCycleOutcome.APPLIED},
        [('setpoint', setpoint)],
        MORNING,
        None,
        None,
        [
            ZoneCommandIssued(
                zone='kitchen',
                change=ThermostatChange(setpoint=setpoint),
                dry_run=False,
                occurred_at=MORNING,
            ),
        ],
        None,
    )


@pytest.mark.parametrize(
    ('f_live_start_cycle', 'outcome', 'commands', 'stamp', 'last_permit_at'),
    [
        ((FRESH_METER, '12.0', None), ZoneCycleOutcome.APPLIED, [('setpoint', Decimal('28.0'))], MORNING, 0.0),
        ((FRESH_METER, '12.5', None), ZoneCycleOutcome.WAITING, [], None, None),
    ],
    ids=['permit_granted', 'permit_refused'],
    indirect=['f_live_start_cycle'],
)
def test_live_start_is_written_only_with_a_permit_and_keeps_the_write_budget_while_it_waits(
    f_live_start_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
    f_guard_book: GuardBook,
    f_events: list[DomainEvent],
    outcome: ZoneCycleOutcome,
    commands: list[tuple[str, Decimal]],
    stamp: datetime | None,
    last_permit_at: float | None,
) -> None:
    output = f_live_start_cycle.execute(_at(MORNING))

    assert (
        output.outcomes,
        f_thermostat.commands,
        Zone.objects.get(slug='kitchen').last_command_at,
        f_guard_book.last_permit_at(),
        f_events,
    ) == (
        {'kitchen': outcome},
        commands,
        stamp,
        last_permit_at,
        [
            ZoneCommandIssued(
                zone='kitchen',
                change=ThermostatChange(setpoint=setpoint),
                dry_run=False,
                occurred_at=MORNING,
            )
            for _, setpoint in commands
        ],
    )


@pytest.mark.parametrize('f_live_start_cycle', [(FRESH_METER, '12.5', None)], indirect=True)
def test_waiting_start_asks_again_at_the_next_cycle(
    f_live_start_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
    f_guard_book: GuardBook,
) -> None:
    waiting = f_live_start_cycle.execute(_at(MORNING))
    _watch(f_guard_book, management_live=True, l2='12.0')
    started = f_live_start_cycle.execute(_at(MORNING + MINUTE))

    assert (waiting.outcomes, started.outcomes, f_thermostat.commands) == (
        {'kitchen': ZoneCycleOutcome.WAITING},
        {'kitchen': ZoneCycleOutcome.APPLIED},
        [('setpoint', Decimal('28.0'))],
    )


def test_live_starts_of_one_cycle_go_most_important_first_one_a_cycle(
    f_two_starts_cycle: RunHeatingCycleUseCase,
    f_gate_clock: MonotonicClockStub,
    f_thermostat: FloorThermostatStub,
    f_living_room_thermostat: FloorThermostatStub,
) -> None:
    first = f_two_starts_cycle.execute(_at(MORNING))
    f_gate_clock.advance(MINUTE.total_seconds())
    second = f_two_starts_cycle.execute(_at(MORNING + MINUTE))

    assert (first.outcomes, second.outcomes, f_living_room_thermostat.commands, f_thermostat.commands) == (
        {'kitchen': ZoneCycleOutcome.WAITING, 'living-room': ZoneCycleOutcome.APPLIED},
        {'kitchen': ZoneCycleOutcome.APPLIED, 'living-room': ZoneCycleOutcome.THROTTLED},
        [('setpoint', Decimal('28.0'))],
        [('setpoint', Decimal('28.0'))],
    )


@pytest.mark.parametrize(
    ('f_live_start_cycle', 'outcome', 'commands'),
    [
        ((MeterWatch(seen=True, stale_since=-59.0), '10.0', None), ZoneCycleOutcome.WAITING, []),
        ((MeterWatch(seen=False, stale_since=-59.0), '10.0', None), ZoneCycleOutcome.WAITING, []),
        (
            (MeterWatch(seen=True, stale_since=-60.0), '10.0', None),
            ZoneCycleOutcome.APPLIED,
            [('setpoint', Decimal('28.0'))],
        ),
        (
            (MeterWatch(seen=False, stale_since=-60.0), '10.0', None),
            ZoneCycleOutcome.APPLIED,
            [('setpoint', Decimal('28.0'))],
        ),
        (
            (MeterWatch(seen=True, stale_since=-60.0), '10.0', {'hallway': HALLWAY_HEATING}),
            ZoneCycleOutcome.WAITING,
            [],
        ),
    ],
    ids=[
        'first_minute_of_a_stale_meter',
        'first_minute_after_the_start_without_a_meter_reading',
        'lost_meter_with_no_floor_drawing_on_the_phase',
        'meter_silent_since_the_start_for_a_minute',
        'lost_meter_with_another_floor_drawing_on_the_phase',
    ],
    indirect=['f_live_start_cycle'],
)
def test_live_start_on_a_stale_meter_waits_a_minute_then_keeps_one_drawing_floor_a_phase(
    f_live_start_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
    outcome: ZoneCycleOutcome,
    commands: list[tuple[str, object]],
) -> None:
    output = f_live_start_cycle.execute(_at(MORNING))

    assert (output.outcomes, f_thermostat.commands) == ({'kitchen': outcome}, commands)
