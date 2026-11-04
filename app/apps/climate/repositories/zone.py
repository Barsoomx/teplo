from collections.abc import Collection
from dataclasses import asdict
from datetime import datetime, timedelta

from apps.climate.domain.errors import ZoneNotFoundError
from apps.climate.domain.modes import ZoneKind
from apps.climate.domain.zone import AirTargets, ComfortWindow, FloorSetpoints, ZoneSettings
from apps.climate.domain.zone_config import ZoneConfig, ZoneConfigRecord
from apps.climate.models import ComfortPeriod, RoomClimate, Zone
from apps.core.repositories.write_lock import lock_for_update

STAMPS = ('last_command_at', 'last_dry_run_at', 'last_air_dry_run_at')


class ZoneRepository:
    @staticmethod
    def all_settings() -> list[ZoneSettings]:
        zones = (
            Zone.objects.select_related('thermostat', 'air_sensor', 'room__climate')
            .prefetch_related('comfort_periods', 'room__climate__comfort_periods')
            .order_by('slug')
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
    def mark_shed(slug: str, at: datetime) -> None:
        Zone.objects.filter(slug=slug).update(last_command_at=at, manual_until=None)

    @staticmethod
    def boost(slug: str, until: datetime, *, clear_stamps: bool) -> None:
        cleared = dict.fromkeys(STAMPS) if clear_stamps else {}
        updated = Zone.objects.filter(slug=slug).update(boost_until=until, manual_until=None, **cleared)
        if updated == 0:
            raise ZoneNotFoundError(slug)

    @staticmethod
    def hold_by_hand(slug: str, until: datetime, *, stamped_at: datetime | None) -> bool:
        zones = Zone.objects.filter(slug=slug, last_command_at=stamped_at).exclude(
            load__shed_at__isnull=False,
            load__shed_handed_back=False,
        )
        if zones.update(manual_until=until) == 1:
            return True

        if not Zone.objects.filter(slug=slug).exists():
            raise ZoneNotFoundError(slug)

        return False

    @staticmethod
    def config_records() -> list[ZoneConfigRecord]:
        return [
            ZoneConfigRecord(
                id=zone.pk,
                slug=zone.slug,
                kind=ZoneKind(zone.kind),
                config=_to_config(zone),
                live_control=zone.live_control,
                air_control_live=zone.air_control_live,
                room=_comfort_room(zone),
            )
            for zone in Zone.objects.select_related('room__climate').order_by('slug')
        ]

    @staticmethod
    def config_for_update(slug: str) -> ZoneConfig:
        if lock_for_update(Zone.objects.filter(slug=slug)) == 0:
            raise ZoneNotFoundError(slug)

        return _to_config(Zone.objects.get(slug=slug))

    @staticmethod
    def save_config(slug: str, config: ZoneConfig, fields: Collection[str]) -> None:
        values = asdict(config)
        if not fields:
            return

        updated = Zone.objects.filter(slug=slug).update(**{field: values[field] for field in fields})
        if updated == 0:
            raise ZoneNotFoundError(slug)


def _to_config(zone: Zone) -> ZoneConfig:
    return ZoneConfig(
        name=zone.name,
        heating_enabled=zone.heating_enabled,
        comfort_floor_setpoint=zone.comfort_floor_setpoint,
        night_floor_setpoint=zone.night_floor_setpoint,
        boost_floor_setpoint=zone.boost_floor_setpoint,
        idle_floor_setpoint=zone.idle_floor_setpoint,
        floor_limit_setpoint=zone.floor_limit_setpoint,
        comfort_air_target=zone.comfort_air_target,
        night_air_target=zone.night_air_target,
        preheat_lead_minutes=zone.preheat_lead_minutes,
    )


def _room_climate(zone: Zone) -> RoomClimate | None:
    if zone.room is None:
        return None

    try:
        return zone.room.climate
    except RoomClimate.DoesNotExist:
        return None


def _comfort_room(zone: Zone) -> str | None:
    climate = _room_climate(zone)
    return None if climate is None else climate.room.slug


def _air_targets(zone: Zone, climate: RoomClimate | None) -> AirTargets:
    if climate is None:
        return AirTargets(comfort=zone.comfort_air_target, night=zone.night_air_target)

    return AirTargets(comfort=climate.comfort_target, night=climate.setback_target)


def _periods(zone: Zone, climate: RoomClimate | None) -> list[ComfortPeriod]:
    return list(zone.comfort_periods.all() if climate is None else climate.comfort_periods.all())


def _to_settings(zone: Zone) -> ZoneSettings:
    climate = _room_climate(zone)
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
        air_targets=_air_targets(zone, climate),
        preheat_lead=timedelta(minutes=zone.preheat_lead_minutes),
        windows=tuple(
            ComfortWindow(weekday=period.weekday, start=period.start, end=period.end)
            for period in _periods(zone, climate)
        ),
        boost_until=zone.boost_until,
        last_command_at=zone.last_command_at,
        last_dry_run_at=zone.last_dry_run_at,
        last_air_dry_run_at=zone.last_air_dry_run_at,
        room_slug=None if zone.room is None else zone.room.slug,
        manual_until=zone.manual_until,
    )
