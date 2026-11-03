from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from django.db import IntegrityError

from apps.climate.factories import ZoneFactory
from apps.climate.models import Zone
from apps.power.domain.errors import LoadNotFoundError
from apps.power.domain.load_config import LoadConfig, LoadConfigRecord
from apps.power.domain.loads import LearnedPhase, LiveShed, LoadKind, LoadPhase, LoadRecord, ShedReason
from apps.power.factories import PlugLoadFactory, ZoneLoadFactory
from apps.power.models import Load
from apps.power.repositories.load import LoadRepository

pytestmark = pytest.mark.django_db

SHED_AT = datetime(2026, 11, 2, 18, 40, tzinfo=UTC)
OVERLOAD_SHED = LiveShed(
    at=SHED_AT,
    reason=ShedReason.OVERLOAD,
    phase=LoadPhase.L2,
    was_on=True,
    device='plug-boiler-1f',
)
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
        shed_device='thermostat-kitchen',
    )
    return f_kitchen


@pytest.fixture
def kitchen_handed_back_after_an_overload(f_kitchen: Load) -> Load:
    Load.objects.filter(pk=f_kitchen.pk).update(
        shed_at=SHED_AT,
        shed_reason=ShedReason.OVERLOAD,
        shed_phase=LoadPhase.L2,
        shed_device='thermostat-kitchen',
        shed_handed_back=True,
    )
    return f_kitchen


@pytest.fixture
def loads_shed_by_a_release_that_stored_no_device(f_kitchen: Load, boiler: Load) -> None:
    Load.objects.filter(pk=boiler.pk).update(
        shed_at=SHED_AT,
        shed_reason=ShedReason.OVERLOAD,
        shed_phase=LoadPhase.L2,
        shed_was_on=True,
    )
    Load.objects.filter(pk=f_kitchen.pk).update(
        shed_at=SHED_AT,
        shed_reason=ShedReason.OVERLOAD,
        shed_phase=LoadPhase.L2,
    )


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


@pytest.fixture
def f_loads() -> list[Load]:
    return [
        PlugLoadFactory.create(
            slug='boiler-1f',
            name='Бойлер 1 этаж',
            priority=1,
            nominal_amps=Decimal('6.0'),
            live=True,
        ),
        ZoneLoadFactory.create(slug='kitchen', name='Кухня', zone__slug='kitchen', priority=2, phase=LoadPhase.L2),
        ZoneLoadFactory.create(slug='hallway', name='Прихожая', zone__slug='hallway', priority=3),
    ]


@pytest.fixture
def f_boiler_shed_after_learning(f_loads: list[Load]) -> Load:
    Load.objects.filter(pk=f_loads[0].pk).update(
        shed_at=SHED_AT,
        shed_reason=ShedReason.OVERLOAD,
        shed_phase=LoadPhase.L3,
        shed_was_on=True,
        learned_phase=LoadPhase.L3,
        learned_amps=Decimal('8.4'),
        learning_samples=4,
    )
    return f_loads[0]


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


@pytest.mark.usefixtures('f_kitchen')
def test_marked_hand_back_of_a_floor_is_read_back() -> None:
    hand_back = LiveShed(
        at=SHED_AT,
        reason=ShedReason.OVERLOAD,
        phase=LoadPhase.L2,
        was_on=None,
        device='thermostat-kitchen',
        handed_back=True,
    )

    LoadRepository.mark_shed('kitchen', hand_back)

    assert LoadRepository.all_records() == [replace(KITCHEN, shed=hand_back)]


@pytest.mark.usefixtures('kitchen_shed_while_the_meter_was_lost')
def test_stored_shed_of_a_zone_is_read_back_without_a_plug_state() -> None:
    assert LoadRepository.all_records() == [
        replace(
            KITCHEN,
            shed=LiveShed(
                at=SHED_AT,
                reason=ShedReason.METER_LOST,
                phase=LoadPhase.UNKNOWN,
                was_on=None,
                device='thermostat-kitchen',
            ),
        ),
    ]


@pytest.mark.usefixtures('loads_shed_by_a_release_that_stored_no_device')
def test_shed_stored_without_its_device_is_read_as_switched_off_on_the_device_its_load_controls() -> None:
    assert LoadRepository.all_records() == [
        replace(BOILER, shed=OVERLOAD_SHED),
        replace(KITCHEN, shed=replace(OVERLOAD_SHED, was_on=None, device='thermostat-kitchen')),
    ]


@pytest.mark.usefixtures('kitchen_handed_back_after_an_overload')
def test_cleared_shed_leaves_no_trace() -> None:
    LoadRepository.clear_shed('kitchen')

    shed_fields = ('shed_at', 'shed_reason', 'shed_phase', 'shed_was_on', 'shed_device', 'shed_handed_back')
    assert list(Load.objects.values_list(*shed_fields)) == [(None, '', '', None, '', False)]


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


def test_config_records_list_the_loads_by_priority_with_their_gate(f_loads: list[Load]) -> None:
    boiler, kitchen, hallway = f_loads

    assert LoadRepository.config_records() == [
        LoadConfigRecord(
            id=boiler.pk,
            slug='boiler-1f',
            name='Бойлер 1 этаж',
            kind=LoadKind.PLUG,
            config=LoadConfig(priority=1, phase=LoadPhase.UNKNOWN, nominal_amps=Decimal('6.0')),
            live=True,
        ),
        LoadConfigRecord(
            id=kitchen.pk,
            slug='kitchen',
            name='Кухня',
            kind=LoadKind.ZONE,
            config=LoadConfig(priority=2, phase=LoadPhase.L2, nominal_amps=None),
            live=False,
        ),
        LoadConfigRecord(
            id=hallway.pk,
            slug='hallway',
            name='Прихожая',
            kind=LoadKind.ZONE,
            config=LoadConfig(priority=3, phase=LoadPhase.UNKNOWN, nominal_amps=None),
            live=False,
        ),
    ]


@pytest.mark.usefixtures('f_loads')
def test_saved_configs_swap_two_priorities() -> None:
    LoadRepository.save_configs(
        {
            'boiler-1f': LoadConfig(priority=2, phase=LoadPhase.UNKNOWN, nominal_amps=Decimal('6.0')),
            'kitchen': LoadConfig(priority=1, phase=LoadPhase.L2, nominal_amps=None),
        },
    )

    assert list(Load.objects.order_by('priority').values_list('slug', 'priority')) == [
        ('kitchen', 1),
        ('boiler-1f', 2),
        ('hallway', 3),
    ]


@pytest.mark.usefixtures('f_loads')
def test_saved_configs_rotate_three_priorities() -> None:
    LoadRepository.save_configs(
        {
            'boiler-1f': LoadConfig(priority=3, phase=LoadPhase.UNKNOWN, nominal_amps=Decimal('6.0')),
            'kitchen': LoadConfig(priority=1, phase=LoadPhase.L2, nominal_amps=None),
            'hallway': LoadConfig(priority=2, phase=LoadPhase.UNKNOWN, nominal_amps=None),
        },
    )

    assert list(Load.objects.order_by('priority').values_list('slug', 'priority')) == [
        ('kitchen', 1),
        ('hallway', 2),
        ('boiler-1f', 3),
    ]


@pytest.mark.parametrize(
    ('configs', 'expected'),
    [
        (
            {
                'boiler-1f': LoadConfig(priority=5, phase=LoadPhase.UNKNOWN, nominal_amps=Decimal('6.0')),
                'kitchen': LoadConfig(priority=4, phase=LoadPhase.L2, nominal_amps=None),
            },
            [('hallway', 3), ('kitchen', 4), ('boiler-1f', 5)],
        ),
        (
            {
                'boiler-1f': LoadConfig(priority=5, phase=LoadPhase.UNKNOWN, nominal_amps=Decimal('6.0')),
                'kitchen': LoadConfig(priority=1, phase=LoadPhase.L2, nominal_amps=None),
            },
            [('kitchen', 1), ('hallway', 3), ('boiler-1f', 5)],
        ),
        (
            {
                'boiler-1f': LoadConfig(priority=4, phase=LoadPhase.UNKNOWN, nominal_amps=Decimal('6.0')),
                'kitchen': LoadConfig(priority=6, phase=LoadPhase.L2, nominal_amps=None),
                'hallway': LoadConfig(priority=5, phase=LoadPhase.UNKNOWN, nominal_amps=None),
            },
            [('boiler-1f', 4), ('hallway', 5), ('kitchen', 6)],
        ),
    ],
    ids=['two_above_the_highest', 'one_above_the_highest_one_below', 'every_load_above_the_highest'],
)
@pytest.mark.usefixtures('f_loads')
def test_saved_configs_move_loads_to_priorities_above_the_highest_taken(
    configs: dict[str, LoadConfig],
    expected: list[tuple[str, int]],
) -> None:
    LoadRepository.save_configs(configs)

    assert list(Load.objects.order_by('priority').values_list('slug', 'priority')) == expected


def test_saved_config_sets_phase_and_current_and_keeps_the_gate_the_shed_and_learning(
    f_boiler_shed_after_learning: Load,
) -> None:
    LoadRepository.save_configs({'boiler-1f': LoadConfig(priority=1, phase=LoadPhase.L1, nominal_amps=None)})

    boiler = Load.objects.get(pk=f_boiler_shed_after_learning.pk)
    assert (
        boiler.phase,
        boiler.nominal_amps,
        boiler.live,
        (boiler.shed_at, boiler.shed_reason, boiler.shed_phase, boiler.shed_was_on),
        (boiler.learned_phase, boiler.learned_amps, boiler.learning_samples),
    ) == (
        'l1',
        None,
        True,
        (SHED_AT, 'overload', 'l3', True),
        ('l3', Decimal('8.4'), 4),
    )


@pytest.mark.usefixtures('f_loads')
def test_saving_a_load_that_is_gone_raises_and_changes_nothing() -> None:
    with pytest.raises(LoadNotFoundError):
        LoadRepository.save_configs(
            {
                'kitchen': LoadConfig(priority=4, phase=LoadPhase.L2, nominal_amps=None),
                'attic': LoadConfig(priority=5, phase=LoadPhase.UNKNOWN, nominal_amps=None),
            },
        )

    assert list(Load.objects.order_by('priority').values_list('slug', 'priority')) == [
        ('boiler-1f', 1),
        ('kitchen', 2),
        ('hallway', 3),
    ]
