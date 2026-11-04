from collections.abc import Collection, Mapping
from dataclasses import replace
from datetime import datetime
from functools import partial

from apps.climate.domain.ports import WriteGate
from apps.core.domain.errors import DomainError
from apps.devices.domain.kinds import DeviceKind
from apps.power.domain.control import LoadControl
from apps.power.domain.load_config import LoadConfig, LoadConfigRecord, LoadSettingsConfig, LoadSettingsRecord
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
    def __init__(
        self,
        records: list[LoadRecord],
        live_zones: frozenset[str],
        write_errors: tuple[Exception, ...] = (),
    ) -> None:
        self._records = records
        self._live_zones = live_zones
        self._write_errors = write_errors
        self._failed_writes = 0
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
        self._write(('mark_shed', slug, shed))
        self._records = [replace(record, shed=shed) if record.slug == slug else record for record in self._records]

    def clear_shed(self, slug: str) -> None:
        self._write(('clear_shed', slug, None))
        self._records = [replace(record, shed=None) if record.slug == slug else record for record in self._records]

    def save_learned(self, slug: str, learned: LearnedPhase) -> None:
        self._write(('save_learned', slug, learned))
        self._records = [
            replace(record, learned=learned) if record.slug == slug else record for record in self._records
        ]

    def _write(self, write: LoadWrite) -> None:
        if self._failed_writes < len(self._write_errors):
            self._failed_writes += 1
            raise self._write_errors[self._failed_writes - 1]

        self._writes.append(write)


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
    def __init__(
        self,
        writes: WriteGate,
        error: DomainError | None,
        stamped: bool = True,
        on_error: DomainError | None = None,
        on_stamp_errors: tuple[Exception, ...] = (),
    ) -> None:
        self._writes = writes
        self._error = error
        self._stamped = stamped
        self._on_error = on_error
        self._on_stamp_errors = on_stamp_errors
        self._refused_on_stamps = 0
        self._switched_off: list[tuple[str, str, datetime]] = []
        self._switched_on: list[tuple[str, str, datetime]] = []

    @property
    def switched_off(self) -> list[tuple[str, str, datetime]]:
        return self._switched_off

    @property
    def switched_on(self) -> list[tuple[str, str, datetime]]:
        return self._switched_on

    def switch_off(self, zone: str, thermostat: str, at: datetime) -> bool:
        if self._error is not None:
            raise self._error

        shed = self._writes.override(zone, at)
        self._writes.send(shed, partial(self._switched_off.append, (zone, thermostat, at)))
        if self._stamped:
            self._writes.stored(shed)
        return self._stamped

    def switch_on(
        self,
        zone: str,
        thermostat: str,
        at: datetime,
        *,
        stamped_at: datetime | None,
        read_at: float,
    ) -> bool:
        if self._error is not None:
            raise self._error

        reservation = self._writes.reserve(zone, at, stamped_at=stamped_at, read_at=read_at)
        if reservation is None:
            return False

        with self._writes.stamping(reservation):
            self._stamp_on()
        return self._writes.write(reservation, partial(self._power_on, (zone, thermostat, at)))

    def _stamp_on(self) -> None:
        if self._refused_on_stamps < len(self._on_stamp_errors):
            self._refused_on_stamps += 1
            raise self._on_stamp_errors[self._refused_on_stamps - 1]

    def _power_on(self, switch: tuple[str, str, datetime]) -> None:
        if self._on_error is not None:
            raise self._on_error

        self._switched_on.append(switch)


class ZoneWriteStampsStub:
    def __init__(self, error: Exception | None) -> None:
        self._error = error
        self._stamps: list[tuple[str, datetime]] = []
        self._sheds: list[tuple[str, datetime]] = []

    @property
    def stamps(self) -> list[tuple[str, datetime]]:
        return self._stamps

    @property
    def sheds(self) -> list[tuple[str, datetime]]:
        return self._sheds

    def mark_commanded(self, slug: str, at: datetime) -> None:
        self._stamp(self._stamps, slug, at)

    def mark_shed(self, slug: str, at: datetime) -> None:
        self._stamp(self._sheds, slug, at)

    def _stamp(self, stamps: list[tuple[str, datetime]], slug: str, at: datetime) -> None:
        if self._error is not None:
            raise self._error

        stamps.append((slug, at))


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


class LoadSettingsConfigStoreStub:
    def __init__(self, record: LoadSettingsRecord) -> None:
        self._record = record
        self._saved: list[tuple[LoadSettingsConfig, tuple[str, ...]]] = []

    @property
    def saved(self) -> list[tuple[LoadSettingsConfig, tuple[str, ...]]]:
        return self._saved

    def config_record(self) -> LoadSettingsRecord:
        return self._record

    def config_record_for_update(self) -> LoadSettingsRecord:
        return self._record

    def save_config(self, config: LoadSettingsConfig, fields: Collection[str]) -> None:
        self._saved.append((config, tuple(fields)))
        written = {field: getattr(config, field) for field in fields}
        self._record = replace(self._record, config=replace(self._record.config, **written))


class LoadConfigStoreStub:
    def __init__(self, records: list[LoadConfigRecord]) -> None:
        self._records = records
        self._saved: list[dict[str, LoadConfig]] = []

    @property
    def saved(self) -> list[dict[str, LoadConfig]]:
        return self._saved

    def config_records(self) -> list[LoadConfigRecord]:
        return list(self._records)

    def config_records_for_update(self) -> list[LoadConfigRecord]:
        return list(self._records)

    def save_configs(self, configs: Mapping[str, LoadConfig]) -> None:
        self._saved.append(dict(configs))
        self._records = [
            replace(record, config=configs[record.slug]) if record.slug in configs else record
            for record in self._records
        ]
