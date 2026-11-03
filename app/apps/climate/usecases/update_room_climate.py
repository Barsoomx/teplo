from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from functools import partial

from apps.climate.domain.ports import RoomClimateConfigStore
from apps.climate.domain.room_climate import ROOM, RoomClimateConfig, room_climate_problems
from apps.climate.repositories.room_climate import RoomClimateRepository
from apps.core.domain.errors import SettingsRejectedError
from apps.core.domain.ports import SettingsRecorder, Transaction
from apps.core.domain.settings import SettingChange, setting_changes
from apps.core.domain.usecases.base import BaseUseCase
from apps.core.services.database_transaction import DatabaseTransaction
from apps.telemetry.services.settings_journal import SettingsJournal


@dataclass(frozen=True, kw_only=True)
class UpdateRoomClimateInputDTO:
    room: str
    comfort_target: Decimal
    setback_target: Decimal
    backup_below: Decimal | None
    backup_hysteresis: Decimal | None
    guest_room: bool
    now: datetime


@dataclass(frozen=True, kw_only=True)
class UpdateRoomClimateOutputDTO:
    changes: tuple[SettingChange, ...]


class UpdateRoomClimateUseCase(BaseUseCase[UpdateRoomClimateInputDTO, UpdateRoomClimateOutputDTO]):
    def __init__(self, rooms: RoomClimateConfigStore, recorder: SettingsRecorder, transaction: Transaction) -> None:
        super().__init__()
        self._rooms = rooms
        self._recorder = recorder
        self._transaction = transaction

    @classmethod
    def init(cls) -> 'UpdateRoomClimateUseCase':
        return cls(rooms=RoomClimateRepository(), recorder=SettingsJournal.init(), transaction=DatabaseTransaction())

    def _execute(self, input_dto: UpdateRoomClimateInputDTO) -> UpdateRoomClimateOutputDTO:
        with self._transaction():
            changes = self._update(input_dto)
        return UpdateRoomClimateOutputDTO(changes=changes)

    def _update(self, input_dto: UpdateRoomClimateInputDTO) -> tuple[SettingChange, ...]:
        record = self._rooms.config_for_update(input_dto.room)
        config = _edited(record.config, input_dto)
        problems = room_climate_problems(config, has_backup=record.backup_zone_slug is not None)
        if problems:
            raise SettingsRejectedError(problems)

        changes = setting_changes(
            target=ROOM,
            subject=record.slug,
            name=record.name,
            old=asdict(record.config),
            new=asdict(config),
        )
        if changes:
            fields = tuple(change.field for change in changes)
            self._recorder.record(
                changes,
                input_dto.now,
                write=partial(self._rooms.save_config, record.slug, config, fields),
            )
        return changes


def _edited(current: RoomClimateConfig, edit: UpdateRoomClimateInputDTO) -> RoomClimateConfig:
    return RoomClimateConfig(
        comfort_target=edit.comfort_target,
        setback_target=edit.setback_target,
        backup_below=current.backup_below if edit.backup_below is None else edit.backup_below,
        backup_hysteresis=current.backup_hysteresis if edit.backup_hysteresis is None else edit.backup_hysteresis,
        guest_room=edit.guest_room,
    ).normalized()
