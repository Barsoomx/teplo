from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from apps.climate.domain.backup_heat import BackupHeat
from apps.climate.domain.climate_config import OFFSET_MAX, OFFSET_MIN, hundredths
from apps.climate.domain.modes import CommandSource
from apps.climate.domain.zone import AirTargets, ComfortWindow
from apps.climate.domain.zone_config import SETPOINT_MAX, SETPOINT_MIN, tenths
from apps.core.domain.settings import SettingProblem

ROOM = 'room'
BACKUP_MARGIN = Decimal('0.5')


@dataclass(frozen=True, kw_only=True)
class RoomOverride:
    target: Decimal
    until: datetime
    source: CommandSource


@dataclass(frozen=True, kw_only=True)
class RoomClimateSettings:
    slug: str
    name: str
    air_sensor_slug: str | None
    targets: AirTargets
    windows: tuple[ComfortWindow, ...]
    backup: BackupHeat
    guest_room: bool
    override: RoomOverride | None
    adjustable: bool


@dataclass(frozen=True, kw_only=True)
class RoomClimateConfig:
    comfort_target: Decimal
    setback_target: Decimal
    backup_below: Decimal
    backup_hysteresis: Decimal
    guest_room: bool

    def normalized(self) -> 'RoomClimateConfig':
        return RoomClimateConfig(
            comfort_target=tenths(self.comfort_target),
            setback_target=tenths(self.setback_target),
            backup_below=tenths(self.backup_below),
            backup_hysteresis=hundredths(self.backup_hysteresis),
            guest_room=self.guest_room,
        )


@dataclass(frozen=True, kw_only=True)
class RoomClimateConfigRecord:
    id: int
    slug: str
    name: str
    floor_name: str
    config: RoomClimateConfig
    air_sensor_slug: str | None
    backup_zone_slug: str | None
    backup_zone_name: str | None
    backup_live: bool


def room_climate_problems(config: RoomClimateConfig, *, has_backup: bool) -> tuple[SettingProblem, ...]:
    ranges = (
        ('comfort_target', config.comfort_target, SETPOINT_MIN, SETPOINT_MAX),
        ('setback_target', config.setback_target, SETPOINT_MIN, SETPOINT_MAX),
        ('backup_below', config.backup_below, SETPOINT_MIN, SETPOINT_MAX),
        ('backup_hysteresis', config.backup_hysteresis, OFFSET_MIN, OFFSET_MAX),
    )
    problems = [
        SettingProblem(field=field, code='out_of_range')
        for field, value, lowest, highest in ranges
        if not lowest <= value <= highest
    ]
    if config.setback_target > config.comfort_target:
        problems.append(SettingProblem(field='setback_target', code='above_comfort'))
    if has_backup and config.backup_below > config.setback_target - BACKUP_MARGIN:
        problems.append(SettingProblem(field='backup_below', code='not_below_setback'))
    return tuple(problems)
