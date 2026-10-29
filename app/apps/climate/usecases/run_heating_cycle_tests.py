from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from apps.climate.domain.change import ThermostatChange
from apps.climate.domain.events import ZoneCommandFailed, ZoneCommandIssued
from apps.climate.factories import ComfortPeriodFactory, ZoneFactory
from apps.climate.models import Zone
from apps.climate.repositories.zone import ZoneRepository
from apps.climate.usecases.run_heating_cycle import (
    RunHeatingCycleInputDTO,
    RunHeatingCycleUseCase,
    ZoneCycleOutcome,
)
from apps.core.domain.events import DomainEvent, EventDispatcher
from apps.devices.domain.capabilities import FloorThermostat
from apps.devices.domain.errors import DeviceUnavailableError
from apps.devices.domain.readings import ThermostatMode, ThermostatState
from apps.devices.services.state_store import ThermostatStateStore
from apps.devices.stubs import FloorThermostatStub, ThermostatProviderStub
from apps.system.stubs import ClockStub

pytestmark = pytest.mark.django_db

MOSCOW = ZoneInfo('Europe/Moscow')
MORNING = datetime(2026, 10, 29, 7, 0, tzinfo=MOSCOW)
PREHEAT = datetime(2026, 10, 29, 4, 0, tzinfo=MOSCOW)
NIGHT = datetime(2026, 10, 29, 1, 0, tzinfo=MOSCOW)


def _state(setpoint: str, mode: ThermostatMode = ThermostatMode.HOLD, read_at: datetime = MORNING) -> ThermostatState:
    return ThermostatState(
        power=True,
        mode=mode,
        setpoint=Decimal(setpoint),
        floor_temperature=Decimal('21.0'),
        read_at=read_at,
    )


def _cycle(
    zone: Zone,
    state: ThermostatState,
    thermostat: FloorThermostat,
    dispatcher: EventDispatcher,
    *,
    dry_run: bool = False,
    clock_trusted: bool = True,
) -> RunHeatingCycleUseCase:
    store = ThermostatStateStore(unavailable_after=timedelta(minutes=10))
    store.record_state(zone.thermostat.slug, state)
    return RunHeatingCycleUseCase(
        zones=ZoneRepository(),
        store=store,
        provider=ThermostatProviderStub(thermostats={zone.thermostat.slug: thermostat}),
        clock=ClockStub(trusted=clock_trusted),
        dry_run=dry_run,
        dispatcher=dispatcher,
    )


@pytest.fixture
def f_zone() -> Zone:
    zone = ZoneFactory.create(slug='kitchen', thermostat__slug='thermostat-kitchen', live_control=True)
    ComfortPeriodFactory.create_batch(7, zone=zone, start=time(6, 30), end=time(23, 30))
    return zone


@pytest.fixture
def f_thermostat() -> FloorThermostatStub:
    return FloorThermostatStub(state=None)


@pytest.fixture
def f_events() -> list[DomainEvent]:
    return []


@pytest.fixture
def dispatcher(f_events: list[DomainEvent]) -> EventDispatcher:
    event_dispatcher = EventDispatcher()
    event_dispatcher.register(ZoneCommandIssued, f_events.append)
    event_dispatcher.register(ZoneCommandFailed, f_events.append)
    return event_dispatcher


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
def f_untrusted_cycle(
    f_zone: Zone,
    f_thermostat: FloorThermostatStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    return _cycle(f_zone, _state('22.0'), f_thermostat, dispatcher, clock_trusted=False)


@pytest.fixture
def f_clock_behind_cycle(
    f_zone: Zone,
    f_thermostat: FloorThermostatStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    Zone.objects.filter(pk=f_zone.pk).update(last_command_at=NIGHT + timedelta(hours=2))
    return _cycle(f_zone, _state('22.0', read_at=NIGHT), f_thermostat, dispatcher, clock_trusted=False)


@pytest.fixture
def f_boost_ahead_of_clock_cycle(
    f_zone: Zone,
    f_thermostat: FloorThermostatStub,
    dispatcher: EventDispatcher,
) -> RunHeatingCycleUseCase:
    Zone.objects.filter(pk=f_zone.pk).update(boost_until=NIGHT + timedelta(hours=5))
    return _cycle(f_zone, _state('22.0', read_at=NIGHT), f_thermostat, dispatcher, clock_trusted=False)


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
    return _cycle(f_zone, _state('22.0', read_at=MORNING - timedelta(minutes=11)), f_thermostat, dispatcher)


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
    output = f_cycle.execute(RunHeatingCycleInputDTO(now=MORNING))

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
    f_cycle.execute(RunHeatingCycleInputDTO(now=PREHEAT))

    assert f_thermostat.commands == [('setpoint', Decimal('28.0'))]


def test_cycle_leaves_matching_thermostat_alone(
    f_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
) -> None:
    output = f_cycle.execute(RunHeatingCycleInputDTO(now=NIGHT))

    assert (output.outcomes, f_thermostat.commands) == ({'kitchen': ZoneCycleOutcome.UNCHANGED}, [])


def test_cycle_holds_comfort_when_clock_untrusted(
    f_untrusted_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
) -> None:
    f_untrusted_cycle.execute(RunHeatingCycleInputDTO(now=NIGHT))

    assert f_thermostat.commands == [('setpoint', Decimal('28.0'))]


def test_cycle_holds_comfort_when_clock_fell_behind_last_command(
    f_clock_behind_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
) -> None:
    output = f_clock_behind_cycle.execute(RunHeatingCycleInputDTO(now=NIGHT))

    assert (output.outcomes, f_thermostat.commands) == (
        {'kitchen': ZoneCycleOutcome.APPLIED},
        [('setpoint', Decimal('28.0'))],
    )


def test_cycle_ignores_boost_ending_beyond_boost_duration_after_clock_moved_back(
    f_boost_ahead_of_clock_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
) -> None:
    output = f_boost_ahead_of_clock_cycle.execute(RunHeatingCycleInputDTO(now=NIGHT))

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
    output = f_dry_run_cycle.execute(RunHeatingCycleInputDTO(now=MORNING))

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
    output = f_observed_cycle.execute(RunHeatingCycleInputDTO(now=MORNING))

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
    f_program_mode_cycle.execute(RunHeatingCycleInputDTO(now=NIGHT))

    assert f_thermostat.commands == [('mode', ThermostatMode.HOLD), ('setpoint', Decimal('22.0'))]


def test_cycle_throttles_zone_commanded_recently(
    f_recent_command_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
) -> None:
    output = f_recent_command_cycle.execute(RunHeatingCycleInputDTO(now=MORNING))

    assert (output.outcomes, f_thermostat.commands) == ({'kitchen': ZoneCycleOutcome.THROTTLED}, [])


def test_cycle_writes_at_once_when_zone_goes_live_after_dry_run(
    f_promoted_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
) -> None:
    output = f_promoted_cycle.execute(RunHeatingCycleInputDTO(now=MORNING))

    assert (output.outcomes, f_thermostat.commands) == (
        {'kitchen': ZoneCycleOutcome.APPLIED},
        [('setpoint', Decimal('28.0'))],
    )


def test_dry_run_throttles_its_journal_like_live_writes(
    f_recent_dry_run_cycle: RunHeatingCycleUseCase,
    f_events: list[DomainEvent],
) -> None:
    output = f_recent_dry_run_cycle.execute(RunHeatingCycleInputDTO(now=MORNING))

    assert (output.outcomes, f_events) == ({'kitchen': ZoneCycleOutcome.THROTTLED}, [])


def test_cycle_skips_unavailable_thermostat(
    f_stale_cycle: RunHeatingCycleUseCase,
    f_thermostat: FloorThermostatStub,
) -> None:
    output = f_stale_cycle.execute(RunHeatingCycleInputDTO(now=MORNING))

    assert (output.outcomes, f_thermostat.commands) == ({'kitchen': ZoneCycleOutcome.DEVICE_UNAVAILABLE}, [])


def test_cycle_reports_failed_write(f_failing_cycle: RunHeatingCycleUseCase, f_events: list[DomainEvent]) -> None:
    output = f_failing_cycle.execute(RunHeatingCycleInputDTO(now=MORNING))

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
