from dataclasses import dataclass
from datetime import datetime, time
from functools import partial

from apps.climate.domain.comfort_windows import schedule_edit
from apps.climate.domain.errors import ComfortWindowNotFoundError
from apps.climate.domain.ports import ComfortWindowStore
from apps.climate.domain.zone import ComfortWindow
from apps.climate.repositories.comfort_period import ComfortPeriodRepository
from apps.core.domain.ports import SettingsRecorder
from apps.core.domain.settings import SettingChange
from apps.core.domain.usecases.base import BaseUseCase
from apps.telemetry.services.settings_journal import SettingsJournal


@dataclass(frozen=True, kw_only=True)
class ChangeComfortWindowInputDTO:
    window: int
    start: time
    end: time
    now: datetime


@dataclass(frozen=True, kw_only=True)
class ChangeComfortWindowOutputDTO:
    zone: str
    changes: tuple[SettingChange, ...]


class ChangeComfortWindowUseCase(BaseUseCase[ChangeComfortWindowInputDTO, ChangeComfortWindowOutputDTO]):
    def __init__(self, windows: ComfortWindowStore, recorder: SettingsRecorder) -> None:
        super().__init__()
        self._windows = windows
        self._recorder = recorder

    @classmethod
    def init(cls) -> 'ChangeComfortWindowUseCase':
        return cls(windows=ComfortPeriodRepository(), recorder=SettingsJournal.init())

    def _execute(self, input_dto: ChangeComfortWindowInputDTO) -> ChangeComfortWindowOutputDTO:
        week = self._windows.window_week(input_dto.window)
        stored = week.stored(input_dto.window)
        if stored is None:
            raise ComfortWindowNotFoundError(input_dto.window)

        window = ComfortWindow(weekday=stored.weekday, start=input_dto.start, end=input_dto.end)
        others = tuple(other.window for other in week.windows if other.weekday == stored.weekday and other != stored)
        edit = schedule_edit(week, {stored.weekday: (*others, window)}, written=(window,))
        if edit.changes:
            self._recorder.record(
                edit.changes,
                input_dto.now,
                write=partial(self._windows.replace_days, week.slug, edit.days),
            )
        return ChangeComfortWindowOutputDTO(zone=week.slug, changes=edit.changes)
