from dataclasses import asdict, dataclass
from datetime import datetime
from functools import partial

from apps.core.domain.errors import SettingsRejectedError
from apps.core.domain.ports import SettingsRecorder
from apps.core.domain.settings import SettingChange, setting_changes
from apps.core.domain.usecases.base import BaseUseCase
from apps.power.domain.errors import LoadNotFoundError
from apps.power.domain.load_config import LOAD, LoadConfig, LoadConfigRecord, load_problems
from apps.power.domain.ports import LoadConfigStore
from apps.power.repositories.load import LoadRepository
from apps.telemetry.services.settings_journal import SettingsJournal


@dataclass(frozen=True, kw_only=True)
class LoadEdit:
    slug: str
    config: LoadConfig


@dataclass(frozen=True, kw_only=True)
class UpdateLoadsInputDTO:
    loads: tuple[LoadEdit, ...]
    now: datetime


@dataclass(frozen=True, kw_only=True)
class UpdateLoadsOutputDTO:
    changes: tuple[SettingChange, ...]


class UpdateLoadsUseCase(BaseUseCase[UpdateLoadsInputDTO, UpdateLoadsOutputDTO]):
    def __init__(self, loads: LoadConfigStore, recorder: SettingsRecorder) -> None:
        super().__init__()
        self._loads = loads
        self._recorder = recorder

    @classmethod
    def init(cls) -> 'UpdateLoadsUseCase':
        return cls(loads=LoadRepository(), recorder=SettingsJournal.init())

    def _execute(self, input_dto: UpdateLoadsInputDTO) -> UpdateLoadsOutputDTO:
        records = {record.slug: record for record in self._loads.config_records()}
        missing = [edit.slug for edit in input_dto.loads if edit.slug not in records]
        if missing:
            raise LoadNotFoundError(missing[0])

        edits = {edit.slug: edit.config.normalized() for edit in input_dto.loads}
        configs = {slug: edits.get(slug, record.config) for slug, record in records.items()}
        problems = load_problems(configs)
        if problems:
            raise SettingsRejectedError(problems)

        changes = tuple(change for slug, record in records.items() for change in _changes(record, configs[slug]))
        changed = {slug: configs[slug] for slug in dict.fromkeys(change.subject for change in changes)}
        if changes:
            self._recorder.record(changes, input_dto.now, write=partial(self._loads.save_configs, changed))
        return UpdateLoadsOutputDTO(changes=changes)


def _changes(record: LoadConfigRecord, config: LoadConfig) -> tuple[SettingChange, ...]:
    return setting_changes(
        target=LOAD,
        subject=record.slug,
        name=record.name,
        old=asdict(record.config),
        new=asdict(config),
    )
