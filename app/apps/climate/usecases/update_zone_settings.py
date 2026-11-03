from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from functools import partial

from apps.climate.domain.ports import ZoneConfigStore
from apps.climate.domain.zone_config import ZONE, ZoneConfig, zone_config_problems
from apps.climate.repositories.zone import ZoneRepository
from apps.core.domain.errors import SettingsRejectedError
from apps.core.domain.ports import SettingsRecorder
from apps.core.domain.settings import SettingChange, setting_changes
from apps.core.domain.usecases.base import BaseUseCase
from apps.telemetry.services.settings_journal import SettingsJournal


@dataclass(frozen=True, kw_only=True)
class UpdateZoneSettingsInputDTO:
    zone: str
    name: str
    heating_enabled: bool
    comfort_floor_setpoint: Decimal
    night_floor_setpoint: Decimal
    boost_floor_setpoint: Decimal
    idle_floor_setpoint: Decimal
    floor_limit_setpoint: Decimal
    comfort_air_target: Decimal | None
    night_air_target: Decimal | None
    preheat_lead_minutes: int
    now: datetime


@dataclass(frozen=True, kw_only=True)
class UpdateZoneSettingsOutputDTO:
    changes: tuple[SettingChange, ...]


class UpdateZoneSettingsUseCase(BaseUseCase[UpdateZoneSettingsInputDTO, UpdateZoneSettingsOutputDTO]):
    def __init__(self, zones: ZoneConfigStore, recorder: SettingsRecorder) -> None:
        super().__init__()
        self._zones = zones
        self._recorder = recorder

    @classmethod
    def init(cls) -> 'UpdateZoneSettingsUseCase':
        return cls(zones=ZoneRepository(), recorder=SettingsJournal.init())

    def _execute(self, input_dto: UpdateZoneSettingsInputDTO) -> UpdateZoneSettingsOutputDTO:
        current = self._zones.config(input_dto.zone)
        config = _edited(current, input_dto)
        problems = zone_config_problems(config)
        if problems:
            raise SettingsRejectedError(problems)

        changes = setting_changes(
            target=ZONE,
            subject=input_dto.zone,
            name=config.name,
            old=asdict(current),
            new=asdict(config),
        )
        if changes:
            fields = tuple(change.field for change in changes)
            self._recorder.record(
                changes,
                input_dto.now,
                write=partial(self._zones.save_config, input_dto.zone, config, fields),
            )
        return UpdateZoneSettingsOutputDTO(changes=changes)


def _edited(current: ZoneConfig, edit: UpdateZoneSettingsInputDTO) -> ZoneConfig:
    return ZoneConfig(
        name=edit.name,
        heating_enabled=edit.heating_enabled,
        comfort_floor_setpoint=edit.comfort_floor_setpoint,
        night_floor_setpoint=edit.night_floor_setpoint,
        boost_floor_setpoint=edit.boost_floor_setpoint,
        idle_floor_setpoint=edit.idle_floor_setpoint,
        floor_limit_setpoint=edit.floor_limit_setpoint,
        comfort_air_target=current.comfort_air_target if edit.comfort_air_target is None else edit.comfort_air_target,
        night_air_target=current.night_air_target if edit.night_air_target is None else edit.night_air_target,
        preheat_lead_minutes=edit.preheat_lead_minutes,
    ).normalized()
