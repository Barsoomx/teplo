from datetime import datetime
from typing import Protocol

from apps.notifications.domain.alerts import MonitoredDevice, PhaseAlarm, ZoneHeating
from apps.power.domain.outage import OutageRecord, PowerCut


class DeviceSource(Protocol):
    def devices(self) -> list[MonitoredDevice]: ...


class ZoneSource(Protocol):
    def zones(self, now: datetime, *, clock_trusted: bool) -> list[ZoneHeating]: ...


class PhaseSource(Protocol):
    def phases(self) -> list[PhaseAlarm]: ...


class AlertSender(Protocol):
    def send(self, text: str) -> None: ...


class PowerCutLedger(Protocol):
    def record(self, power_cut: PowerCut) -> OutageRecord | None: ...
