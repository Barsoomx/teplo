from dataclasses import dataclass

from django.conf import settings

from apps.climate.domain.climate_config import ClimateConfigRecord
from apps.climate.domain.ports import ClimateConfigStore, RoomClimateConfigStore, ZoneConfigStore
from apps.climate.domain.room_climate import RoomClimateConfigRecord
from apps.climate.domain.zone_config import ZoneConfigRecord
from apps.climate.repositories.climate_settings import ClimateSettingsRepository
from apps.climate.repositories.room_climate import RoomClimateRepository
from apps.climate.repositories.zone import ZoneRepository
from apps.core.domain.usecases.base import BaseUseCase
from apps.power.domain.load_config import LoadConfigRecord, LoadSettingsRecord
from apps.power.domain.ports import LoadConfigStore, LoadSettingsConfigStore
from apps.power.repositories.load import LoadRepository
from apps.power.repositories.load_settings import LoadSettingsRepository


@dataclass(frozen=True, kw_only=True)
class GetSettingsPageInputDTO:
    pass


@dataclass(frozen=True, kw_only=True)
class GetSettingsPageOutputDTO:
    rooms: tuple[RoomClimateConfigRecord, ...]
    zones: tuple[ZoneConfigRecord, ...]
    climate: ClimateConfigRecord
    load_settings: LoadSettingsRecord
    loads: tuple[LoadConfigRecord, ...]
    dry_run: bool

    def room(self, slug: str) -> RoomClimateConfigRecord | None:
        return next((room for room in self.rooms if room.slug == slug), None)

    def zone(self, slug: str) -> ZoneConfigRecord | None:
        return next((zone for zone in self.zones if zone.slug == slug), None)


class GetSettingsPageUseCase(BaseUseCase[GetSettingsPageInputDTO, GetSettingsPageOutputDTO]):
    def __init__(
        self,
        rooms: RoomClimateConfigStore,
        zones: ZoneConfigStore,
        climate_settings: ClimateConfigStore,
        load_settings: LoadSettingsConfigStore,
        loads: LoadConfigStore,
        dry_run: bool,
    ) -> None:
        super().__init__()
        self._rooms = rooms
        self._zones = zones
        self._climate_settings = climate_settings
        self._load_settings = load_settings
        self._loads = loads
        self._dry_run = dry_run

    @classmethod
    def init(cls) -> 'GetSettingsPageUseCase':
        return cls(
            rooms=RoomClimateRepository(),
            zones=ZoneRepository(),
            climate_settings=ClimateSettingsRepository(),
            load_settings=LoadSettingsRepository(),
            loads=LoadRepository(),
            dry_run=settings.TEPLO_DRY_RUN,
        )

    def _execute(self, input_dto: GetSettingsPageInputDTO) -> GetSettingsPageOutputDTO:  # noqa: ARG002
        return GetSettingsPageOutputDTO(
            rooms=tuple(self._rooms.config_records()),
            zones=tuple(sorted(self._zones.config_records(), key=lambda zone: zone.config.name)),
            climate=self._climate_settings.config_record(),
            load_settings=self._load_settings.config_record(),
            loads=tuple(sorted(self._loads.config_records(), key=lambda load: load.config.priority)),
            dry_run=self._dry_run,
        )
