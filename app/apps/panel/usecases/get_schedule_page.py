from dataclasses import dataclass
from datetime import timedelta

from apps.climate.domain.comfort_windows import WEEKDAYS, StoredWindow, ZoneWeek
from apps.climate.domain.modes import ZoneKind
from apps.climate.domain.ports import ClimateConfigStore, ComfortWindowStore, ZoneStore
from apps.climate.domain.week_plan import DayPeriod, day_periods
from apps.climate.domain.zone import ZoneSettings
from apps.climate.repositories.climate_settings import ClimateSettingsRepository
from apps.climate.repositories.comfort_period import ComfortPeriodRepository
from apps.climate.repositories.zone import ZoneRepository
from apps.core.domain.usecases.base import BaseUseCase


@dataclass(frozen=True, kw_only=True)
class GetSchedulePageInputDTO:
    pass


@dataclass(frozen=True, kw_only=True)
class ScheduleDay:
    weekday: int
    windows: tuple[StoredWindow, ...]
    periods: tuple[DayPeriod, ...]


@dataclass(frozen=True, kw_only=True)
class ZoneSchedule:
    slug: str
    name: str
    preheat_lead: timedelta
    optimal_start: bool
    days: tuple[ScheduleDay, ...]


@dataclass(frozen=True, kw_only=True)
class GetSchedulePageOutputDTO:
    zones: tuple[ZoneSchedule, ...]
    lead_cap: timedelta
    mornings_needed: int

    def zone(self, slug: str) -> ZoneSchedule | None:
        return next((zone for zone in self.zones if zone.slug == slug), None)

    def zone_of_window(self, window_id: int) -> ZoneSchedule | None:
        return next(
            (zone for zone in self.zones for day in zone.days for window in day.windows if window.id == window_id),
            None,
        )


class GetSchedulePageUseCase(BaseUseCase[GetSchedulePageInputDTO, GetSchedulePageOutputDTO]):
    def __init__(self, zones: ZoneStore, windows: ComfortWindowStore, climate_settings: ClimateConfigStore) -> None:
        super().__init__()
        self._zones = zones
        self._windows = windows
        self._climate_settings = climate_settings

    @classmethod
    def init(cls) -> 'GetSchedulePageUseCase':
        return cls(
            zones=ZoneRepository(),
            windows=ComfortPeriodRepository(),
            climate_settings=ClimateSettingsRepository(),
        )

    def _execute(self, input_dto: GetSchedulePageInputDTO) -> GetSchedulePageOutputDTO:  # noqa: ARG002
        zones = {zone.slug: zone for zone in self._zones.all_settings()}
        climate = self._climate_settings.config_record().config
        schedules = [_schedule(week, zones[week.slug]) for week in self._windows.weeks() if week.slug in zones]
        return GetSchedulePageOutputDTO(
            zones=tuple(sorted(schedules, key=lambda schedule: schedule.name)),
            lead_cap=timedelta(minutes=climate.preheat_lead_cap_minutes),
            mornings_needed=climate.learned_mornings_needed,
        )


def _schedule(week: ZoneWeek, zone: ZoneSettings) -> ZoneSchedule:
    windows = tuple(stored.window for stored in week.windows)
    return ZoneSchedule(
        slug=week.slug,
        name=week.name,
        preheat_lead=zone.preheat_lead,
        optimal_start=zone.kind is ZoneKind.AIR and zone.air_sensor_slug is not None and zone.air_control_live,
        days=tuple(
            ScheduleDay(
                weekday=weekday,
                windows=tuple(
                    sorted(
                        (stored for stored in week.windows if stored.weekday == weekday),
                        key=lambda stored: (stored.start, stored.end),
                    ),
                ),
                periods=day_periods(windows, weekday, zone.preheat_lead),
            )
            for weekday in WEEKDAYS
        ),
    )
