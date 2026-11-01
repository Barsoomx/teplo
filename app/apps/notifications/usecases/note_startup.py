from dataclasses import dataclass
from datetime import datetime

from apps.core.domain.usecases.base import BaseUseCase
from apps.notifications.domain.outage import StartupNote
from apps.notifications.services.alert_book import AlertBook, alert_book


@dataclass(frozen=True, kw_only=True)
class NoteStartupInputDTO:
    last_activity: datetime | None
    monotonic: float


@dataclass(frozen=True, kw_only=True)
class NoteStartupOutputDTO:
    note: StartupNote


class NoteStartupUseCase(BaseUseCase[NoteStartupInputDTO, NoteStartupOutputDTO]):
    def __init__(self, book: AlertBook) -> None:
        super().__init__()
        self._book = book

    @classmethod
    def init(cls) -> 'NoteStartupUseCase':
        return cls(book=alert_book)

    def _execute(self, input_dto: NoteStartupInputDTO) -> NoteStartupOutputDTO:
        note = StartupNote(last_activity=input_dto.last_activity, monotonic=input_dto.monotonic)
        self._book.note_startup(note)
        return NoteStartupOutputDTO(note=note)
