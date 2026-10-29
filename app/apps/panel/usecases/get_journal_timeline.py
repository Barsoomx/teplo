from dataclasses import dataclass
from datetime import date, datetime

from django.utils import timezone

from apps.climate.repositories.zone import ZoneRepository
from apps.core.domain.usecases.base import BaseUseCase
from apps.telemetry.repositories.journal import JournalRepository


@dataclass(frozen=True, kw_only=True)
class GetJournalTimelineInputDTO:
    limit: int


@dataclass(frozen=True, kw_only=True)
class TimelineEntry:
    kind: str
    subject: str
    subject_name: str
    payload: dict[str, object]
    created_at: datetime


@dataclass(frozen=True, kw_only=True)
class TimelineDay:
    day: date
    entries: tuple[TimelineEntry, ...]


@dataclass(frozen=True, kw_only=True)
class GetJournalTimelineOutputDTO:
    days: tuple[TimelineDay, ...]


class GetJournalTimelineUseCase(BaseUseCase[GetJournalTimelineInputDTO, GetJournalTimelineOutputDTO]):
    def __init__(self, journal: JournalRepository, zones: ZoneRepository) -> None:
        super().__init__()
        self._journal = journal
        self._zones = zones

    @classmethod
    def init(cls) -> 'GetJournalTimelineUseCase':
        return cls(journal=JournalRepository(), zones=ZoneRepository())

    def _execute(self, input_dto: GetJournalTimelineInputDTO) -> GetJournalTimelineOutputDTO:
        names = self._subject_names()
        days: dict[date, list[TimelineEntry]] = {}
        for entry in self._journal.latest(limit=input_dto.limit):
            days.setdefault(timezone.localdate(entry.created_at), []).append(
                TimelineEntry(
                    kind=entry.kind,
                    subject=entry.subject,
                    subject_name=names.get(entry.subject, entry.subject),
                    payload=entry.payload,
                    created_at=entry.created_at,
                ),
            )
        return GetJournalTimelineOutputDTO(
            days=tuple(TimelineDay(day=day, entries=tuple(entries)) for day, entries in days.items()),
        )

    def _subject_names(self) -> dict[str, str]:
        names: dict[str, str] = {}
        for zone in self._zones.all_settings():
            names[zone.slug] = zone.name
            names[zone.thermostat_slug] = zone.name
        return names
