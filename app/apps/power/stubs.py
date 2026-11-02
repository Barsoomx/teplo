from dataclasses import replace
from datetime import datetime

from apps.core.domain.errors import DomainError
from apps.devices.domain.kinds import DeviceKind
from apps.power.domain.control import LoadControl
from apps.power.domain.loads import LearnedPhase, LiveShed, LoadRecord

JournalLine = tuple[str, str, dict[str, object], datetime]
LoadWrite = tuple[str, str, LiveShed | LearnedPhase | None]


class OutageJournalStub:
    def __init__(self, errors: tuple[Exception, ...]) -> None:
        self._errors = errors
        self._attempts = 0
        self._lines: list[JournalLine] = []

    @property
    def lines(self) -> list[JournalLine]:
        return self._lines

    def add(self, kind: str, subject: str, payload: dict[str, object], created_at: datetime) -> None:
        self._attempts += 1
        if self._attempts <= len(self._errors):
            raise self._errors[self._attempts - 1]

        self._lines.append((kind, subject, payload, created_at))


class LoadStoreStub:
    def __init__(self, records: list[LoadRecord], live_zones: frozenset[str]) -> None:
        self._records = records
        self._live_zones = live_zones
        self._reads = 0
        self._writes: list[LoadWrite] = []

    @property
    def records(self) -> list[LoadRecord]:
        return self._records

    @property
    def reads(self) -> int:
        return self._reads

    @property
    def writes(self) -> list[LoadWrite]:
        return self._writes

    def edit(self, records: list[LoadRecord], live_zones: frozenset[str]) -> None:
        self._records = records
        self._live_zones = live_zones

    def all_records(self) -> list[LoadRecord]:
        self._reads += 1
        return list(self._records)

    def live_zone_slugs(self) -> frozenset[str]:
        return self._live_zones

    def mark_shed(self, slug: str, shed: LiveShed) -> None:
        self._writes.append(('mark_shed', slug, shed))
        self._records = [replace(record, shed=shed) if record.slug == slug else record for record in self._records]

    def clear_shed(self, slug: str) -> None:
        self._writes.append(('clear_shed', slug, None))
        self._records = [replace(record, shed=None) if record.slug == slug else record for record in self._records]

    def save_learned(self, slug: str, learned: LearnedPhase) -> None:
        self._writes.append(('save_learned', slug, learned))
        self._records = [
            replace(record, learned=learned) if record.slug == slug else record for record in self._records
        ]


class LoadSettingsStoreStub:
    def __init__(self, control: LoadControl) -> None:
        self._control = control

    def edit(self, control: LoadControl) -> None:
        self._control = control

    def control(self) -> LoadControl:
        return self._control


class DeviceDirectoryStub:
    def __init__(self, slugs: dict[DeviceKind, list[str]]) -> None:
        self._slugs = slugs

    def slugs_of_kind(self, kind: DeviceKind) -> list[str]:
        return self._slugs.get(kind, [])


class ZoneSwitchStub:
    def __init__(self, error: DomainError | None) -> None:
        self._error = error
        self._switched_off: list[tuple[str, str, datetime]] = []
        self._switched_on: list[tuple[str, str, datetime]] = []

    @property
    def switched_off(self) -> list[tuple[str, str, datetime]]:
        return self._switched_off

    @property
    def switched_on(self) -> list[tuple[str, str, datetime]]:
        return self._switched_on

    def switch_off(self, zone: str, thermostat: str, at: datetime) -> None:
        if self._error is not None:
            raise self._error

        self._switched_off.append((zone, thermostat, at))

    def switch_on(self, zone: str, thermostat: str, at: datetime) -> None:
        if self._error is not None:
            raise self._error

        self._switched_on.append((zone, thermostat, at))


class PermitStub:
    def __init__(self, granted: bool) -> None:
        self._granted = granted
        self._asked = 0

    @property
    def asked(self) -> int:
        return self._asked

    def __call__(self) -> bool:
        self._asked += 1
        return self._granted
