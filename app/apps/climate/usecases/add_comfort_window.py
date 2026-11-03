from dataclasses import dataclass
from datetime import datetime, time
from functools import partial

from apps.climate.domain.comfort_windows import schedule_edit
from apps.climate.domain.ports import ComfortWindowStore
from apps.climate.domain.zone import ComfortWindow
from apps.climate.repositories.comfort_period import ComfortPeriodRepository
from apps.core.domain.ports import SettingsRecorder, Transaction
from apps.core.domain.settings import SettingChange
from apps.core.domain.usecases.base import BaseUseCase
from apps.core.services.database_transaction import DatabaseTransaction
from apps.telemetry.services.settings_journal import SettingsJournal


@dataclass(frozen=True, kw_only=True)
class AddComfortWindowInputDTO:
    zone: str
    weekday: int
    start: time
    end: time
    now: datetime


@dataclass(frozen=True, kw_only=True)
class AddComfortWindowOutputDTO:
    zone: str
    changes: tuple[SettingChange, ...]


class AddComfortWindowUseCase(BaseUseCase[AddComfortWindowInputDTO, AddComfortWindowOutputDTO]):
    def __init__(self, windows: ComfortWindowStore, recorder: SettingsRecorder, transaction: Transaction) -> None:
        super().__init__()
        self._windows = windows
        self._recorder = recorder
        self._transaction = transaction

    @classmethod
    def init(cls) -> 'AddComfortWindowUseCase':
        return cls(
            windows=ComfortPeriodRepository(),
            recorder=SettingsJournal.init(),
            transaction=DatabaseTransaction(),
        )

    def _execute(self, input_dto: AddComfortWindowInputDTO) -> AddComfortWindowOutputDTO:
        with self._transaction():
            return self._add(input_dto)

    def _add(self, input_dto: AddComfortWindowInputDTO) -> AddComfortWindowOutputDTO:
        week = self._windows.week_for_update(input_dto.zone)
        window = ComfortWindow(weekday=input_dto.weekday, start=input_dto.start, end=input_dto.end)
        edit = schedule_edit(week, {input_dto.weekday: (*week.day(input_dto.weekday), window)}, written=(window,))
        if edit.changes:
            self._recorder.record(
                edit.changes,
                input_dto.now,
                write=partial(self._windows.replace_days, week.slug, edit.days),
            )
        return AddComfortWindowOutputDTO(zone=week.slug, changes=edit.changes)
