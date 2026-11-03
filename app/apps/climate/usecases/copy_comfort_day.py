from dataclasses import dataclass, replace
from datetime import datetime
from functools import partial

from apps.climate.domain.comfort_windows import schedule_edit
from apps.climate.domain.ports import ComfortWindowStore
from apps.climate.repositories.comfort_period import ComfortPeriodRepository
from apps.core.domain.ports import SettingsRecorder, Transaction
from apps.core.domain.settings import SettingChange
from apps.core.domain.usecases.base import BaseUseCase
from apps.core.services.database_transaction import DatabaseTransaction
from apps.telemetry.services.settings_journal import SettingsJournal


@dataclass(frozen=True, kw_only=True)
class CopyComfortDayInputDTO:
    zone: str
    weekday: int
    to_weekdays: tuple[int, ...]
    now: datetime


@dataclass(frozen=True, kw_only=True)
class CopyComfortDayOutputDTO:
    zone: str
    changes: tuple[SettingChange, ...]


class CopyComfortDayUseCase(BaseUseCase[CopyComfortDayInputDTO, CopyComfortDayOutputDTO]):
    def __init__(self, windows: ComfortWindowStore, recorder: SettingsRecorder, transaction: Transaction) -> None:
        super().__init__()
        self._windows = windows
        self._recorder = recorder
        self._transaction = transaction

    @classmethod
    def init(cls) -> 'CopyComfortDayUseCase':
        return cls(
            windows=ComfortPeriodRepository(),
            recorder=SettingsJournal.init(),
            transaction=DatabaseTransaction(),
        )

    def _execute(self, input_dto: CopyComfortDayInputDTO) -> CopyComfortDayOutputDTO:
        with self._transaction():
            return self._copy_day(input_dto)

    def _copy_day(self, input_dto: CopyComfortDayInputDTO) -> CopyComfortDayOutputDTO:
        week = self._windows.week_for_update(input_dto.zone)
        source = week.day(input_dto.weekday)
        days = {
            weekday: tuple(replace(window, weekday=weekday) for window in source)
            for weekday in input_dto.to_weekdays
            if weekday != input_dto.weekday
        }
        edit = schedule_edit(week, days, written=tuple(window for windows in days.values() for window in windows))
        if edit.changes:
            self._recorder.record(
                edit.changes,
                input_dto.now,
                write=partial(self._windows.replace_days, week.slug, edit.days),
            )
        return CopyComfortDayOutputDTO(zone=week.slug, changes=edit.changes)
