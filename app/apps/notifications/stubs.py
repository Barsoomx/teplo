import io
import urllib.request
from collections.abc import Callable
from datetime import datetime

from apps.climate.domain.room_status import RoomStatus
from apps.core.domain.errors import DomainError
from apps.notifications.domain.alerts import MonitoredDevice, PhaseAlarm, PhaseReading, ZoneHeating
from apps.notifications.domain.bot import BotUpdate
from apps.notifications.domain.commands import CommandRoom, OwnerCommand
from apps.power.domain.outage import OutageCause, OutageRecord, PowerCut


class DeviceSourceStub:
    def __init__(self, devices: list[MonitoredDevice]) -> None:
        self._devices = devices

    def devices(self) -> list[MonitoredDevice]:
        return self._devices


class ZoneSourceStub:
    def __init__(self, zones: list[ZoneHeating]) -> None:
        self._zones = zones
        self._clock_trusted: list[bool] = []

    @property
    def clock_trusted(self) -> list[bool]:
        return self._clock_trusted

    def zones(self, now: datetime, *, clock_trusted: bool) -> list[ZoneHeating]:  # noqa: ARG002
        self._clock_trusted.append(clock_trusted)
        return self._zones


class PhaseSourceStub:
    def __init__(self, alarms: list[PhaseAlarm]) -> None:
        self._alarms = alarms

    def phases(self) -> list[PhaseAlarm]:
        return self._alarms


class AlertSenderStub:
    def __init__(self, errors: dict[str, DomainError]) -> None:
        self._errors = errors
        self._attempts: list[str] = []
        self._sent: list[str] = []

    @property
    def attempts(self) -> list[str]:
        return self._attempts

    @property
    def sent(self) -> list[str]:
        return self._sent

    def send(self, text: str) -> None:
        self._attempts.append(text)
        if text in self._errors:
            raise self._errors[text]

        self._sent.append(text)


class UrlOpenerStub:
    def __init__(self, error: Exception | None, bodies: tuple[bytes, ...]) -> None:
        self._error = error
        self._bodies = bodies
        self._requests: list[tuple[urllib.request.Request, float]] = []

    @property
    def requests(self) -> list[tuple[urllib.request.Request, float]]:
        return self._requests

    def open(self, fullurl: urllib.request.Request, *, timeout: float) -> io.BytesIO:
        self._requests.append((fullurl, timeout))
        if self._error is not None:
            raise self._error

        return io.BytesIO(self._bodies[min(len(self._requests), len(self._bodies)) - 1])


class PowerCutLedgerStub:
    def __init__(self, errors: tuple[Exception, ...], boots_on_record: tuple[str, ...]) -> None:
        self._errors = errors
        self._boots_on_record = boots_on_record
        self._attempts = 0
        self._recorded: list[PowerCut] = []

    @property
    def recorded(self) -> list[PowerCut]:
        return self._recorded

    def record(self, power_cut: PowerCut) -> OutageRecord | None:
        self._attempts += 1
        if self._attempts <= len(self._errors):
            raise self._errors[self._attempts - 1]

        if power_cut.boot_id is not None and power_cut.boot_id in self._boot_ids():
            return None

        self._recorded.append(power_cut)
        return OutageRecord(
            id=len(self._recorded),
            began_at=power_cut.began_at,
            ended_at=power_cut.ended_at,
            cause=OutageCause.UNMARKED,
            cause_marked_at=None,
        )

    def _boot_ids(self) -> set[str | None]:
        return {*self._boots_on_record, *(recorded.boot_id for recorded in self._recorded)}


class ExecutableStub[TInput, TOutput]:
    def __init__(self, outcomes: tuple[TOutput | Exception, ...]) -> None:
        self._outcomes = outcomes
        self._inputs: list[TInput] = []

    @property
    def inputs(self) -> list[TInput]:
        return self._inputs

    def execute(self, input_dto: TInput) -> TOutput:
        self._inputs.append(input_dto)
        outcome = self._outcomes[min(len(self._inputs), len(self._outcomes)) - 1]
        if isinstance(outcome, Exception):
            raise outcome

        return outcome


class RoomStatusSourceStub:
    def __init__(self, readings: tuple[list[RoomStatus] | Exception, ...]) -> None:
        self._readings = readings
        self._reads: list[tuple[datetime, bool]] = []

    @property
    def reads(self) -> list[tuple[datetime, bool]]:
        return self._reads

    def rooms(self, now: datetime, *, clock_trusted: bool) -> list[RoomStatus]:
        self._reads.append((now, clock_trusted))
        reading = self._readings[min(len(self._reads), len(self._readings)) - 1]
        if isinstance(reading, Exception):
            raise reading

        return list(reading)


class PhaseReadoutStub:
    def __init__(self, phases: tuple[PhaseReading, ...] | None) -> None:
        self._phases = phases

    def phases(self) -> tuple[PhaseReading, ...] | None:
        return self._phases


class CommandBotStub:
    def __init__(self, polls: tuple[list[BotUpdate] | Exception, ...], send_error: DomainError | None) -> None:
        self._polls = polls
        self._send_error = send_error
        self._asked: list[tuple[int | None, int]] = []
        self._sent: list[str] = []

    @property
    def asked(self) -> list[tuple[int | None, int]]:
        return self._asked

    @property
    def sent(self) -> list[str]:
        return self._sent

    def updates(self, offset: int | None, wait_seconds: int) -> list[BotUpdate]:
        self._asked.append((offset, wait_seconds))
        poll = self._polls[min(len(self._asked), len(self._polls)) - 1]
        if isinstance(poll, Exception):
            raise poll

        return list(poll)

    def send(self, text: str) -> None:
        if self._send_error is not None:
            raise self._send_error

        self._sent.append(text)


class CommandLedgerStub:
    def __init__(self, timeline: list[str], last: int | Exception | None, error: Exception | None) -> None:
        self._timeline = timeline
        self._last = last
        self._error = error
        self._recorded: list[tuple[BotUpdate, OwnerCommand, datetime]] = []

    @property
    def recorded(self) -> list[tuple[BotUpdate, OwnerCommand, datetime]]:
        return self._recorded

    def last_update_id(self) -> int | None:
        self._timeline.append('read the last update')
        if isinstance(self._last, Exception):
            raise self._last

        return self._last

    def record(self, update: BotUpdate, command: OwnerCommand, at: datetime) -> None:
        if self._error is not None:
            raise self._error

        self._timeline.append(f'record {update.update_id}')
        self._recorded.append((update, command, at))


class CommandRoomsStub:
    def __init__(self, rooms: list[CommandRoom]) -> None:
        self._rooms = rooms

    def rooms(self) -> list[CommandRoom]:
        return list(self._rooms)


class OwnerCommandRunnerStub:
    def __init__(self, timeline: list[str]) -> None:
        self._timeline = timeline
        self._runs: list[tuple[OwnerCommand, datetime, bool]] = []

    @property
    def runs(self) -> list[tuple[OwnerCommand, datetime, bool]]:
        return self._runs

    def run(self, command: OwnerCommand, now: datetime, *, clock_trusted: bool) -> str:
        self._timeline.append(f'run {command.kind}')
        self._runs.append((command, now, clock_trusted))
        return f'{command.kind} done'


class StopSignalStub:
    def __init__(self, rounds: int) -> None:
        self._rounds = rounds
        self._checks = 0
        self._stopped = False
        self._waits: list[float] = []

    @property
    def waits(self) -> list[float]:
        return self._waits

    @property
    def stopped(self) -> bool:
        return self._stopped

    def is_set(self) -> bool:
        self._checks += 1
        return self._stopped or self._checks > self._rounds

    def set(self) -> None:
        self._stopped = True

    def wait(self, timeout: float) -> bool:
        self._waits.append(timeout)
        return self._stopped


class StartThreadStub:
    def __init__(self) -> None:
        self._started = 0

    @property
    def started(self) -> int:
        return self._started

    def __call__(self, target: Callable[[], None]) -> None:
        self._started += 1
        target()
