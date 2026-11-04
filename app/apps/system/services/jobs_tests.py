from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from apps.climate.usecases.watch_source_changes import WatchSourceChangesInputDTO, WatchSourceChangesOutputDTO
from apps.climate.usecases.watch_thermostat_changes import (
    WatchThermostatChangesInputDTO,
    WatchThermostatChangesOutputDTO,
)
from apps.devices.domain.readings import ClimateMode, ClimateUnitState, ReadingPair, ThermostatMode, ThermostatState
from apps.devices.usecases.poll_climate_units import PollClimateUnitsInputDTO, PollClimateUnitsOutputDTO
from apps.devices.usecases.poll_thermostats import PollThermostatsInputDTO, PollThermostatsOutputDTO
from apps.notifications.stubs import ExecutableStub
from apps.system.services.jobs import (
    shutdown_jobs,
    startup_jobs,
    teplo_jobs,
    watch_climate_units,
    watch_thermostats,
)

NOW = datetime(2026, 11, 4, 13, 40, tzinfo=UTC)
STATE = ThermostatState(
    power=True,
    mode=ThermostatMode.HOLD,
    setpoint=Decimal('28.0'),
    floor_temperature=Decimal('24.5'),
    heating=True,
    read_at=NOW,
)
PAIR = ReadingPair(
    slug='thermostat-kitchen',
    before=STATE,
    after=STATE,
    before_moment=1000.0,
    after_moment=1030.0,
)
POLLED = PollThermostatsOutputDTO(polled=('thermostat-kitchen',), failed=(), pairs=(PAIR,))
WATCHED = WatchThermostatChangesOutputDTO(held=(), journaled=())
CONVECTOR = ClimateUnitState(
    mode=ClimateMode.HEAT,
    target=Decimal(22),
    room_temperature=Decimal(21),
    read_at=NOW,
    level='High',
)
UNIT_PAIR = ReadingPair(
    slug='heater-office',
    before=CONVECTOR,
    after=CONVECTOR,
    before_moment=1000.0,
    after_moment=1030.0,
)
UNITS_POLLED = PollClimateUnitsOutputDTO(polled=('heater-office',), failed=(), pairs=(UNIT_PAIR,))
SOURCES_WATCHED = WatchSourceChangesOutputDTO(held=(), journaled=())


@pytest.fixture
def f_poll() -> ExecutableStub[PollThermostatsInputDTO, PollThermostatsOutputDTO]:
    return ExecutableStub(outcomes=(POLLED,))


@pytest.fixture
def f_watch() -> ExecutableStub[WatchThermostatChangesInputDTO, WatchThermostatChangesOutputDTO]:
    return ExecutableStub(outcomes=(WATCHED,))


@pytest.fixture
def f_unit_poll() -> ExecutableStub[PollClimateUnitsInputDTO, PollClimateUnitsOutputDTO]:
    return ExecutableStub(outcomes=(UNITS_POLLED,))


@pytest.fixture
def f_source_watch() -> ExecutableStub[WatchSourceChangesInputDTO, WatchSourceChangesOutputDTO]:
    return ExecutableStub(outcomes=(SOURCES_WATCHED,))


def test_teplo_jobs_schedule() -> None:
    assert [(job.name, job.interval, job.delay) for job in teplo_jobs()] == [
        ('poll_thermostats', timedelta(seconds=30), timedelta(0)),
        ('poll_plugs', timedelta(seconds=30), timedelta(0)),
        ('poll_climate_units', timedelta(seconds=30), timedelta(0)),
        ('guard_phases', timedelta(seconds=1), timedelta(0)),
        ('check_sensors', timedelta(seconds=30), timedelta(minutes=1)),
        ('capture_samples', timedelta(minutes=1), timedelta(0)),
        ('run_heating_cycle', timedelta(minutes=1), timedelta(0)),
        ('run_source_cycle', timedelta(minutes=1), timedelta(seconds=30)),
        ('flush_samples', timedelta(minutes=5), timedelta(0)),
        ('evaluate_alerts', timedelta(minutes=1), timedelta(0)),
        ('report_outages', timedelta(minutes=1), timedelta(0)),
        ('deliver_alerts', timedelta(minutes=1), timedelta(0)),
    ]


def test_startup_journals_the_start_then_recovers_the_write_holds_then_starts_the_listeners() -> None:
    assert [(job.name, job.interval) for job in startup_jobs()] == [
        ('journal_startup', timedelta(0)),
        ('recover_write_holds', timedelta(0)),
        ('start_mqtt_listener', timedelta(0)),
        ('start_command_listener', timedelta(0)),
    ]


def test_shutdown_flushes_the_samples_then_stops_the_listeners() -> None:
    assert [(job.name, job.interval) for job in shutdown_jobs()] == [
        ('flush_samples', timedelta(0)),
        ('stop_mqtt_listener', timedelta(0)),
        ('stop_command_listener', timedelta(0)),
    ]


def test_thermostat_poll_hands_its_reading_pairs_and_its_time_to_the_change_watch(
    f_poll: ExecutableStub[PollThermostatsInputDTO, PollThermostatsOutputDTO],
    f_watch: ExecutableStub[WatchThermostatChangesInputDTO, WatchThermostatChangesOutputDTO],
) -> None:
    output = watch_thermostats(f_poll, f_watch, NOW)

    assert (output, f_poll.inputs, f_watch.inputs) == (
        WATCHED,
        [PollThermostatsInputDTO(now=NOW)],
        [WatchThermostatChangesInputDTO(pairs=(PAIR,), now=NOW)],
    )


def test_climate_unit_poll_hands_its_reading_pairs_and_its_time_to_the_source_change_watch(
    f_unit_poll: ExecutableStub[PollClimateUnitsInputDTO, PollClimateUnitsOutputDTO],
    f_source_watch: ExecutableStub[WatchSourceChangesInputDTO, WatchSourceChangesOutputDTO],
) -> None:
    output = watch_climate_units(f_unit_poll, f_source_watch, NOW)

    assert (output, f_unit_poll.inputs, f_source_watch.inputs) == (
        SOURCES_WATCHED,
        [PollClimateUnitsInputDTO(now=NOW)],
        [WatchSourceChangesInputDTO(pairs=(UNIT_PAIR,), now=NOW)],
    )
