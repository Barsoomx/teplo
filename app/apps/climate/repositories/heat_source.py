from datetime import datetime, timedelta
from decimal import Decimal

from django.db.models import QuerySet

from apps.climate.domain.errors import HeatSourceNotFoundError
from apps.climate.domain.heat_sources import COMMANDED_KINDS, FireplaceRecord, HeatSourceKind, SourceSettings
from apps.climate.models import HeatSource
from apps.core.repositories.write_lock import lock_for_update
from apps.power.models import Load, LoadSettings


class HeatSourceRepository:
    @staticmethod
    def fireplaces() -> list[FireplaceRecord]:
        return [_to_fireplace(source) for source in _fireplaces().select_related('room')]

    @staticmethod
    def fireplaces_for_update(room: str | None) -> list[FireplaceRecord]:
        rows = _fireplaces() if room is None else _fireplaces().filter(room__slug=room)
        lock_for_update(rows)
        return [_to_fireplace(source) for source in rows.select_related('room')]

    @staticmethod
    def save_lit_until(slug: str, until: datetime | None) -> None:
        HeatSource.objects.filter(slug=slug).update(lit_until=until)

    @staticmethod
    def commanded_settings() -> list[SourceSettings]:
        sources = list(
            HeatSource.objects.filter(kind__in=[kind.value for kind in COMMANDED_KINDS], device__isnull=False)
            .select_related('room', 'device')
            .order_by('room__floor__level', 'room__position', 'room__name', 'priority'),
        )
        loads = {load.heat_source_id: load for load in Load.objects.filter(heat_source__in=sources)}
        load_settings = LoadSettings.objects.order_by('pk').first()
        load_management_live = load_settings is not None and load_settings.load_management_live
        return [
            _to_settings(source, device.slug, loads.get(source.pk), load_management_live=load_management_live)
            for source in sources
            if (device := source.device) is not None
        ]

    @staticmethod
    def mark_commanded(slug: str, at: datetime, *, power: bool, setpoint: Decimal | None) -> None:
        commanded = {} if setpoint is None else {'commanded_setpoint': setpoint}
        HeatSource.objects.filter(slug=slug).update(last_command_at=at, commanded_power=power, **commanded)

    @staticmethod
    def mark_dry_run(slug: str, at: datetime) -> None:
        HeatSource.objects.filter(slug=slug).update(last_dry_run_at=at)

    @staticmethod
    def mark_shed(slug: str, at: datetime) -> None:
        HeatSource.objects.filter(slug=slug).update(last_command_at=at, commanded_power=False, manual_until=None)

    @staticmethod
    def hold_by_hand(slug: str, until: datetime, *, stamped_at: datetime | None) -> bool:
        if HeatSource.objects.filter(slug=slug, last_command_at=stamped_at).update(manual_until=until) == 1:
            return True

        if not HeatSource.objects.filter(slug=slug).exists():
            raise HeatSourceNotFoundError(slug)

        return False


def _fireplaces() -> QuerySet[HeatSource]:
    return HeatSource.objects.filter(kind=HeatSourceKind.FIREPLACE.value)


def _to_fireplace(source: HeatSource) -> FireplaceRecord:
    return FireplaceRecord(
        slug=source.slug,
        name=source.name,
        room_slug=source.room.slug,
        room_name=source.room.name,
        live=source.live,
        lit_until=source.lit_until,
        priority=source.priority,
    )


def _to_settings(
    source: HeatSource,
    device_slug: str,
    load: Load | None,
    *,
    load_management_live: bool,
) -> SourceSettings:
    return SourceSettings(
        slug=source.slug,
        name=source.name,
        room_slug=source.room.slug,
        room_name=source.room.name,
        kind=HeatSourceKind(source.kind),
        device_slug=device_slug,
        priority=source.priority,
        live=source.live,
        setpoint_bias=source.setpoint_bias,
        max_setpoint=source.max_setpoint,
        min_outdoor=source.min_outdoor,
        write_interval=timedelta(minutes=source.min_write_minutes),
        rated_watts=source.rated_watts,
        manual_until=source.manual_until,
        last_command_at=source.last_command_at,
        last_dry_run_at=source.last_dry_run_at,
        commanded_power=source.commanded_power,
        commanded_setpoint=source.commanded_setpoint,
        load=None if load is None else load.slug,
        load_live=load is not None and load.live,
        load_management_live=load_management_live,
    )
