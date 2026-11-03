from dataclasses import dataclass
from datetime import datetime
from functools import partial

from apps.climate.domain.comfort_windows import schedule_edit
from apps.climate.domain.errors import ComfortWindowNotFoundError
from apps.climate.domain.ports import ComfortWindowStore
from apps.climate.repositories.comfort_period import ComfortPeriodRepository
from apps.core.domain.ports import SettingsRecorder, Transaction
from apps.core.domain.settings import SettingChange
from apps.core.domain.usecases.base import BaseUseCase
from apps.core.services.database_transaction import DatabaseTransaction
from apps.telemetry.services.settings_journal import SettingsJournal


@dataclass(frozen=True, kw_only=True)
class DeleteComfortWindowInputDTO:
    window: int
    now: datetime


@dataclass(frozen=True, kw_only=True)
class DeleteComfortWindowOutputDTO:
    zone: str
    changes: tuple[SettingChange, ...]


class DeleteComfortWindowUseCase(BaseUseCase[DeleteComfortWindowInputDTO, DeleteComfortWindowOutputDTO]):
    def __init__(self, windows: ComfortWindowStore, recorder: SettingsRecorder, transaction: Transaction) -> None:
        super().__init__()
        self._windows = windows
        self._recorder = recorder
        self._transaction = transaction

    @classmethod
    def init(cls) -> 'DeleteComfortWindowUseCase':
        return cls(
            windows=ComfortPeriodRepository(),
            recorder=SettingsJournal.init(),
            transaction=DatabaseTransaction(),
        )

    def _execute(self, input_dto: DeleteComfortWindowInputDTO) -> DeleteComfortWindowOutputDTO:
        with self._transaction():
            return self._delete(input_dto)

    def _delete(self, input_dto: DeleteComfortWindowInputDTO) -> DeleteComfortWindowOutputDTO:
        week = self._windows.window_week_for_update(input_dto.window)
        stored = week.stored(input_dto.window)
        if stored is None:
            raise ComfortWindowNotFoundError(input_dto.window)

        others = tuple(other.window for other in week.windows if other.weekday == stored.weekday and other != stored)
        edit = schedule_edit(week, {stored.weekday: others}, written=())
        if edit.changes:
            self._recorder.record(
                edit.changes,
                input_dto.now,
                write=partial(self._windows.replace_days, week.slug, edit.days),
            )
        return DeleteComfortWindowOutputDTO(zone=week.slug, changes=edit.changes)
