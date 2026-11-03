from dataclasses import dataclass
from datetime import datetime
from functools import partial

from apps.climate.domain.comfort_windows import WEEKDAYS, ScheduleEdit, ZoneWeek, schedule_edit
from apps.climate.domain.ports import ComfortWindowStore
from apps.climate.repositories.comfort_period import ComfortPeriodRepository
from apps.core.domain.ports import SettingsRecorder, Transaction
from apps.core.domain.settings import SettingChange
from apps.core.domain.usecases.base import BaseUseCase
from apps.core.services.database_transaction import DatabaseTransaction
from apps.telemetry.services.settings_journal import SettingsJournal


@dataclass(frozen=True, kw_only=True)
class CopyComfortWeekInputDTO:
    zone: str
    to_zones: tuple[str, ...]
    now: datetime


@dataclass(frozen=True, kw_only=True)
class CopyComfortWeekOutputDTO:
    zones: tuple[str, ...]
    changes: tuple[SettingChange, ...]


class CopyComfortWeekUseCase(BaseUseCase[CopyComfortWeekInputDTO, CopyComfortWeekOutputDTO]):
    def __init__(self, windows: ComfortWindowStore, recorder: SettingsRecorder, transaction: Transaction) -> None:
        super().__init__()
        self._windows = windows
        self._recorder = recorder
        self._transaction = transaction

    @classmethod
    def init(cls) -> 'CopyComfortWeekUseCase':
        return cls(
            windows=ComfortPeriodRepository(),
            recorder=SettingsJournal.init(),
            transaction=DatabaseTransaction(),
        )

    def _execute(self, input_dto: CopyComfortWeekInputDTO) -> CopyComfortWeekOutputDTO:
        with self._transaction():
            return self._copy_week(input_dto)

    def _copy_week(self, input_dto: CopyComfortWeekInputDTO) -> CopyComfortWeekOutputDTO:
        source = self._windows.week_for_update(input_dto.zone)
        targets = [
            self._windows.week_for_update(slug) for slug in dict.fromkeys(input_dto.to_zones) if slug != input_dto.zone
        ]
        edits = {target.slug: _copy(source, target) for target in targets}
        changes = tuple(change for edit in edits.values() for change in edit.changes)
        if changes:
            self._recorder.record(changes, input_dto.now, write=partial(self._replace, edits))
        return CopyComfortWeekOutputDTO(zones=tuple(edits), changes=changes)

    def _replace(self, edits: dict[str, ScheduleEdit]) -> None:
        for slug, edit in edits.items():
            if edit.days:
                self._windows.replace_days(slug, edit.days)


def _copy(source: ZoneWeek, target: ZoneWeek) -> ScheduleEdit:
    days = {weekday: source.day(weekday) for weekday in WEEKDAYS}
    return schedule_edit(target, days, written=tuple(window for windows in days.values() for window in windows))
