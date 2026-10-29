from dataclasses import dataclass
from datetime import datetime

from apps.core.domain.usecases.base import BaseUseCase
from apps.telemetry.repositories.journal import JournalRepository


@dataclass(frozen=True, kw_only=True)
class ListJournalEntriesInputDTO:
    limit: int


@dataclass(frozen=True, kw_only=True)
class JournalEntryDTO:
    kind: str
    subject: str
    payload: dict[str, object]
    created_at: datetime


@dataclass(frozen=True, kw_only=True)
class ListJournalEntriesOutputDTO:
    entries: tuple[JournalEntryDTO, ...]


class ListJournalEntriesUseCase(BaseUseCase[ListJournalEntriesInputDTO, ListJournalEntriesOutputDTO]):
    def __init__(self, journal: JournalRepository) -> None:
        super().__init__()
        self._journal = journal

    @classmethod
    def init(cls) -> 'ListJournalEntriesUseCase':
        return cls(journal=JournalRepository())

    def _execute(self, input_dto: ListJournalEntriesInputDTO) -> ListJournalEntriesOutputDTO:
        return ListJournalEntriesOutputDTO(
            entries=tuple(
                JournalEntryDTO(
                    kind=entry.kind,
                    subject=entry.subject,
                    payload=entry.payload,
                    created_at=entry.created_at,
                )
                for entry in self._journal.latest(limit=input_dto.limit)
            ),
        )
