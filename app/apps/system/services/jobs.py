import time
from datetime import datetime, timedelta

from django.utils import timezone

from apps.climate.usecases.run_heating_cycle import RunHeatingCycleInputDTO, RunHeatingCycleUseCase
from apps.climate.usecases.run_source_cycle import RunSourceCycleInputDTO, RunSourceCycleUseCase
from apps.climate.usecases.watch_source_changes import (
    WatchSourceChangesInputDTO,
    WatchSourceChangesOutputDTO,
    WatchSourceChangesUseCase,
)
from apps.climate.usecases.watch_thermostat_changes import (
    WatchThermostatChangesInputDTO,
    WatchThermostatChangesOutputDTO,
    WatchThermostatChangesUseCase,
)
from apps.core.domain.ports import Executable
from apps.core.runtime.periodic import PeriodicJob
from apps.devices.services.mqtt_listener import mqtt_listener
from apps.devices.usecases.check_sensors import CheckSensorsInputDTO, CheckSensorsUseCase
from apps.devices.usecases.poll_climate_units import (
    PollClimateUnitsInputDTO,
    PollClimateUnitsOutputDTO,
    PollClimateUnitsUseCase,
)
from apps.devices.usecases.poll_plugs import PollPlugsInputDTO, PollPlugsUseCase
from apps.devices.usecases.poll_thermostats import (
    PollThermostatsInputDTO,
    PollThermostatsOutputDTO,
    PollThermostatsUseCase,
)
from apps.notifications.services.command_listener import command_listener
from apps.notifications.usecases.deliver_alerts import DeliverAlertsInputDTO, DeliverAlertsUseCase
from apps.notifications.usecases.evaluate_alerts import EvaluateAlertsInputDTO, EvaluateAlertsUseCase
from apps.notifications.usecases.note_startup import NoteStartupInputDTO, NoteStartupUseCase
from apps.notifications.usecases.report_outages import ReportOutagesInputDTO, ReportOutagesUseCase
from apps.power.usecases.recover_write_holds import RecoverWriteHoldsInputDTO, RecoverWriteHoldsUseCase
from apps.power.usecases.shed_phase_loads import ShedPhaseLoadsInputDTO, ShedPhaseLoadsUseCase
from apps.power.usecases.watch_phases import WatchPhasesInputDTO, WatchPhasesUseCase
from apps.system.services.boot_id import BootIdReader
from apps.system.services.clock_trust import clock_trust_source, read_clock
from apps.system.services.uptime import UptimeReader
from apps.telemetry.usecases.capture_samples import CaptureSamplesInputDTO, CaptureSamplesUseCase
from apps.telemetry.usecases.flush_samples import FlushSamplesInputDTO, FlushSamplesUseCase
from apps.telemetry.usecases.journal_startup import JournalStartupInputDTO, JournalStartupUseCase

ONCE = timedelta(0)


def startup_jobs() -> tuple[PeriodicJob, ...]:
    return (
        PeriodicJob(name='journal_startup', interval=ONCE, action=journal_startup),
        PeriodicJob(name='recover_write_holds', interval=ONCE, action=recover_write_holds),
        PeriodicJob(name='start_mqtt_listener', interval=ONCE, action=mqtt_listener.start),
        PeriodicJob(name='start_command_listener', interval=ONCE, action=command_listener.start),
    )


def shutdown_jobs() -> tuple[PeriodicJob, ...]:
    return (
        PeriodicJob(name='flush_samples', interval=ONCE, action=flush_samples),
        PeriodicJob(name='stop_mqtt_listener', interval=ONCE, action=mqtt_listener.stop),
        PeriodicJob(name='stop_command_listener', interval=ONCE, action=command_listener.stop),
    )


def teplo_jobs() -> tuple[PeriodicJob, ...]:
    return (
        PeriodicJob(name='poll_thermostats', interval=timedelta(seconds=30), action=poll_thermostats),
        PeriodicJob(name='poll_plugs', interval=timedelta(seconds=30), action=poll_plugs),
        PeriodicJob(name='poll_climate_units', interval=timedelta(seconds=30), action=poll_climate_units),
        PeriodicJob(name='guard_phases', interval=timedelta(seconds=1), action=guard_phases),
        PeriodicJob(
            name='check_sensors',
            interval=timedelta(seconds=30),
            action=check_sensors,
            delay=timedelta(minutes=1),
        ),
        PeriodicJob(name='capture_samples', interval=timedelta(minutes=1), action=capture_samples),
        PeriodicJob(name='run_heating_cycle', interval=timedelta(minutes=1), action=run_heating_cycle),
        PeriodicJob(
            name='run_source_cycle',
            interval=timedelta(minutes=1),
            action=run_source_cycle,
            delay=timedelta(seconds=30),
        ),
        PeriodicJob(name='flush_samples', interval=timedelta(minutes=5), action=flush_samples),
        PeriodicJob(name='evaluate_alerts', interval=timedelta(minutes=1), action=evaluate_alerts),
        PeriodicJob(name='report_outages', interval=timedelta(minutes=1), action=report_outages),
        PeriodicJob(name='deliver_alerts', interval=timedelta(minutes=1), action=deliver_alerts),
    )


def journal_startup() -> object:
    router_uptime = UptimeReader.init().read()
    monotonic = time.monotonic()
    started = JournalStartupUseCase.init().execute(
        JournalStartupInputDTO(now=timezone.now(), boot_id=BootIdReader.init().read(), router_uptime=router_uptime),
    )
    return NoteStartupUseCase.init().execute(
        NoteStartupInputDTO(
            last_activity=started.last_activity,
            monotonic=monotonic,
            router_uptime=router_uptime,
            boot=started.boot,
        ),
    )


def recover_write_holds() -> object:
    return RecoverWriteHoldsUseCase.init().execute(RecoverWriteHoldsInputDTO(now=timezone.now()))


def poll_thermostats() -> object:
    return watch_thermostats(PollThermostatsUseCase.init(), WatchThermostatChangesUseCase.init(), timezone.now())


def watch_thermostats(
    poll: Executable[PollThermostatsInputDTO, PollThermostatsOutputDTO],
    watch: Executable[WatchThermostatChangesInputDTO, WatchThermostatChangesOutputDTO],
    now: datetime,
) -> WatchThermostatChangesOutputDTO:
    polled = poll.execute(PollThermostatsInputDTO(now=now))
    return watch.execute(WatchThermostatChangesInputDTO(pairs=polled.pairs, now=now))


def poll_plugs() -> object:
    return PollPlugsUseCase.init().execute(PollPlugsInputDTO(now=timezone.now()))


def poll_climate_units() -> object:
    return watch_climate_units(PollClimateUnitsUseCase.init(), WatchSourceChangesUseCase.init(), timezone.now())


def watch_climate_units(
    poll: Executable[PollClimateUnitsInputDTO, PollClimateUnitsOutputDTO],
    watch: Executable[WatchSourceChangesInputDTO, WatchSourceChangesOutputDTO],
    now: datetime,
) -> WatchSourceChangesOutputDTO:
    polled = poll.execute(PollClimateUnitsInputDTO(now=now))
    return watch.execute(WatchSourceChangesInputDTO(pairs=polled.pairs, now=now))


def guard_phases() -> object:
    now = timezone.now()
    WatchPhasesUseCase.init().execute(WatchPhasesInputDTO(now=now))
    return ShedPhaseLoadsUseCase.init().execute(ShedPhaseLoadsInputDTO(now=now))


def check_sensors() -> object:
    return CheckSensorsUseCase.init().execute(CheckSensorsInputDTO(now=timezone.now()))


def capture_samples() -> object:
    return CaptureSamplesUseCase.init().execute(CaptureSamplesInputDTO(now=timezone.now()))


def run_heating_cycle() -> object:
    reading = read_clock(clock_trust_source())
    return RunHeatingCycleUseCase.init().execute(
        RunHeatingCycleInputDTO(now=reading.now, monotonic=reading.monotonic, clock_trusted=reading.trusted),
    )


def run_source_cycle() -> object:
    reading = read_clock(clock_trust_source())
    return RunSourceCycleUseCase.init().execute(
        RunSourceCycleInputDTO(now=reading.now, monotonic=reading.monotonic, clock_trusted=reading.trusted),
    )


def flush_samples() -> object:
    return FlushSamplesUseCase.init().execute(FlushSamplesInputDTO())


def evaluate_alerts() -> object:
    reading = read_clock(clock_trust_source())
    return EvaluateAlertsUseCase.init().execute(
        EvaluateAlertsInputDTO(now=reading.now, monotonic=reading.monotonic, clock_trusted=reading.trusted),
    )


def report_outages() -> object:
    reading = read_clock(clock_trust_source())
    return ReportOutagesUseCase.init().execute(
        ReportOutagesInputDTO(now=reading.now, monotonic=reading.monotonic, clock_trusted=reading.trusted),
    )


def deliver_alerts() -> object:
    return DeliverAlertsUseCase.init().execute(DeliverAlertsInputDTO(now=timezone.now()))
