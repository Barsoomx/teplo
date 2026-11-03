from datetime import datetime
from decimal import Decimal
from typing import Protocol

from apps.climate.domain.room_status import RoomStatus
from apps.notifications.domain.alerts import MonitoredDevice, PhaseAlarm, ZoneHeating
from apps.notifications.domain.bot import BotUpdate
from apps.notifications.domain.commands import CommandRoom, OwnerCommand
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


class CommandBot(Protocol):
    def updates(self, offset: int | None, wait_seconds: int) -> list[BotUpdate]: ...

    def send(self, text: str) -> None: ...


class CommandLedger(Protocol):
    def last_update_id(self) -> int | None: ...

    def record(self, update: BotUpdate, command: OwnerCommand, at: datetime) -> None: ...


class CommandRooms(Protocol):
    def rooms(self) -> list[CommandRoom]: ...


class OwnerCommandRunner(Protocol):
    def run(self, command: OwnerCommand, now: datetime, *, clock_trusted: bool) -> str: ...


class RoomStatusSource(Protocol):
    def rooms(self, now: datetime, *, clock_trusted: bool) -> list[RoomStatus]: ...


class PhaseReadout(Protocol):
    def phases(self) -> tuple[tuple[str, Decimal | None], ...] | None: ...
