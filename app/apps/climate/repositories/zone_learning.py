from decimal import Decimal

from apps.climate.domain.errors import ZoneNotFoundError
from apps.climate.domain.learning import HeatUpModel, ZoneLearningRecord
from apps.climate.models import Zone, ZoneLearning


class ZoneLearningRepository:
    @staticmethod
    def all_records() -> dict[str, ZoneLearningRecord]:
        return {zone.slug: _to_record(zone) for zone in Zone.objects.select_related('learning').order_by('slug')}

    @staticmethod
    def save_early_stop_offset(slug: str, offset: Decimal) -> None:
        ZoneLearning.objects.update_or_create(zone=_zone(slug), defaults={'early_stop_offset': offset})

    @staticmethod
    def save_heat_up(slug: str, heat_up: HeatUpModel) -> None:
        ZoneLearning.objects.update_or_create(
            zone=_zone(slug),
            defaults={
                'dead_time': heat_up.dead_time,
                'heat_up_rates': {str(bucket): str(rate) for bucket, rate in sorted(heat_up.rates.items())},
                'mornings': heat_up.mornings,
            },
        )


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

    return ZoneLearningRecord(
        early_stop_offset=learning.early_stop_offset,
        heat_up=HeatUpModel(
            dead_time=learning.dead_time,
            rates={int(bucket): Decimal(rate) for bucket, rate in learning.heat_up_rates.items()},
            mornings=learning.mornings,
        ),
    )
