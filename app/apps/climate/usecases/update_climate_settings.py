from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from functools import partial

from apps.climate.domain.climate_config import CLIMATE_SETTINGS, ClimateConfig, climate_config_problems
from apps.climate.domain.ports import ClimateConfigStore
from apps.climate.repositories.climate_settings import ClimateSettingsRepository
from apps.core.domain.errors import SettingsRejectedError
from apps.core.domain.ports import SettingsRecorder, Transaction
from apps.core.domain.settings import SettingChange, setting_changes
from apps.core.domain.usecases.base import BaseUseCase
from apps.core.services.database_transaction import DatabaseTransaction
from apps.telemetry.services.settings_journal import SettingsJournal


@dataclass(frozen=True, kw_only=True)
class UpdateClimateSettingsInputDTO:
    air_start_offset: Decimal
    initial_early_stop_offset: Decimal
    air_min_band: Decimal
    learned_mornings_needed: int
    preheat_lead_cap_minutes: int
    now: datetime


@dataclass(frozen=True, kw_only=True)
class UpdateClimateSettingsOutputDTO:
    changes: tuple[SettingChange, ...]


class UpdateClimateSettingsUseCase(BaseUseCase[UpdateClimateSettingsInputDTO, UpdateClimateSettingsOutputDTO]):
    def __init__(
        self,
        climate_settings: ClimateConfigStore,
        recorder: SettingsRecorder,
        transaction: Transaction,
    ) -> None:
        super().__init__()
        self._climate_settings = climate_settings
        self._recorder = recorder
        self._transaction = transaction

    @classmethod
    def init(cls) -> 'UpdateClimateSettingsUseCase':
        return cls(
            climate_settings=ClimateSettingsRepository(),
            recorder=SettingsJournal.init(),
            transaction=DatabaseTransaction(),
        )

    def _execute(self, input_dto: UpdateClimateSettingsInputDTO) -> UpdateClimateSettingsOutputDTO:
        with self._transaction():
            changes = self._update(input_dto)
        return UpdateClimateSettingsOutputDTO(changes=changes)

    def _update(self, input_dto: UpdateClimateSettingsInputDTO) -> tuple[SettingChange, ...]:
        current = self._climate_settings.config_record_for_update().config
        config = ClimateConfig(
            air_start_offset=input_dto.air_start_offset,
            initial_early_stop_offset=input_dto.initial_early_stop_offset,
            air_min_band=input_dto.air_min_band,
            learned_mornings_needed=input_dto.learned_mornings_needed,
            preheat_lead_cap_minutes=input_dto.preheat_lead_cap_minutes,
        ).normalized()
        problems = climate_config_problems(config)
        if problems:
            raise SettingsRejectedError(problems)

        changes = setting_changes(target=CLIMATE_SETTINGS, subject='', name='', old=asdict(current), new=asdict(config))
        if changes:
            fields = tuple(change.field for change in changes)
            self._recorder.record(
                changes,
                input_dto.now,
                write=partial(self._climate_settings.save_config, config, fields),
            )
        return changes
