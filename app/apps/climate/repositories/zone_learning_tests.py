from datetime import timedelta
from decimal import Decimal

import pytest

from apps.climate.domain.errors import ZoneNotFoundError
from apps.climate.domain.learning import HeatUpModel, ZoneLearningRecord
from apps.climate.factories import ZoneFactory, ZoneLearningFactory
from apps.climate.models import Zone, ZoneLearning
from apps.climate.repositories.zone_learning import ZoneLearningRepository

pytestmark = pytest.mark.django_db

HEAT_UP = HeatUpModel(
    dead_time=timedelta(minutes=46),
    rates={-10: Decimal('0.550'), -5: Decimal('0.690'), 0: Decimal('1.200')},
    mornings=4,
)


@pytest.fixture
def f_zone() -> Zone:
    return ZoneFactory.create(slug='kitchen')


@pytest.fixture
def f_learned_zone() -> Zone:
    return ZoneLearningFactory.create(
        zone__slug='living-room',
        early_stop_offset=Decimal('0.42'),
        dead_time=timedelta(minutes=40),
        heat_up_rates={'-5': '0.771'},
        mornings=1,
    ).zone


def test_all_records_maps_learning_and_leaves_unlearned_zone_empty(f_zone: Zone, f_learned_zone: Zone) -> None:
    assert ZoneLearningRepository.all_records() == {
        f_zone.slug: ZoneLearningRecord.unlearned(),
        f_learned_zone.slug: ZoneLearningRecord(
            early_stop_offset=Decimal('0.42'),
            heat_up=HeatUpModel(dead_time=timedelta(minutes=40), rates={-5: Decimal('0.771')}, mornings=1),
        ),
    }


def test_save_early_stop_offset_starts_learning_of_unlearned_zone(f_zone: Zone) -> None:
    ZoneLearningRepository.save_early_stop_offset(f_zone.slug, Decimal('0.42'))

    assert ZoneLearningRepository.all_records()[f_zone.slug] == ZoneLearningRecord(
        early_stop_offset=Decimal('0.42'),
        heat_up=HeatUpModel(dead_time=None, rates={}, mornings=0),
    )


def test_save_early_stop_offset_keeps_heat_up(f_learned_zone: Zone) -> None:
    ZoneLearningRepository.save_early_stop_offset(f_learned_zone.slug, Decimal('0.39'))

    assert ZoneLearningRepository.all_records()[f_learned_zone.slug] == ZoneLearningRecord(
        early_stop_offset=Decimal('0.39'),
        heat_up=HeatUpModel(dead_time=timedelta(minutes=40), rates={-5: Decimal('0.771')}, mornings=1),
    )


def test_save_heat_up_keeps_early_stop_offset(f_learned_zone: Zone) -> None:
    ZoneLearningRepository.save_heat_up(f_learned_zone.slug, HEAT_UP)

    assert ZoneLearningRepository.all_records()[f_learned_zone.slug] == ZoneLearningRecord(
        early_stop_offset=Decimal('0.42'),
        heat_up=HEAT_UP,
    )


def test_save_heat_up_stores_rates_by_bucket_from_coldest(f_zone: Zone) -> None:
    ZoneLearningRepository.save_heat_up(f_zone.slug, HEAT_UP)

    assert list(ZoneLearning.objects.get(zone=f_zone).heat_up_rates.items()) == [
        ('-10', '0.550'),
        ('-5', '0.690'),
        ('0', '1.200'),
    ]


def test_save_early_stop_offset_of_unknown_zone_raises() -> None:
    with pytest.raises(ZoneNotFoundError):
        ZoneLearningRepository.save_early_stop_offset('attic', Decimal('0.42'))


def test_save_heat_up_of_unknown_zone_raises() -> None:
    with pytest.raises(ZoneNotFoundError):
        ZoneLearningRepository.save_heat_up('attic', HEAT_UP)
