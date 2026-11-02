import io
import urllib.request
from datetime import datetime

from apps.core.domain.errors import DomainError
from apps.notifications.domain.alerts import MonitoredDevice, ZoneHeating
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
    def __init__(self, error: Exception | None) -> None:
        self._error = error
        self._requests: list[tuple[urllib.request.Request, float]] = []

    @property
    def requests(self) -> list[tuple[urllib.request.Request, float]]:
        return self._requests

    def open(self, fullurl: urllib.request.Request, *, timeout: float) -> io.BytesIO:
        self._requests.append((fullurl, timeout))
        if self._error is not None:
            raise self._error

        return io.BytesIO(b'{"ok": true}')


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
