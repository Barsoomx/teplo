from dataclasses import dataclass
from datetime import timedelta

from apps.climate.domain.comfort_windows import WEEKDAYS, RoomWeek, StoredWindow
from apps.climate.domain.modes import ZoneKind
from apps.climate.domain.ports import ClimateConfigStore, ComfortWindowStore, HeatSourceStore, ZoneStore
from apps.climate.domain.week_plan import DayPeriod, day_periods
from apps.climate.domain.zone import ZoneSettings
from apps.climate.repositories.climate_settings import ClimateSettingsRepository
from apps.climate.repositories.comfort_period import ComfortPeriodRepository
from apps.climate.repositories.heat_source import HeatSourceRepository
from apps.climate.repositories.zone import ZoneRepository
from apps.core.domain.usecases.base import BaseUseCase
from apps.house.domain.ports import HouseStore
from apps.house.repositories.house import HouseRepository


@dataclass(frozen=True, kw_only=True)
class GetSchedulePageInputDTO:
    pass


@dataclass(frozen=True, kw_only=True)
class ScheduleDay:
    weekday: int
    windows: tuple[StoredWindow, ...]
    periods: tuple[DayPeriod, ...]


@dataclass(frozen=True, kw_only=True)
class ZoneWarmUp:
    slug: str
    name: str
    preheat_lead: timedelta
    optimal_start: bool


@dataclass(frozen=True, kw_only=True)
class RoomSchedule:
    slug: str
    name: str
    floor_name: str
    preheat_lead: timedelta
    zones: tuple[ZoneWarmUp, ...]
    sources: tuple[str, ...]
    days: tuple[ScheduleDay, ...]


@dataclass(frozen=True, kw_only=True)
class GetSchedulePageOutputDTO:
    rooms: tuple[RoomSchedule, ...]
    lead_cap: timedelta
    mornings_needed: int

    def room(self, slug: str) -> RoomSchedule | None:
        return next((room for room in self.rooms if room.slug == slug), None)

    def room_of_window(self, window_id: int) -> RoomSchedule | None:
        return next(
            (room for room in self.rooms for day in room.days for window in day.windows if window.id == window_id),
            None,
        )


class GetSchedulePageUseCase(BaseUseCase[GetSchedulePageInputDTO, GetSchedulePageOutputDTO]):
    def __init__(
        self,
        zones: ZoneStore,
        windows: ComfortWindowStore,
        house: HouseStore,
        climate_settings: ClimateConfigStore,
        sources: HeatSourceStore,
    ) -> None:
        super().__init__()
        self._zones = zones
        self._windows = windows
        self._house = house
        self._climate_settings = climate_settings
        self._sources = sources

    @classmethod
    def init(cls) -> 'GetSchedulePageUseCase':
        return cls(
            zones=ZoneRepository(),
            windows=ComfortPeriodRepository(),
            house=HouseRepository(),
            climate_settings=ClimateSettingsRepository(),
            sources=HeatSourceRepository(),
        )

    def _execute(self, input_dto: GetSchedulePageInputDTO) -> GetSchedulePageOutputDTO:  # noqa: ARG002
        climate = self._climate_settings.config_record().config
        lead_cap = timedelta(minutes=climate.preheat_lead_cap_minutes)
        floors = {room.slug: floor.name for floor in self._house.floors() for room in floor.rooms}
        warm_ups: dict[str, list[ZoneWarmUp]] = {}
        for zone in sorted(self._zones.all_settings(), key=lambda zone: zone.name):
            if zone.room_slug is not None:
                warm_ups.setdefault(zone.room_slug, []).append(_warm_up(zone, lead_cap))
        sources: dict[str, list[str]] = {}
        for source in self._sources.commanded_settings():
            sources.setdefault(source.room_slug, []).append(source.name)
        return GetSchedulePageOutputDTO(
            rooms=tuple(
                _schedule(
                    week,
                    floors.get(week.slug, ''),
                    tuple(warm_ups.get(week.slug, ())),
                    tuple(sources.get(week.slug, ())),
                )
                for week in self._windows.weeks()
            ),
            lead_cap=lead_cap,
            mornings_needed=climate.learned_mornings_needed,
        )


def _warm_up(zone: ZoneSettings, lead_cap: timedelta) -> ZoneWarmUp:
    optimal_start = zone.kind is ZoneKind.AIR and zone.air_sensor_slug is not None and zone.air_control_live
    return ZoneWarmUp(
        slug=zone.slug,
        name=zone.name,
        preheat_lead=min(zone.preheat_lead, lead_cap) if optimal_start else zone.preheat_lead,
        optimal_start=optimal_start,
    )


def _schedule(
    week: RoomWeek,
    floor_name: str,
    zones: tuple[ZoneWarmUp, ...],
    sources: tuple[str, ...],
) -> RoomSchedule:
    windows = tuple(stored.window for stored in week.windows)
    lead = max((zone.preheat_lead for zone in zones), default=timedelta(0))
    return RoomSchedule(
        slug=week.slug,
        name=week.name,
        floor_name=floor_name,
        preheat_lead=lead,
        zones=zones,
        sources=sources,
        days=tuple(
            ScheduleDay(
                weekday=weekday,
                windows=tuple(
                    sorted(
                        (stored for stored in week.windows if stored.weekday == weekday),
                        key=lambda stored: (stored.start, stored.end),
                    ),
                ),
                periods=day_periods(windows, weekday, lead),
            )
            for weekday in WEEKDAYS
        ),
    )
