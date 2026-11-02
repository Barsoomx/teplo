from dataclasses import dataclass
from datetime import date, datetime

from django.utils import timezone

from apps.climate.repositories.zone import ZoneRepository
from apps.core.domain.usecases.base import BaseUseCase
from apps.devices.domain.device import DeviceSettings
from apps.devices.domain.kinds import DeviceKind
from apps.devices.repositories.device import DeviceRepository
from apps.power.domain.loads import LoadKind, LoadRecord
from apps.power.domain.outage import OutageRecord
from apps.power.repositories.load import LoadRepository
from apps.power.repositories.outage import OutageRepository
from apps.power.services.outage_overloads import OutageOverloads
from apps.telemetry.models import JournalEntry
from apps.telemetry.repositories.journal import JournalRepository

POWER_OUTAGE = 'power_outage'
LOAD_DEVICE_KINDS = {LoadKind.PLUG: DeviceKind.PLUG, LoadKind.ZONE: DeviceKind.THERMOSTAT}


@dataclass(frozen=True, kw_only=True)
class GetJournalTimelineInputDTO:
    limit: int


@dataclass(frozen=True, kw_only=True)
class TimelineEntry:
    kind: str
    subject: str
    subject_name: str
    device_kind: DeviceKind | None
    payload: dict[str, object]
    created_at: datetime
    outage: OutageRecord | None = None


@dataclass(frozen=True, kw_only=True)
class TimelineDay:
    day: date
    entries: tuple[TimelineEntry, ...]


@dataclass(frozen=True, kw_only=True)
class GetJournalTimelineOutputDTO:
    days: tuple[TimelineDay, ...]


class GetJournalTimelineUseCase(BaseUseCase[GetJournalTimelineInputDTO, GetJournalTimelineOutputDTO]):
    def __init__(
        self,
        journal: JournalRepository,
        zones: ZoneRepository,
        devices: DeviceRepository,
        loads: LoadRepository,
        outages: OutageRepository,
        overloads: OutageOverloads,
    ) -> None:
        super().__init__()
        self._journal = journal
        self._zones = zones
        self._devices = devices
        self._loads = loads
        self._outages = outages
        self._overloads = overloads

    @classmethod
    def init(cls) -> 'GetJournalTimelineUseCase':
        return cls(
            journal=JournalRepository(),
            zones=ZoneRepository(),
            devices=DeviceRepository(),
            loads=LoadRepository(),
            outages=OutageRepository(),
            overloads=OutageOverloads.init(),
        )

    def _execute(self, input_dto: GetJournalTimelineInputDTO) -> GetJournalTimelineOutputDTO:
        devices = {device.slug: device for device in self._devices.all_settings()}
        loads = {load.slug: load for load in self._loads.all_records()}
        names = self._subject_names(devices, loads)
        journal = self._journal.latest(limit=input_dto.limit)
        outage_ids = [_outage_id(entry) for entry in journal]
        recorded = self._outages.by_ids([outage_id for outage_id in outage_ids if outage_id is not None])
        outages = {outage_id: self._overloads.noted(outage) for outage_id, outage in recorded.items()}
        days: dict[date, list[TimelineEntry]] = {}
        for entry, outage_id in zip(journal, outage_ids, strict=True):
            days.setdefault(timezone.localdate(entry.created_at), []).append(
                TimelineEntry(
                    kind=entry.kind,
                    subject=entry.subject,
                    subject_name=names.get(entry.subject, entry.subject),
                    device_kind=_device_kind(devices.get(entry.subject), loads.get(entry.subject)),
                    payload=entry.payload,
                    created_at=entry.created_at,
                    outage=None if outage_id is None else outages.get(outage_id),
                ),
            )
        return GetJournalTimelineOutputDTO(
            days=tuple(TimelineDay(day=day, entries=tuple(entries)) for day, entries in days.items()),
        )

    def _subject_names(self, devices: dict[str, DeviceSettings], loads: dict[str, LoadRecord]) -> dict[str, str]:
        names = {slug: device.name for slug, device in devices.items()}
        for zone in self._zones.all_settings():
            names[zone.slug] = zone.name
            names[zone.thermostat_slug] = zone.name
        for slug, load in loads.items():
            names.setdefault(slug, load.name)
        return names


def _device_kind(device: DeviceSettings | None, load: LoadRecord | None) -> DeviceKind | None:
    if device is not None:
        return device.kind

    return None if load is None else LOAD_DEVICE_KINDS[load.kind]


def _outage_id(entry: JournalEntry) -> int | None:
    if entry.kind != POWER_OUTAGE or not isinstance(entry.payload, dict):
        return None

    outage_id = entry.payload.get('outage_id')
    return outage_id if isinstance(outage_id, int) else None
