from datetime import datetime
from typing import Protocol

from apps.notifications.domain.alerts import MonitoredDevice, ZoneHeating


class DeviceSource(Protocol):
    def devices(self) -> list[MonitoredDevice]: ...


class ZoneSource(Protocol):
    def zones(self, now: datetime, *, clock_trusted: bool) -> list[ZoneHeating]: ...


class AlertSender(Protocol):
    def send(self, text: str) -> None: ...
