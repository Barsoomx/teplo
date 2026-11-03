from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from functools import partial

from apps.core.domain.errors import SettingsRejectedError
from apps.core.domain.ports import SettingsRecorder, Transaction
from apps.core.domain.settings import SettingChange, setting_changes
from apps.core.domain.usecases.base import BaseUseCase
from apps.core.services.database_transaction import DatabaseTransaction
from apps.power.domain.load_config import LOAD_SETTINGS, LoadSettingsConfig, load_settings_problems
from apps.power.domain.ports import LoadSettingsConfigStore
from apps.power.repositories.load_settings import LoadSettingsRepository
from apps.telemetry.services.settings_journal import SettingsJournal


@dataclass(frozen=True, kw_only=True)
class UpdateLoadSettingsInputDTO:
    start_ceiling_amps: Decimal
    shed_amps: Decimal
    shed_hold_seconds: int
    restore_amps: Decimal
    restore_hold_seconds: int
    start_interval_seconds: int
    meter_lost_after_seconds: int
    default_load_amps: Decimal
    now: datetime


@dataclass(frozen=True, kw_only=True)
class UpdateLoadSettingsOutputDTO:
    changes: tuple[SettingChange, ...]


class UpdateLoadSettingsUseCase(BaseUseCase[UpdateLoadSettingsInputDTO, UpdateLoadSettingsOutputDTO]):
    def __init__(
        self,
        load_settings: LoadSettingsConfigStore,
        recorder: SettingsRecorder,
        transaction: Transaction,
    ) -> None:
        super().__init__()
        self._load_settings = load_settings
        self._recorder = recorder
        self._transaction = transaction

    @classmethod
    def init(cls) -> 'UpdateLoadSettingsUseCase':
        return cls(
            load_settings=LoadSettingsRepository(),
            recorder=SettingsJournal.init(),
            transaction=DatabaseTransaction(),
        )

    def _execute(self, input_dto: UpdateLoadSettingsInputDTO) -> UpdateLoadSettingsOutputDTO:
        with self._transaction():
            changes = self._update(input_dto)
        return UpdateLoadSettingsOutputDTO(changes=changes)

    def _update(self, input_dto: UpdateLoadSettingsInputDTO) -> tuple[SettingChange, ...]:
        current = self._load_settings.config_record_for_update().config
        config = LoadSettingsConfig(
            start_ceiling_amps=input_dto.start_ceiling_amps,
            shed_amps=input_dto.shed_amps,
            shed_hold_seconds=input_dto.shed_hold_seconds,
            restore_amps=input_dto.restore_amps,
            restore_hold_seconds=input_dto.restore_hold_seconds,
            start_interval_seconds=input_dto.start_interval_seconds,
            meter_lost_after_seconds=input_dto.meter_lost_after_seconds,
            default_load_amps=input_dto.default_load_amps,
        ).normalized()
        problems = load_settings_problems(config)
        if problems:
            raise SettingsRejectedError(problems)

        changes = setting_changes(target=LOAD_SETTINGS, subject='', name='', old=asdict(current), new=asdict(config))
        if changes:
            fields = tuple(change.field for change in changes)
            self._recorder.record(
                changes,
                input_dto.now,
                write=partial(self._load_settings.save_config, config, fields),
            )
        return changes
