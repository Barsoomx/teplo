from datetime import datetime, timedelta

from apps.climate.domain.errors import ZoneNotFoundError
from apps.climate.domain.modes import ZoneKind
from apps.climate.domain.zone import ComfortWindow, FloorSetpoints, ZoneSettings
from apps.climate.models import Zone


class ZoneRepository:
    @staticmethod
    def all_settings() -> list[ZoneSettings]:
        zones = Zone.objects.select_related('thermostat').prefetch_related('comfort_periods').order_by('slug')
        return [_to_settings(zone) for zone in zones]

    @staticmethod
    def mark_commanded(slug: str, at: datetime) -> None:
        Zone.objects.filter(slug=slug).update(last_command_at=at)

    @staticmethod
    def boost(slug: str, until: datetime) -> None:
        if Zone.objects.filter(slug=slug).update(boost_until=until, last_command_at=None) == 0:
            raise ZoneNotFoundError(slug)


def _to_settings(zone: Zone) -> ZoneSettings:
    return ZoneSettings(
        slug=zone.slug,
        name=zone.name,
        kind=ZoneKind(zone.kind),
        thermostat_slug=zone.thermostat.slug,
        heating_enabled=zone.heating_enabled,
        live_control=zone.live_control,
        setpoints=FloorSetpoints(
            comfort=zone.comfort_floor_setpoint,
            night=zone.night_floor_setpoint,
            boost=zone.boost_floor_setpoint,
        ),
        preheat_lead=timedelta(minutes=zone.preheat_lead_minutes),
        windows=tuple(
            ComfortWindow(weekday=period.weekday, start=period.start, end=period.end)
            for period in zone.comfort_periods.all()
        ),
        boost_until=zone.boost_until,
        last_command_at=zone.last_command_at,
    )
