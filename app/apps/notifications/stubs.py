import io
import urllib.request
from datetime import datetime

from apps.core.domain.errors import DomainError
from apps.notifications.domain.alerts import MonitoredDevice, ZoneHeating


class DeviceSourceStub:
    def __init__(self, devices: list[MonitoredDevice]) -> None:
        self._devices = devices

    def devices(self, now: datetime) -> list[MonitoredDevice]:  # noqa: ARG002
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


class MonotonicClockStub:
    def __init__(self, now: float) -> None:
        self._now = now

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds
