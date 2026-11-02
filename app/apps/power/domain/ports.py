from datetime import datetime
from typing import Protocol

from apps.devices.domain.kinds import DeviceKind
from apps.power.domain.control import LoadControl
from apps.power.domain.loads import LearnedPhase, LiveShed, LoadRecord
from apps.power.domain.outage import OutageRecord, PowerCut


class OutageStore(Protocol):
    def add(self, power_cut: PowerCut) -> OutageRecord | None: ...


class OutageJournal(Protocol):
    def add(self, kind: str, subject: str, payload: dict[str, object], created_at: datetime) -> None: ...


class LoadStore(Protocol):
    def all_records(self) -> list[LoadRecord]: ...

    def live_zone_slugs(self) -> frozenset[str]: ...

    def mark_shed(self, slug: str, shed: LiveShed) -> None: ...

    def clear_shed(self, slug: str) -> None: ...

    def save_learned(self, slug: str, learned: LearnedPhase) -> None: ...


class LoadSettingsStore(Protocol):
    def control(self) -> LoadControl: ...


class DeviceDirectory(Protocol):
    def slugs_of_kind(self, kind: DeviceKind) -> list[str]: ...


class ZoneSwitch(Protocol):
    def switch_off(self, zone: str, thermostat: str, at: datetime) -> None: ...

    def switch_on(self, zone: str, thermostat: str, at: datetime) -> None: ...
