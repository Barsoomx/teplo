import time
from datetime import timedelta

from django.utils import timezone

from apps.climate.usecases.run_heating_cycle import RunHeatingCycleInputDTO, RunHeatingCycleUseCase
from apps.core.runtime.periodic import PeriodicJob
from apps.devices.usecases.poll_plugs import PollPlugsInputDTO, PollPlugsUseCase
from apps.devices.usecases.poll_thermostats import PollThermostatsInputDTO, PollThermostatsUseCase
from apps.notifications.usecases.deliver_alerts import DeliverAlertsInputDTO, DeliverAlertsUseCase
from apps.notifications.usecases.evaluate_alerts import EvaluateAlertsInputDTO, EvaluateAlertsUseCase
from apps.notifications.usecases.note_startup import NoteStartupInputDTO, NoteStartupUseCase
from apps.telemetry.usecases.capture_samples import CaptureSamplesInputDTO, CaptureSamplesUseCase
from apps.telemetry.usecases.flush_samples import FlushSamplesInputDTO, FlushSamplesUseCase
from apps.telemetry.usecases.journal_startup import JournalStartupInputDTO, JournalStartupUseCase


def teplo_jobs() -> tuple[PeriodicJob, ...]:
    return (
        PeriodicJob(name='poll_thermostats', interval=timedelta(seconds=30), action=poll_thermostats),
        PeriodicJob(name='poll_plugs', interval=timedelta(seconds=30), action=poll_plugs),
        PeriodicJob(name='capture_samples', interval=timedelta(minutes=1), action=capture_samples),
        PeriodicJob(name='run_heating_cycle', interval=timedelta(minutes=1), action=run_heating_cycle),
        PeriodicJob(name='flush_samples', interval=timedelta(minutes=5), action=flush_samples),
        PeriodicJob(name='evaluate_alerts', interval=timedelta(minutes=1), action=evaluate_alerts),
        PeriodicJob(name='deliver_alerts', interval=timedelta(minutes=1), action=deliver_alerts),
    )


def journal_startup() -> object:
    started = JournalStartupUseCase.init().execute(JournalStartupInputDTO(now=timezone.now()))
    return NoteStartupUseCase.init().execute(
        NoteStartupInputDTO(last_activity=started.last_activity, monotonic=time.monotonic()),
    )


def poll_thermostats() -> object:
    return PollThermostatsUseCase.init().execute(PollThermostatsInputDTO(now=timezone.now()))


def poll_plugs() -> object:
    return PollPlugsUseCase.init().execute(PollPlugsInputDTO(now=timezone.now()))


def capture_samples() -> object:
    return CaptureSamplesUseCase.init().execute(CaptureSamplesInputDTO(now=timezone.now()))


def run_heating_cycle() -> object:
    return RunHeatingCycleUseCase.init().execute(RunHeatingCycleInputDTO(now=timezone.now()))


def flush_samples() -> object:
    return FlushSamplesUseCase.init().execute(FlushSamplesInputDTO())


def evaluate_alerts() -> object:
    return EvaluateAlertsUseCase.init().execute(EvaluateAlertsInputDTO(now=timezone.now(), monotonic=time.monotonic()))


def deliver_alerts() -> object:
    return DeliverAlertsUseCase.init().execute(DeliverAlertsInputDTO())
