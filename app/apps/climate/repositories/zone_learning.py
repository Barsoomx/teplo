from decimal import Decimal

from django.db.models import F
from django.utils import timezone

from apps.climate.domain.errors import ZoneNotFoundError
from apps.climate.domain.learning import HeatUpModel, ZoneLearningEntry, ZoneLearningRecord
from apps.climate.models import Zone, ZoneLearning
from apps.core.repositories.write_lock import lock_for_update


class ZoneLearningRepository:
    @staticmethod
    def all_records() -> dict[str, ZoneLearningRecord]:
        return {zone.slug: _to_record(zone) for zone in Zone.objects.select_related('learning').order_by('slug')}

    @staticmethod
    def save_early_stop_offset(slug: str, offset: Decimal, *, resets: int) -> bool:
        return _save(slug, {'early_stop_offset': offset}, resets=resets)

    @staticmethod
    def save_heat_up(slug: str, heat_up: HeatUpModel, *, resets: int) -> bool:
        return _save(
            slug,
            {
                'dead_time': heat_up.dead_time,
                'heat_up_rates': {str(bucket): str(rate) for bucket, rate in sorted(heat_up.rates.items())},
                'mornings': heat_up.mornings,
            },
            resets=resets,
        )

    @staticmethod
    def record_for_update(slug: str) -> ZoneLearningEntry | None:
        if lock_for_update(Zone.objects.filter(slug=slug)) == 0:
            raise ZoneNotFoundError(slug)

        learning = ZoneLearning.objects.select_related('zone').filter(zone__slug=slug).first()
        if learning is None:
            return None

        return ZoneLearningEntry(zone=learning.zone.slug, name=learning.zone.name, record=_learning_record(learning))

    @staticmethod
    def reset(slug: str) -> None:
        ZoneLearning.objects.filter(zone__slug=slug).update(
            early_stop_offset=None,
            dead_time=None,
            heat_up_rates={},
            mornings=0,
            resets=F('resets') + 1,
            updated_at=timezone.now(),
        )


def _save(slug: str, values: dict[str, object], *, resets: int) -> bool:
    if ZoneLearning.objects.filter(zone__slug=slug, resets=resets).update(**values, updated_at=timezone.now()) > 0:
        return True

    zone = _zone(slug)
    if resets != 0 or ZoneLearning.objects.filter(zone=zone).exists():
        return False

    ZoneLearning.objects.create(zone=zone, **values)
    return True


def _zone(slug: str) -> Zone:
    zone = Zone.objects.filter(slug=slug).first()
    if zone is None:
        raise ZoneNotFoundError(slug)

    return zone


def _to_record(zone: Zone) -> ZoneLearningRecord:
    try:
        learning = zone.learning
    except ZoneLearning.DoesNotExist:
        return ZoneLearningRecord.unlearned()

    return _learning_record(learning)


def _learning_record(learning: ZoneLearning) -> ZoneLearningRecord:
    return ZoneLearningRecord(
        early_stop_offset=learning.early_stop_offset,
        heat_up=HeatUpModel(
            dead_time=learning.dead_time,
            rates={int(bucket): Decimal(rate) for bucket, rate in learning.heat_up_rates.items()},
            mornings=learning.mornings,
        ),
        resets=learning.resets,
    )
