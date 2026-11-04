from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.climate.domain.errors import ZoneNotFoundError
from apps.climate.domain.learning import HeatUpModel, ZoneLearningEntry, ZoneLearningRecord
from apps.climate.factories import ZoneFactory, ZoneLearningFactory
from apps.climate.models import Zone, ZoneLearning
from apps.climate.repositories.zone_learning import ZoneLearningRepository

pytestmark = pytest.mark.django_db

HEAT_UP = HeatUpModel(
    dead_time=timedelta(minutes=46),
    rates={-10: Decimal('0.550'), -5: Decimal('0.690'), 0: Decimal('1.200')},
    mornings=4,
)
LEARNED = ZoneLearningRecord(
    early_stop_offset=Decimal('0.42'),
    heat_up=HeatUpModel(dead_time=timedelta(minutes=40), rates={-5: Decimal('0.771')}, mornings=1),
)
RESET_ONCE = ZoneLearningRecord(
    early_stop_offset=None,
    heat_up=HeatUpModel(dead_time=None, rates={}, mornings=0),
    resets=1,
)


@pytest.fixture
def f_zone() -> Zone:
    return ZoneFactory.create(slug='kitchen')


@pytest.fixture
def f_learned_zone() -> Zone:
    return ZoneLearningFactory.create(
        zone__slug='living-room',
        zone__name='Гостиная',
        early_stop_offset=Decimal('0.42'),
        dead_time=timedelta(minutes=40),
        heat_up_rates={'-5': '0.771'},
        mornings=1,
    ).zone


@pytest.fixture
def f_reset_zone(f_learned_zone: Zone) -> Zone:
    ZoneLearningRepository.reset(f_learned_zone.slug)
    return f_learned_zone


def test_all_records_maps_learning_and_leaves_unlearned_zone_empty(f_zone: Zone, f_learned_zone: Zone) -> None:
    assert ZoneLearningRepository.all_records() == {
        f_zone.slug: ZoneLearningRecord.unlearned(),
        f_learned_zone.slug: LEARNED,
    }


def test_save_early_stop_offset_starts_learning_of_unlearned_zone(f_zone: Zone) -> None:
    saved = ZoneLearningRepository.save_early_stop_offset(f_zone.slug, Decimal('0.42'), resets=0)

    assert (saved, ZoneLearningRepository.all_records()[f_zone.slug]) == (
        True,
        ZoneLearningRecord(
            early_stop_offset=Decimal('0.42'),
            heat_up=HeatUpModel(dead_time=None, rates={}, mornings=0),
        ),
    )


def test_save_early_stop_offset_keeps_heat_up(f_learned_zone: Zone) -> None:
    saved = ZoneLearningRepository.save_early_stop_offset(f_learned_zone.slug, Decimal('0.39'), resets=0)

    assert (saved, ZoneLearningRepository.all_records()[f_learned_zone.slug]) == (
        True,
        ZoneLearningRecord(early_stop_offset=Decimal('0.39'), heat_up=LEARNED.heat_up),
    )


def test_save_heat_up_keeps_early_stop_offset(f_learned_zone: Zone) -> None:
    saved = ZoneLearningRepository.save_heat_up(f_learned_zone.slug, HEAT_UP, resets=0)

    assert (saved, ZoneLearningRepository.all_records()[f_learned_zone.slug]) == (
        True,
        ZoneLearningRecord(early_stop_offset=Decimal('0.42'), heat_up=HEAT_UP),
    )


def test_save_heat_up_stores_rates_by_bucket_from_coldest(f_zone: Zone) -> None:
    ZoneLearningRepository.save_heat_up(f_zone.slug, HEAT_UP, resets=0)

    assert list(ZoneLearning.objects.get(zone=f_zone).heat_up_rates.items()) == [
        ('-10', '0.550'),
        ('-5', '0.690'),
        ('0', '1.200'),
    ]


def test_save_early_stop_offset_of_unknown_zone_raises() -> None:
    with pytest.raises(ZoneNotFoundError):
        ZoneLearningRepository.save_early_stop_offset('attic', Decimal('0.42'), resets=0)


def test_save_heat_up_of_unknown_zone_raises() -> None:
    with pytest.raises(ZoneNotFoundError):
        ZoneLearningRepository.save_heat_up('attic', HEAT_UP, resets=0)


def test_reset_clears_what_the_zone_learned_and_counts_the_reset(f_learned_zone: Zone) -> None:
    ZoneLearningRepository.reset(f_learned_zone.slug)

    assert ZoneLearningRepository.all_records()[f_learned_zone.slug] == RESET_ONCE


def test_saves_of_learning_read_before_a_reset_store_nothing(f_reset_zone: Zone) -> None:
    saves = (
        ZoneLearningRepository.save_heat_up(f_reset_zone.slug, HEAT_UP, resets=0),
        ZoneLearningRepository.save_early_stop_offset(f_reset_zone.slug, Decimal('0.39'), resets=0),
    )

    assert (saves, ZoneLearningRepository.all_records()[f_reset_zone.slug]) == ((False, False), RESET_ONCE)


def test_saves_of_learning_read_after_a_reset_store_it(f_reset_zone: Zone) -> None:
    saves = (
        ZoneLearningRepository.save_heat_up(f_reset_zone.slug, HEAT_UP, resets=1),
        ZoneLearningRepository.save_early_stop_offset(f_reset_zone.slug, Decimal('0.39'), resets=1),
    )

    assert (saves, ZoneLearningRepository.all_records()[f_reset_zone.slug]) == (
        (True, True),
        ZoneLearningRecord(early_stop_offset=Decimal('0.39'), heat_up=HEAT_UP, resets=1),
    )


def test_record_for_update_locks_before_it_reads_and_names_the_zone(f_learned_zone: Zone) -> None:
    with CaptureQueriesContext(connection) as queries:
        entry = ZoneLearningRepository.record_for_update(f_learned_zone.slug)

    assert (entry, queries.captured_queries[0]['sql'].split(' ', 1)[0]) == (
        ZoneLearningEntry(zone='living-room', name='Гостиная', record=LEARNED),
        'UPDATE',
    )


def test_record_for_update_of_a_zone_that_learned_nothing_is_none(f_zone: Zone) -> None:
    assert ZoneLearningRepository.record_for_update(f_zone.slug) is None


def test_record_for_update_of_unknown_zone_raises() -> None:
    with pytest.raises(ZoneNotFoundError):
        ZoneLearningRepository.record_for_update('attic')
