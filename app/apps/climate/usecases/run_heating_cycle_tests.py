from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.db import OperationalError
from structlog.testing import capture_logs

from apps.climate.domain.change import ThermostatChange
from apps.climate.domain.events import (
    AirControlDryRun,
    OptimalStartDryRun,
    SecondFloorGuardDryRun,
    ZoneCommandFailed,
    ZoneCommandIssued,
)
from apps.climate.domain.floor_policy import ThermostatTarget
from apps.climate.domain.learning import AirSample, Morning
from apps.climate.domain.memory import ZoneMemory
from apps.climate.domain.modes import AirState, ControlPath, ScheduleMode, ZoneCycleOutcome, ZoneKind
from apps.climate.domain.observation import StopWatch
from apps.climate.domain.ports import ZoneLearningStore, ZoneStore
from apps.climate.domain.zone import ZoneSettings
from apps.climate.factories import ClimateSettingsFactory, ComfortPeriodFactory, ZoneFactory, ZoneLearningFactory
from apps.climate.models import ClimateSettings, Zone, ZoneLearning
from apps.climate.repositories.climate_settings import ClimateSettingsRepository
from apps.climate.repositories.zone import ZoneRepository
from apps.climate.repositories.zone_learning import ZoneLearningRepository
from apps.climate.services.control_book import ControlBook
from apps.climate.stubs import ZoneLearningStoreStub, ZoneStoreStub
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

pytestmark = pytest.mark.django_db

MOSCOW = ZoneInfo('Europe/Moscow')
MORNING = datetime(2026, 10, 29, 7, 0, tzinfo=MOSCOW)
PREHEAT = datetime(2026, 10, 29, 4, 0, tzinfo=MOSCOW)
NIGHT = datetime(2026, 10, 29, 1, 0, tzinfo=MOSCOW)
COMFORT_AT = datetime(2026, 10, 29, 6, 30, tzinfo=MOSCOW)
FIXED_START = datetime(2026, 10, 29, 3, 30, tzinfo=MOSCOW)
OPTIMAL_START = datetime(2026, 10, 29, 3, 50, tzinfo=MOSCOW)
CAPPED_START = COMFORT_AT - timedelta(hours=4)
BOOST_END = MORNING + timedelta(hours=2)
FRESH = timedelta(0)
MINUTE = timedelta(minutes=1)
UNAVAILABLE_AFTER = timedelta(minutes=10)
STALE = timedelta(minutes=11)
KITCHEN = 'thermostat-kitchen'
BATHROOM = 'thermostat-bathroom-2f'
LIVING_ROOM = 'thermostat-living-room'
LOCKED = 'database is locked'
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


def _state(setpoint: str, mode: ThermostatMode = ThermostatMode.HOLD, read_at: datetime = MORNING) -> ThermostatState:
    return ThermostatState(
        power=True,
        mode=mode,
        setpoint=Decimal(setpoint),
        floor_temperature=Decimal('21.0'),
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
    zones: ZoneStore | None = None,
    learning: ZoneLearningStore | None = None,
) -> RunHeatingCycleUseCase:
    return RunHeatingCycleUseCase(
        zones=ZoneRepository() if zones is None else zones,
        climate_settings=ClimateSettingsRepository(),
        learning=ZoneLearningRepository() if learning is None else learning,
        store=store,
        temperatures=temperatures,
        provider=ThermostatProviderStub(thermostats=thermostats),
        book=book,
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


def _living_room() -> Zone:
    zone = ZoneFactory.create(slug='living-room', kind=ZoneKind.FLOOR, thermostat__slug=LIVING_ROOM, live_control=True)
    ComfortPeriodFactory.create_batch(7, zone=zone, start=time(6, 30), end=time(23, 30))
    return zone


def _issued(zone: str, setpoint: str, at: datetime) -> ZoneCommandIssued:
    return ZoneCommandIssued(
        zone=zone,
        change=ThermostatChange(setpoint=Decimal(setpoint)),
        dry_run=False,
        occurred_at=at,
    )


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
def boosted_thermostats(
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    f_thermostat: FloorThermostatStub,
) -> dict[str, FloorThermostat]:
    Zone.objects.filter(pk=_air_zone().pk).update(boost_until=BOOST_END)
    thermostats: dict[str, FloorThermostat] = {KITCHEN: f_thermostat}
    store.record_state(KITCHEN, _state('30.0'))
    temperatures.record_state('air-kitchen', _reading('21.5'))
    _use_case(store, temperatures, thermostats, book, EventDispatcher()).execute(_at(MORNING))
    temperatures.record_state('air-kitchen', _reading('21.8'))
    return thermostats


@pytest.fixture
def f_boosted_air_cycle(
    boosted_thermostats: dict[str, FloorThermostat],
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    return _use_case(store, temperatures, boosted_thermostats, book, dispatcher)


@pytest.fixture
def f_boost_ending_air_cycle(
    boosted_thermostats: dict[str, FloorThermostat],
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    _use_case(store, temperatures, boosted_thermostats, book, EventDispatcher()).execute(
        _at(BOOST_END - timedelta(minutes=10)),
    )
    return _use_case(store, temperatures, boosted_thermostats, book, dispatcher)


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


def test_air_dry_run_follows_would_be_stop_under_boost_without_entry(
    f_boosted_air_cycle: RunHeatingCycleUseCase,
    f_events: list[DomainEvent],
) -> None:
    f_boosted_air_cycle.execute(_at(BOOST_END - timedelta(minutes=10)))

    assert (f_events, Zone.objects.get(slug='kitchen').last_air_dry_run_at) == ([], MORNING)


def test_air_dry_run_journals_idle_setpoint_when_boost_ends_as_live_air_mode_writes_it(
    f_boost_ending_air_cycle: RunHeatingCycleUseCase,
    f_events: list[DomainEvent],
) -> None:
    f_boost_ending_air_cycle.execute(_at(BOOST_END))

    assert (f_events, Zone.objects.get(slug='kitchen').last_air_dry_run_at) == (
        [
            _issued('kitchen', '28.0', BOOST_END),
            AirControlDryRun(
                zone='kitchen',
                air=Decimal('21.8'),
                target=Decimal('22.0'),
                mode=ScheduleMode.COMFORT,
                state=AirState.IDLE,
                previous_state=AirState.IDLE,
                power=True,
                setpoint=Decimal('18.0'),
                early_stop_offset=Decimal('0.30'),
                lead_minutes=None,
                occurred_at=BOOST_END,
            ),
        ],
        BOOST_END,
    )


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
def f_book() -> ControlBook:
    return ControlBook()


@pytest.fixture
def house_zones() -> list[ZoneSettings]:
    _guard_zone()
    _air_zone()
    _living_room()
    return ZoneRepository.all_settings()


@pytest.fixture
def house_thermostats(
    house_zones: list[ZoneSettings],
    store: ThermostatStateStore,
    f_thermostat: FloorThermostatStub,
    f_guard_thermostat: FloorThermostatStub,
    f_living_room_thermostat: FloorThermostatStub,
) -> dict[str, FloorThermostat]:
    for zone in house_zones:
        store.record_state(zone.thermostat_slug, _state('22.0'))
    return {BATHROOM: f_guard_thermostat, KITCHEN: f_thermostat, LIVING_ROOM: f_living_room_thermostat}


@pytest.fixture
def locked_learning() -> ZoneLearningStoreStub:
    return ZoneLearningStoreStub(records={}, errors={'kitchen': OperationalError(LOCKED)}, read_errors=())


@pytest.fixture
def f_unsaved_morning_cycle(
    house_thermostats: dict[str, FloorThermostat],
    locked_learning: ZoneLearningStoreStub,
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    f_book: ControlBook,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    f_book.remember_zone(
        'kitchen',
        ZoneMemory(
            path=ControlPath.FLOOR,
            mode=ScheduleMode.PREHEAT,
            morning=Morning(
                comfort_at=COMFORT_AT,
                target=Decimal('22.0'),
                outdoor=Decimal('-3.4'),
                samples=tuple(AirSample(at=at, air=Decimal(air)) for at, air in MORNING_AIR[1:]),
            ),
            preheated_for=COMFORT_AT,
        ),
    )
    return _use_case(store, temperatures, house_thermostats, f_book, dispatcher, learning=locked_learning)


@pytest.fixture
def f_unsaved_stop_cycle(
    house_thermostats: dict[str, FloorThermostat],
    locked_learning: ZoneLearningStoreStub,
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    f_book: ControlBook,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    Zone.objects.filter(slug='kitchen').update(air_control_live=True)
    temperatures.record_state('air-kitchen', _reading('22.1'))
    f_book.remember_zone(
        'kitchen',
        ZoneMemory(
            path=ControlPath.AIR,
            mode=ScheduleMode.COMFORT,
            air_state=AirState.IDLE,
            heat_target=Decimal('22.0'),
            stop=StopWatch(stopped_at=MORNING, target=Decimal('22.0'), peak=Decimal('22.3')),
        ),
    )
    return _use_case(store, temperatures, house_thermostats, f_book, dispatcher, learning=locked_learning)


@pytest.fixture
def f_locked_air_stamp_zones(house_zones: list[ZoneSettings]) -> ZoneStoreStub:
    return ZoneStoreStub(
        zones=house_zones,
        errors={('last_air_dry_run_at', 'kitchen'): OperationalError(LOCKED)},
    )


@pytest.fixture
def f_unstamped_air_dry_run_cycle(
    house_thermostats: dict[str, FloorThermostat],
    f_locked_air_stamp_zones: ZoneStoreStub,
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    f_book: ControlBook,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    store.record_state(KITCHEN, _state('28.0'))
    temperatures.record_state('air-kitchen', _reading('20.4'))
    return _use_case(store, temperatures, house_thermostats, f_book, dispatcher, zones=f_locked_air_stamp_zones)


@pytest.fixture
def f_locked_command_stamp_zones(house_zones: list[ZoneSettings]) -> ZoneStoreStub:
    return ZoneStoreStub(
        zones=house_zones,
        errors={('last_command_at', 'kitchen'): OperationalError(LOCKED)},
    )


@pytest.fixture
def f_unstamped_command_cycle(
    house_thermostats: dict[str, FloorThermostat],
    f_locked_command_stamp_zones: ZoneStoreStub,
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    f_book: ControlBook,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    return _use_case(store, temperatures, house_thermostats, f_book, dispatcher, zones=f_locked_command_stamp_zones)


def test_morning_that_fails_to_save_is_lost_while_the_cycle_goes_on(
    f_unsaved_morning_cycle: RunHeatingCycleUseCase,
    f_living_room_thermostat: FloorThermostatStub,
    f_events: list[DomainEvent],
    f_book: ControlBook,
) -> None:
    with capture_logs() as logs:
        output = f_unsaved_morning_cycle.execute(_at(COMFORT_AT))

    assert (
        output.outcomes,
        f_living_room_thermostat.commands,
        f_events,
        f_book.zone('kitchen'),
        [(log['event'], log['log_level'], log['zone'], log['error']) for log in logs],
    ) == (
        {
            'bathroom-2f': ZoneCycleOutcome.APPLIED,
            'kitchen': ZoneCycleOutcome.APPLIED,
            'living-room': ZoneCycleOutcome.APPLIED,
        },
        [('setpoint', Decimal('28.0'))],
        [
            _issued('bathroom-2f', '30.0', COMFORT_AT),
            _issued('kitchen', '28.0', COMFORT_AT),
            _issued('living-room', '28.0', COMFORT_AT),
        ],
        ZoneMemory(path=ControlPath.FLOOR, mode=ScheduleMode.COMFORT, preheated_for=COMFORT_AT),
        [('zone_morning_save_failed', 'warning', 'kitchen', LOCKED)],
    )


def test_stop_that_fails_to_save_is_lost_while_the_cycle_goes_on(
    f_unsaved_stop_cycle: RunHeatingCycleUseCase,
    f_living_room_thermostat: FloorThermostatStub,
    f_events: list[DomainEvent],
    f_book: ControlBook,
) -> None:
    now = MORNING + timedelta(hours=2, minutes=1)

    with capture_logs() as logs:
        output = f_unsaved_stop_cycle.execute(_at(now))

    assert (
        output.outcomes,
        f_living_room_thermostat.commands,
        f_events,
        f_book.zone('kitchen'),
        [(log['event'], log['log_level'], log['zone'], log['error']) for log in logs],
    ) == (
        {
            'bathroom-2f': ZoneCycleOutcome.APPLIED,
            'kitchen': ZoneCycleOutcome.APPLIED,
            'living-room': ZoneCycleOutcome.APPLIED,
        },
        [('setpoint', Decimal('28.0'))],
        [_issued('bathroom-2f', '30.0', now), _issued('kitchen', '18.0', now), _issued('living-room', '28.0', now)],
        ZoneMemory(
            path=ControlPath.AIR,
            mode=ScheduleMode.COMFORT,
            air_state=AirState.IDLE,
            heat_target=Decimal('22.0'),
        ),
        [('zone_early_stop_offset_save_failed', 'warning', 'kitchen', LOCKED)],
    )


def test_air_dry_run_stamp_that_fails_to_save_is_skipped_while_the_cycle_goes_on(
    f_unstamped_air_dry_run_cycle: RunHeatingCycleUseCase,
    f_locked_air_stamp_zones: ZoneStoreStub,
    f_living_room_thermostat: FloorThermostatStub,
    f_events: list[DomainEvent],
    f_book: ControlBook,
) -> None:
    with capture_logs() as logs:
        output = f_unstamped_air_dry_run_cycle.execute(_at(MORNING))

    assert (
        output.outcomes,
        f_living_room_thermostat.commands,
        f_locked_air_stamp_zones.stamps,
        f_events,
        f_book.zone('kitchen'),
        [(log['event'], log['log_level'], log['zone'], log['error']) for log in logs],
    ) == (
        {
            'bathroom-2f': ZoneCycleOutcome.APPLIED,
            'kitchen': ZoneCycleOutcome.UNCHANGED,
            'living-room': ZoneCycleOutcome.APPLIED,
        },
        [('setpoint', Decimal('28.0'))],
        [('last_command_at', 'bathroom-2f', MORNING), ('last_command_at', 'living-room', MORNING)],
        [
            _issued('bathroom-2f', '30.0', MORNING),
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
            _issued('living-room', '28.0', MORNING),
        ],
        ZoneMemory(
            path=ControlPath.AIR_DRY_RUN,
            mode=ScheduleMode.COMFORT,
            air_state=AirState.HEAT,
            would_be_target=ThermostatTarget(power=True, setpoint=Decimal('30.0')),
        ),
        [('zone_air_dry_run_stamp_failed', 'warning', 'kitchen', LOCKED)],
    )


def test_zone_whose_write_stamp_fails_reports_failed_while_the_cycle_goes_on(
    f_unstamped_command_cycle: RunHeatingCycleUseCase,
    f_locked_command_stamp_zones: ZoneStoreStub,
    f_thermostat: FloorThermostatStub,
    f_living_room_thermostat: FloorThermostatStub,
    f_events: list[DomainEvent],
    f_book: ControlBook,
) -> None:
    with capture_logs() as logs:
        output = f_unstamped_command_cycle.execute(_at(MORNING))

    assert (
        output.outcomes,
        f_thermostat.commands,
        f_living_room_thermostat.commands,
        f_locked_command_stamp_zones.stamps,
        f_events,
        f_book.zone('kitchen'),
        f_book.zone('living-room'),
        [(log['event'], log['log_level'], log['zone'], log['error']) for log in logs],
    ) == (
        {
            'bathroom-2f': ZoneCycleOutcome.APPLIED,
            'kitchen': ZoneCycleOutcome.FAILED,
            'living-room': ZoneCycleOutcome.APPLIED,
        },
        [],
        [('setpoint', Decimal('28.0'))],
        [('last_command_at', 'bathroom-2f', MORNING), ('last_command_at', 'living-room', MORNING)],
        [_issued('bathroom-2f', '30.0', MORNING), _issued('living-room', '28.0', MORNING)],
        ZoneMemory(),
        ZoneMemory(path=ControlPath.FLOOR, mode=ScheduleMode.COMFORT),
        [('zone_cycle_failed', 'warning', 'kitchen', LOCKED)],
    )


@pytest.fixture
def f_guard_after_unread_learning_cycle(
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    book: ControlBook,
    f_guard_thermostat: FloorThermostatStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    _house_settings()
    store.record_state(BATHROOM, _state('22.0'))
    temperatures.record_state('air-second-floor', _reading('18.8'))
    thermostats: dict[str, FloorThermostat] = {BATHROOM: f_guard_thermostat}
    learning = ZoneLearningStoreStub(records={}, errors={}, read_errors=(OperationalError(LOCKED),))
    with pytest.raises(OperationalError):
        _use_case(store, temperatures, thermostats, book, EventDispatcher(), learning=learning).execute(_at(NIGHT))
    return _use_case(store, temperatures, thermostats, book, dispatcher, learning=learning)


def test_guard_change_of_a_cycle_that_fails_to_read_learning_is_journaled_by_the_next_one(
    f_guard_after_unread_learning_cycle: RunHeatingCycleUseCase,
    f_events: list[DomainEvent],
) -> None:
    now = NIGHT + MINUTE

    f_guard_after_unread_learning_cycle.execute(_at(now))

    assert f_events == [
        SecondFloorGuardDryRun(
            zone='bathroom-2f',
            active=True,
            air=Decimal('18.8'),
            setpoint=Decimal('30.0'),
            occurred_at=now,
        ),
    ]


@pytest.fixture
def capped_lead_thermostats(
    temperatures: TemperatureStateStore,
    f_thermostat: FloorThermostatStub,
) -> dict[str, FloorThermostat]:
    _learned(_air_zone(air_control_live=True), rate='0.500')
    ClimateSettingsFactory.create(outdoor_sensor=_sensor('outdoor'))
    _read(temperatures, {'air-kitchen': '19.4', 'outdoor': '-3.4'})
    return {KITCHEN: f_thermostat}


@pytest.fixture
def f_air_sensor_flap_cycle(
    capped_lead_thermostats: dict[str, FloorThermostat],
    clock: MonotonicClockStub,
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    f_book: ControlBook,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    store.record_state(KITCHEN, _state('18.0'))
    for at in (CAPPED_START - MINUTE, CAPPED_START):
        _use_case(store, temperatures, capped_lead_thermostats, f_book, EventDispatcher()).execute(_at(at))
    clock.advance(STALE.total_seconds())
    store.record_state(KITCHEN, _state('30.0'))
    temperatures.record_state('outdoor', _reading('-3.4'))
    _use_case(store, temperatures, capped_lead_thermostats, f_book, EventDispatcher()).execute(
        _at(CAPPED_START + timedelta(minutes=15)),
    )
    temperatures.record_state('air-kitchen', _reading('19.5'))
    return _use_case(store, temperatures, capped_lead_thermostats, f_book, dispatcher)


@pytest.fixture
def f_air_sensor_flap_after_held_back_preheat_cycle(
    capped_lead_thermostats: dict[str, FloorThermostat],
    clock: MonotonicClockStub,
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    f_book: ControlBook,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    Zone.objects.filter(slug='kitchen').update(last_command_at=CAPPED_START - timedelta(minutes=20))
    store.record_state(KITCHEN, _state('18.0'))
    for at in (CAPPED_START - MINUTE, CAPPED_START, CAPPED_START + timedelta(minutes=10)):
        _use_case(store, temperatures, capped_lead_thermostats, f_book, EventDispatcher()).execute(_at(at))
    clock.advance(STALE.total_seconds())
    store.record_state(KITCHEN, _state('30.0'))
    temperatures.record_state('outdoor', _reading('-3.4'))
    _use_case(store, temperatures, capped_lead_thermostats, f_book, EventDispatcher()).execute(
        _at(CAPPED_START + timedelta(minutes=15)),
    )
    temperatures.record_state('air-kitchen', _reading('19.5'))
    return _use_case(store, temperatures, capped_lead_thermostats, f_book, dispatcher)


@pytest.fixture
def f_air_sensor_lost_at_preheat_start_cycle(
    capped_lead_thermostats: dict[str, FloorThermostat],
    clock: MonotonicClockStub,
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    f_book: ControlBook,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    store.record_state(KITCHEN, _state('22.0'))
    _use_case(store, temperatures, capped_lead_thermostats, f_book, EventDispatcher()).execute(
        _at(CAPPED_START - timedelta(minutes=15)),
    )
    clock.advance(STALE.total_seconds())
    store.record_state(KITCHEN, _state('18.0'))
    temperatures.record_state('outdoor', _reading('-3.4'))
    _use_case(store, temperatures, capped_lead_thermostats, f_book, EventDispatcher()).execute(_at(CAPPED_START))
    temperatures.record_state('air-kitchen', _reading('19.4'))
    return _use_case(store, temperatures, capped_lead_thermostats, f_book, dispatcher)


@pytest.fixture
def f_restart_in_air_mode_preheat_cycle(
    request: pytest.FixtureRequest,
    capped_lead_thermostats: dict[str, FloorThermostat],
    clock: MonotonicClockStub,
    store: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    f_book: ControlBook,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    polled_before_first_cycle = request.param
    Zone.objects.filter(slug='kitchen').update(last_command_at=CAPPED_START)
    clock.advance(STALE.total_seconds())
    temperatures.record_state('outdoor', _reading('-3.4'))
    if polled_before_first_cycle:
        store.record_state(KITCHEN, _state('30.0'))
    _use_case(store, temperatures, capped_lead_thermostats, f_book, EventDispatcher()).execute(
        _at(CAPPED_START + timedelta(minutes=10)),
    )
    store.record_state(KITCHEN, _state('30.0'))
    temperatures.record_state('air-kitchen', _reading('19.4'))
    return _use_case(store, temperatures, capped_lead_thermostats, f_book, dispatcher)


def test_air_sensor_back_in_preheat_starts_no_second_morning_for_its_comfort_start(
    f_air_sensor_flap_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
    f_book: ControlBook,
) -> None:
    output = f_air_sensor_flap_cycle.execute(_at(CAPPED_START + timedelta(minutes=16)))

    assert (output.outcomes, f_thermostat.commands, f_book.zone('kitchen').morning) == (
        {'kitchen': ZoneCycleOutcome.UNCHANGED},
        [('setpoint', Decimal('30.0'))],
        None,
    )


def test_air_sensor_back_in_preheat_starts_no_morning_once_its_held_back_write_went_out(
    f_air_sensor_flap_after_held_back_preheat_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
    f_book: ControlBook,
) -> None:
    output = f_air_sensor_flap_after_held_back_preheat_cycle.execute(_at(CAPPED_START + timedelta(minutes=16)))

    assert (output.outcomes, f_thermostat.commands, f_book.zone('kitchen').morning) == (
        {'kitchen': ZoneCycleOutcome.UNCHANGED},
        [('setpoint', Decimal('30.0'))],
        None,
    )


@pytest.mark.parametrize(
    'f_restart_in_air_mode_preheat_cycle',
    [True, False],
    ids=['night_write_waits_its_turn', 'thermostat_not_polled_yet'],
    indirect=True,
)
def test_restart_in_air_mode_preheat_starts_no_morning_when_the_air_sensor_first_reports(
    f_restart_in_air_mode_preheat_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
    f_book: ControlBook,
) -> None:
    output = f_restart_in_air_mode_preheat_cycle.execute(_at(CAPPED_START + timedelta(minutes=11)))

    assert (output.outcomes, f_thermostat.commands, f_book.zone('kitchen').morning) == (
        {'kitchen': ZoneCycleOutcome.UNCHANGED},
        [],
        None,
    )


def test_air_sensor_back_in_preheat_starts_morning_missed_at_its_start(
    f_air_sensor_lost_at_preheat_start_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
    f_book: ControlBook,
) -> None:
    now = CAPPED_START + timedelta(minutes=16)

    output = f_air_sensor_lost_at_preheat_start_cycle.execute(_at(now))

    assert (output.outcomes, f_thermostat.commands, f_book.zone('kitchen').morning) == (
        {'kitchen': ZoneCycleOutcome.APPLIED},
        [('setpoint', Decimal('18.0')), ('setpoint', Decimal('30.0'))],
        Morning(
            comfort_at=COMFORT_AT,
            target=Decimal('22.0'),
            outdoor=Decimal('-3.4'),
            samples=(AirSample(at=now, air=Decimal('19.4')),),
        ),
    )
