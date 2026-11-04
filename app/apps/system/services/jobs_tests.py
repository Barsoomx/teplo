from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from apps.climate.usecases.watch_thermostat_changes import (
    WatchThermostatChangesInputDTO,
    WatchThermostatChangesOutputDTO,
)
from apps.devices.domain.readings import ReadingPair, ThermostatMode, ThermostatState
from apps.devices.usecases.poll_thermostats import PollThermostatsInputDTO, PollThermostatsOutputDTO
from apps.notifications.stubs import ExecutableStub
from apps.system.services.jobs import shutdown_jobs, startup_jobs, teplo_jobs, watch_thermostats

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


@pytest.fixture
def f_poll() -> ExecutableStub[PollThermostatsInputDTO, PollThermostatsOutputDTO]:
    return ExecutableStub(outcomes=(POLLED,))


@pytest.fixture
def f_watch() -> ExecutableStub[WatchThermostatChangesInputDTO, WatchThermostatChangesOutputDTO]:
    return ExecutableStub(outcomes=(WATCHED,))


def test_teplo_jobs_schedule() -> None:
    assert [(job.name, job.interval, job.delay) for job in teplo_jobs()] == [
        ('poll_thermostats', timedelta(seconds=30), timedelta(0)),
        ('poll_plugs', timedelta(seconds=30), timedelta(0)),
        ('guard_phases', timedelta(seconds=1), timedelta(0)),
        ('check_sensors', timedelta(seconds=30), timedelta(minutes=1)),
        ('capture_samples', timedelta(minutes=1), timedelta(0)),
        ('run_heating_cycle', timedelta(minutes=1), timedelta(0)),
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
