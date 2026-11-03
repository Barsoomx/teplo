from collections.abc import Collection
from dataclasses import asdict

from django.db.models import Exists, OuterRef, QuerySet

from apps.climate.domain.backup_heat import BackupHeat
from apps.climate.domain.modes import CommandSource, ZoneKind
from apps.climate.domain.room_climate import (
    RoomClimateConfig,
    RoomClimateConfigRecord,
    RoomClimateSettings,
    RoomOverride,
)
from apps.climate.domain.zone import AirTargets, ComfortWindow
from apps.climate.models import RoomClimate, Zone
from apps.core.repositories.write_lock import lock_for_update
from apps.house.domain.errors import RoomNotFoundError

NO_OVERRIDE = {'override_target': None, 'override_until': None, 'override_source': ''}


class RoomClimateRepository:
    @staticmethod
    def all_settings() -> list[RoomClimateSettings]:
        climates = _settings_climates().annotate(adjustable=_reads_the_air())
        return [_to_settings(climate, adjustable=climate.adjustable) for climate in climates]

    @staticmethod
    def comfort_for_update(slug: str) -> RoomClimateSettings:
        if lock_for_update(RoomClimate.objects.filter(room__slug=slug)) == 0:
            raise RoomNotFoundError(slug)

        climate = _settings_climates().annotate(adjustable=_reads_the_air()).get(room__slug=slug)
        return _to_settings(climate, adjustable=climate.adjustable)

    @staticmethod
    def save_override(slug: str, override: RoomOverride | None) -> None:
        values = (
            NO_OVERRIDE
            if override is None
            else {
                'override_target': override.target,
                'override_until': override.until,
                'override_source': override.source.value,
            }
        )
        if RoomClimate.objects.filter(room__slug=slug).update(**values) == 0:
            raise RoomNotFoundError(slug)

    @staticmethod
    def config_records() -> list[RoomClimateConfigRecord]:
        return [_to_record(climate) for climate in _climates()]

    @staticmethod
    def config_for_update(slug: str) -> RoomClimateConfigRecord:
        if lock_for_update(RoomClimate.objects.filter(room__slug=slug)) == 0:
            raise RoomNotFoundError(slug)

        return _to_record(_climates().get(room__slug=slug))

    @staticmethod
    def save_config(slug: str, config: RoomClimateConfig, fields: Collection[str]) -> None:
        values = asdict(config)
        if not fields:
            return

        updated = RoomClimate.objects.filter(room__slug=slug).update(**{field: values[field] for field in fields})
        if updated == 0:
            raise RoomNotFoundError(slug)


def _climates() -> QuerySet[RoomClimate]:
    return RoomClimate.objects.select_related('room__floor', 'air_sensor', 'backup_zone')


def _settings_climates() -> QuerySet[RoomClimate]:
    return _climates().prefetch_related('comfort_periods')


def _reads_the_air() -> Exists:
    return Exists(Zone.objects.filter(room=OuterRef('room'), kind=ZoneKind.AIR, air_sensor__isnull=False))


def _to_settings(climate: RoomClimate, *, adjustable: bool) -> RoomClimateSettings:
    return RoomClimateSettings(
        slug=climate.room.slug,
        name=climate.room.name,
        air_sensor_slug=None if climate.air_sensor is None else climate.air_sensor.slug,
        targets=AirTargets(comfort=climate.comfort_target, night=climate.setback_target),
        windows=tuple(
            ComfortWindow(weekday=period.weekday, start=period.start, end=period.end)
            for period in climate.comfort_periods.all()
        ),
        backup=BackupHeat(
            zone_slug=None if climate.backup_zone is None else climate.backup_zone.slug,
            below=climate.backup_below,
            hysteresis=climate.backup_hysteresis,
            live=climate.backup_live,
        ),
        guest_room=climate.guest_room,
        override=_override(climate),
        adjustable=adjustable,
    )


def _override(climate: RoomClimate) -> RoomOverride | None:
    if climate.override_target is None or climate.override_until is None:
        return None

    source = climate.override_source
    return RoomOverride(
        target=climate.override_target,
        until=climate.override_until,
        source=CommandSource(source) if source in CommandSource else CommandSource.PANEL,
    )


def _to_record(climate: RoomClimate) -> RoomClimateConfigRecord:
    return RoomClimateConfigRecord(
        id=climate.pk,
        slug=climate.room.slug,
        name=climate.room.name,
        floor_name=climate.room.floor.name,
        config=RoomClimateConfig(
            comfort_target=climate.comfort_target,
            setback_target=climate.setback_target,
            backup_below=climate.backup_below,
            backup_hysteresis=climate.backup_hysteresis,
            guest_room=climate.guest_room,
        ),
        air_sensor_slug=None if climate.air_sensor is None else climate.air_sensor.slug,
        backup_zone_slug=None if climate.backup_zone is None else climate.backup_zone.slug,
        backup_zone_name=None if climate.backup_zone is None else climate.backup_zone.name,
        backup_live=climate.backup_live,
    )
