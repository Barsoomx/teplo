from datetime import datetime, timedelta

from apps.climate.domain.errors import ZoneNotFoundError
from apps.climate.domain.modes import ZoneKind
from apps.climate.domain.zone import AirTargets, ComfortWindow, FloorSetpoints, ZoneSettings
from apps.climate.models import Zone

STAMPS = ('last_command_at', 'last_dry_run_at', 'last_air_dry_run_at')


class ZoneRepository:
    @staticmethod
    def all_settings() -> list[ZoneSettings]:
        zones = (
            Zone.objects.select_related('thermostat', 'air_sensor').prefetch_related('comfort_periods').order_by('slug')
        )
        return [_to_settings(zone) for zone in zones]

    @staticmethod
    def mark_commanded(slug: str, at: datetime) -> None:
        Zone.objects.filter(slug=slug).update(last_command_at=at)

    @staticmethod
    def mark_dry_run(slug: str, at: datetime) -> None:
        Zone.objects.filter(slug=slug).update(last_dry_run_at=at)

    @staticmethod
    def mark_air_dry_run(slug: str, at: datetime) -> None:
        Zone.objects.filter(slug=slug).update(last_air_dry_run_at=at)

    @staticmethod
    def boost(slug: str, until: datetime, *, clear_stamps: bool) -> None:
        cleared = dict.fromkeys(STAMPS) if clear_stamps else {}
        updated = Zone.objects.filter(slug=slug).update(boost_until=until, **cleared)
        if updated == 0:
            raise ZoneNotFoundError(slug)


def _to_settings(zone: Zone) -> ZoneSettings:
    return ZoneSettings(
        slug=zone.slug,
        name=zone.name,
        kind=ZoneKind(zone.kind),
        thermostat_slug=zone.thermostat.slug,
        air_sensor_slug=None if zone.air_sensor is None else zone.air_sensor.slug,
        heating_enabled=zone.heating_enabled,
        live_control=zone.live_control,
        air_control_live=zone.air_control_live,
        setpoints=FloorSetpoints(
            comfort=zone.comfort_floor_setpoint,
            night=zone.night_floor_setpoint,
            boost=zone.boost_floor_setpoint,
            idle=zone.idle_floor_setpoint,
            limit=zone.floor_limit_setpoint,
        ),
        air_targets=AirTargets(comfort=zone.comfort_air_target, night=zone.night_air_target),
        preheat_lead=timedelta(minutes=zone.preheat_lead_minutes),
        windows=tuple(
            ComfortWindow(weekday=period.weekday, start=period.start, end=period.end)
            for period in zone.comfort_periods.all()
        ),
        boost_until=zone.boost_until,
        last_command_at=zone.last_command_at,
        last_dry_run_at=zone.last_dry_run_at,
        last_air_dry_run_at=zone.last_air_dry_run_at,
    )
