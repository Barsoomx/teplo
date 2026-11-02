from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from django.db import IntegrityError

from apps.climate.factories import ZoneFactory
from apps.climate.models import Zone
from apps.power.domain.loads import LearnedPhase, LiveShed, LoadKind, LoadPhase, LoadRecord, ShedReason
from apps.power.factories import PlugLoadFactory, ZoneLoadFactory
from apps.power.models import Load
from apps.power.repositories.load import LoadRepository

pytestmark = pytest.mark.django_db

SHED_AT = datetime(2026, 11, 2, 18, 40, tzinfo=UTC)
OVERLOAD_SHED = LiveShed(at=SHED_AT, reason=ShedReason.OVERLOAD, phase=LoadPhase.L2, was_on=True)
LEARNED = LearnedPhase(phase=LoadPhase.L3, amps=Decimal('8.4'), samples=4)
BOILER = LoadRecord(
    slug='boiler-1f',
    name='Бойлер 1 этаж',
    kind=LoadKind.PLUG,
    zone_slug=None,
    device_slug='plug-boiler-1f',
    priority=1,
    phase=LoadPhase.UNKNOWN,
    nominal_amps=Decimal('6.0'),
    learned=None,
    live=True,
    shed=None,
)
KITCHEN = LoadRecord(
    slug='kitchen',
    name='Кухня',
    kind=LoadKind.ZONE,
    zone_slug='kitchen',
    device_slug='thermostat-kitchen',
    priority=5,
    phase=LoadPhase.L2,
    nominal_amps=None,
    learned=None,
    live=False,
    shed=None,
)


@pytest.fixture
def f_kitchen() -> Load:
    return ZoneLoadFactory.create(
        slug='kitchen',
        name='Кухня',
        zone__slug='kitchen',
        zone__thermostat__slug='thermostat-kitchen',
        priority=5,
        phase=LoadPhase.L2,
    )


@pytest.fixture
def boiler() -> Load:
    return PlugLoadFactory.create(
        slug='boiler-1f',
        name='Бойлер 1 этаж',
        device__slug='plug-boiler-1f',
        priority=1,
        nominal_amps=Decimal('6.0'),
        live=True,
    )


@pytest.fixture
def kitchen_shed_while_the_meter_was_lost(f_kitchen: Load) -> Load:
    Load.objects.filter(pk=f_kitchen.pk).update(
        shed_at=SHED_AT,
        shed_reason=ShedReason.METER_LOST,
        shed_phase=LoadPhase.UNKNOWN,
    )
    return f_kitchen


@pytest.fixture
def kitchen_written_by_teplo(f_kitchen: Load) -> Load:
    Zone.objects.filter(slug='kitchen').update(last_command_at=SHED_AT)
    return f_kitchen


@pytest.fixture
def f_hallway_zone() -> Zone:
    return ZoneFactory.create(slug='hallway')


@pytest.fixture
def hallway_under_live_control() -> Load:
    return ZoneLoadFactory.create(slug='hallway', zone__slug='hallway', zone__live_control=True, priority=4)


@pytest.fixture
def zone_under_live_control_without_a_load() -> Zone:
    return ZoneFactory.create(slug='living-room', live_control=True)


@pytest.mark.usefixtures('f_kitchen', 'boiler')
def test_all_records_list_loads_by_priority_with_the_device_each_controls() -> None:
    assert LoadRepository.all_records() == [BOILER, KITCHEN]


@pytest.mark.usefixtures('f_kitchen', 'boiler', 'hallway_under_live_control', 'zone_under_live_control_without_a_load')
def test_live_zone_slugs_are_the_zones_of_loads_under_live_control() -> None:
    assert LoadRepository.live_zone_slugs() == frozenset({'hallway'})


@pytest.mark.usefixtures('kitchen_written_by_teplo')
def test_record_of_a_zone_carries_the_last_write_stamp_of_its_zone() -> None:
    assert LoadRepository.all_records() == [replace(KITCHEN, last_command_at=SHED_AT)]


@pytest.mark.usefixtures('boiler')
def test_marked_shed_is_read_back() -> None:
    LoadRepository.mark_shed('boiler-1f', OVERLOAD_SHED)

    assert LoadRepository.all_records() == [replace(BOILER, shed=OVERLOAD_SHED)]


@pytest.mark.usefixtures('kitchen_shed_while_the_meter_was_lost')
def test_stored_shed_of_a_zone_is_read_back_without_a_plug_state() -> None:
    assert LoadRepository.all_records() == [
        replace(KITCHEN, shed=LiveShed(at=SHED_AT, reason=ShedReason.METER_LOST, phase=LoadPhase.UNKNOWN, was_on=None)),
    ]


@pytest.mark.usefixtures('kitchen_shed_while_the_meter_was_lost')
def test_cleared_shed_leaves_no_trace() -> None:
    LoadRepository.clear_shed('kitchen')

    assert list(Load.objects.values_list('shed_at', 'shed_reason', 'shed_phase', 'shed_was_on')) == [
        (None, '', '', None),
    ]


@pytest.mark.usefixtures('f_kitchen')
def test_saved_learning_is_read_back_beside_the_phase_the_owner_set() -> None:
    LoadRepository.save_learned('kitchen', LEARNED)

    assert LoadRepository.all_records() == [replace(KITCHEN, learned=LEARNED)]


@pytest.mark.usefixtures('boiler')
def test_writes_for_a_load_that_is_gone_change_nothing() -> None:
    LoadRepository.mark_shed('attic', OVERLOAD_SHED)
    LoadRepository.save_learned('attic', LEARNED)

    assert LoadRepository.all_records() == [BOILER]


@pytest.mark.parametrize(
    'overrides',
    [{'device': None}, {'kind': LoadKind.ZONE}],
    ids=['plug_load_without_a_plug', 'zone_load_without_a_zone'],
)
def test_database_refuses_a_load_without_what_its_kind_controls(overrides: dict[str, object]) -> None:
    with pytest.raises(IntegrityError):
        PlugLoadFactory.create(**overrides)


def test_database_refuses_a_load_of_a_zone_and_a_plug_at_once(f_hallway_zone: Zone) -> None:
    with pytest.raises(IntegrityError):
        PlugLoadFactory.create(zone=f_hallway_zone)


@pytest.mark.usefixtures('boiler')
@pytest.mark.parametrize('priority', [1, 0], ids=['taken', 'zero'])
def test_database_refuses_a_priority_taken_or_below_one(priority: int) -> None:
    with pytest.raises(IntegrityError):
        PlugLoadFactory.create(priority=priority)


def test_database_refuses_a_second_load_of_one_zone(f_kitchen: Load) -> None:
    with pytest.raises(IntegrityError):
        ZoneLoadFactory.create(zone=f_kitchen.zone, priority=6)
