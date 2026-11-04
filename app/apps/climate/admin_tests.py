from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from django import forms
from django.contrib import admin
from django.contrib.auth.models import User
from django.http import HttpRequest
from django.test import Client, RequestFactory
from django.utils import translation

from apps.climate.admin import (
    GUARD_FIELDS,
    ClimateSettingsAdmin,
    ComfortPeriodInline,
    HeatSourceAdmin,
    RoomClimateAdmin,
    ZoneAdmin,
    ZoneLearningAdmin,
)
from apps.climate.domain.heat_sources import HeatSourceKind
from apps.climate.factories import (
    ClimateSettingsFactory,
    HeatSourceFactory,
    RoomClimateFactory,
    ZoneFactory,
    ZoneLearningFactory,
)
from apps.climate.models import ClimateSettings, HeatSource, RoomClimate, Zone, ZoneLearning
from apps.devices.domain.kinds import DeviceDriver, DeviceKind
from apps.devices.factories import DeviceFactory
from apps.devices.models import Device
from apps.house.factories import RoomFactory
from apps.house.models import Room
from apps.power.factories import LoadSettingsFactory, SourceLoadFactory, ZoneLoadFactory
from apps.telemetry.models import JournalEntry

pytestmark = pytest.mark.django_db

CYCLE_FIELDS = {'boost_until', 'last_command_at', 'last_dry_run_at', 'last_air_dry_run_at', 'manual_until'}
SHED_AT = datetime(2026, 11, 2, 18, 40, tzinfo=UTC)
CLIMATE_POSTED = {
    'air_start_offset': '0.30',
    'initial_early_stop_offset': '0.30',
    'air_min_band': '0.20',
    'preheat_lead_cap_minutes': '240',
    'learned_mornings_needed': '3',
    'guests_end_hour': '14',
}
BEDROOM_POSTED = {
    'comfort_target': '20.5',
    'setback_target': '19.5',
    'backup_below': '19.0',
    'backup_hysteresis': '0.30',
}
SENSOR_OF_A_ZONE = 'This sensor is the air sensor of a zone'
SENSOR_IN_ANOTHER_ROOM = 'This sensor is placed in another room'
SETBACK_ABOVE_COMFORT = 'The night target must not be above the comfort target'
BACKUP_NOT_BELOW_SETBACK = (
    'Backup heat must turn on at least 0.5° below the night target, or it fights the night target'
)
BELOW_MIN_BAND = 'Must not be below the air min band, or heating stops above the target'
IDLE_NOT_BELOW_LIMIT = (
    'The idle floor setpoint must be below the floor limit setpoint, or air mode would idle warmer than it heats'
)
DEVICE_UNLESS_FIREPLACE = 'A fireplace has no device, and any other heat source needs its device'
SOURCE_STAMP_FIELDS = ('manual_until', 'last_command_at', 'last_dry_run_at', 'commanded_power', 'commanded_setpoint')
SOURCE_COMMANDS_POSTED = {'setpoint_bias': '0.0', 'max_setpoint': '24.0', 'min_write_minutes': '5'}
LEARNING_URL = '/admin/climate/zonelearning/'


@pytest.fixture
def f_request() -> HttpRequest:
    request = RequestFactory().get('/admin/')
    request.user = User(username='owner', is_staff=True, is_superuser=True)
    return request


@pytest.fixture
def f_zone_admin() -> ZoneAdmin:
    return ZoneAdmin(Zone, admin.site)


@pytest.fixture
def f_zone_learning_admin() -> ZoneLearningAdmin:
    return ZoneLearningAdmin(ZoneLearning, admin.site)


@pytest.fixture
def f_climate_settings_admin() -> ClimateSettingsAdmin:
    return ClimateSettingsAdmin(ClimateSettings, admin.site)


@pytest.fixture
def f_room_climate_admin() -> RoomClimateAdmin:
    return RoomClimateAdmin(RoomClimate, admin.site)


@pytest.fixture
def f_bedroom_climate() -> RoomClimate:
    return RoomClimateFactory.create(
        room=RoomFactory.create(slug='bedroom', name='Спальня'),
        comfort_target=Decimal('20.5'),
        backup_zone=ZoneFactory.create(slug='bathroom-2f'),
    )


@pytest.fixture
def f_sensors(f_bedroom_climate: RoomClimate) -> dict[str, Device]:
    office = RoomFactory.create(slug='office', name='Кабинет')
    sensors = {
        slug: DeviceFactory.create(
            slug=slug,
            kind=DeviceKind.TEMPERATURE_SENSOR,
            driver=DeviceDriver.ESPHOME_TEMPERATURE,
            room=room,
        )
        for slug, room in (
            ('air-second-floor', f_bedroom_climate.room),
            ('air-attic', None),
            ('air-office', office),
            ('air-kitchen', None),
        )
    }
    ZoneFactory.create(slug='kitchen', air_sensor=sensors['air-kitchen'])
    return sensors


@pytest.fixture
def f_placed_kitchen(request: pytest.FixtureRequest) -> Zone:
    room = None if request.param == 'no_room' else RoomFactory.create(slug='open-space')
    if request.param == 'room_climate':
        RoomClimateFactory.create(room=room)
    return ZoneFactory.create(slug='kitchen', room=room)


@pytest.fixture
def russian() -> Iterator[None]:
    with translation.override('ru'):
        yield


@pytest.fixture
def f_kitchen() -> Zone:
    return ZoneFactory.create(slug='kitchen', thermostat__slug='thermostat-kitchen')


@pytest.fixture
def f_kitchen_posted(f_kitchen: Zone) -> dict[str, str]:
    return {
        'slug': f_kitchen.slug,
        'name': f_kitchen.name,
        'kind': f_kitchen.kind,
        'thermostat': str(f_kitchen.thermostat.pk),
        'heating_enabled': 'on',
        'comfort_floor_setpoint': '28.0',
        'night_floor_setpoint': '22.0',
        'boost_floor_setpoint': '30.0',
        'idle_floor_setpoint': '18.0',
        'floor_limit_setpoint': '30.0',
        'comfort_air_target': '22.0',
        'night_air_target': '19.5',
        'preheat_lead_minutes': '180',
    }


@pytest.fixture
def f_kitchen_moved_to_the_open_space(
    f_zone_admin: ZoneAdmin,
    f_request: HttpRequest,
    f_kitchen: Zone,
    f_kitchen_posted: dict[str, str],
) -> forms.ModelForm:
    air = DeviceFactory.create(
        slug='air-kitchen',
        kind=DeviceKind.TEMPERATURE_SENSOR,
        driver=DeviceDriver.ESPHOME_TEMPERATURE,
    )
    open_space = RoomFactory.create(slug='open-space')
    DeviceFactory.create(
        slug='plug-oven',
        kind=DeviceKind.PLUG,
        driver=DeviceDriver.TUYA_PLUG,
        monitor_only=True,
        room=RoomFactory.create(slug='pantry'),
    )
    posted = {**f_kitchen_posted, 'air_sensor': str(air.pk), 'room': str(open_space.pk)}
    return f_zone_admin.get_form(f_request, f_kitchen, change=True)(posted, instance=f_kitchen)


@pytest.fixture
def f_kitchen_with_its_load(request: pytest.FixtureRequest, f_kitchen: Zone) -> Zone:
    ZoneLoadFactory.create(slug='kitchen', zone=f_kitchen, shed_at=request.param)
    return f_kitchen


@pytest.fixture
def f_learned_kitchen(request: pytest.FixtureRequest, f_kitchen: Zone) -> ZoneLearning:
    ClimateSettingsFactory.create(air_start_offset=request.param)
    return ZoneLearningFactory.create(zone=f_kitchen, early_stop_offset=Decimal('0.45'))


@pytest.fixture
def f_kitchen_handed_back(f_kitchen: Zone) -> Zone:
    ZoneLoadFactory.create(slug='kitchen', zone=f_kitchen, shed_at=SHED_AT, shed_handed_back=True)
    return f_kitchen


def test_zone_page_shows_the_boost_and_the_write_stamps_without_posting_them_back(
    f_zone_admin: ZoneAdmin,
    f_request: HttpRequest,
) -> None:
    form = f_zone_admin.get_form(f_request, change=True)

    assert (set(f_zone_admin.get_readonly_fields(f_request)), CYCLE_FIELDS & set(form.base_fields)) == (
        CYCLE_FIELDS,
        set(),
    )


@pytest.mark.parametrize(
    ('f_kitchen_with_its_load', 'locked'),
    [(None, set()), (SHED_AT, {'slug', 'thermostat'})],
    ids=['load_not_shed', 'load_shed_live'],
    indirect=['f_kitchen_with_its_load'],
)
def test_zone_page_keeps_the_slug_and_the_thermostat_read_only_while_its_load_is_shed_live(
    f_zone_admin: ZoneAdmin,
    f_request: HttpRequest,
    f_kitchen_with_its_load: Zone,
    locked: set[str],
) -> None:
    readonly = set(f_zone_admin.get_readonly_fields(f_request, f_kitchen_with_its_load))
    form = f_zone_admin.get_form(f_request, f_kitchen_with_its_load, change=True)

    assert (readonly - CYCLE_FIELDS, locked & set(form.base_fields)) == (locked, set())


def test_zone_page_of_a_zone_whose_floor_was_handed_back_keeps_the_slug_and_the_thermostat_editable(
    f_zone_admin: ZoneAdmin,
    f_request: HttpRequest,
    f_kitchen_handed_back: Zone,
) -> None:
    assert set(f_zone_admin.get_readonly_fields(f_request, f_kitchen_handed_back)) == CYCLE_FIELDS


def test_zone_page_of_a_zone_without_a_load_keeps_the_slug_and_the_thermostat_editable(
    f_zone_admin: ZoneAdmin,
    f_request: HttpRequest,
    f_kitchen: Zone,
) -> None:
    readonly = set(f_zone_admin.get_readonly_fields(f_request, f_kitchen))
    form = f_zone_admin.get_form(f_request, f_kitchen, change=True)

    assert (readonly, {'slug', 'thermostat'} <= set(form.base_fields)) == (CYCLE_FIELDS, True)


def test_saving_a_zone_moves_its_thermostat_and_air_sensor_into_its_room(
    f_zone_admin: ZoneAdmin,
    f_request: HttpRequest,
    f_kitchen_moved_to_the_open_space: forms.ModelForm,
) -> None:
    valid = f_kitchen_moved_to_the_open_space.is_valid()
    f_zone_admin.save_model(
        f_request,
        f_kitchen_moved_to_the_open_space.save(commit=False),
        f_kitchen_moved_to_the_open_space,
        change=True,
    )

    assert (valid, sorted(Device.objects.values_list('slug', 'room__slug'))) == (
        True,
        [('air-kitchen', 'open-space'), ('plug-oven', 'pantry'), ('thermostat-kitchen', 'open-space')],
    )


def test_zone_learning_admin_shows_what_zones_learned_without_editing_it(
    f_zone_learning_admin: ZoneLearningAdmin,
    f_request: HttpRequest,
) -> None:
    assert (
        f_zone_learning_admin.has_view_permission(f_request),
        f_zone_learning_admin.has_add_permission(f_request),
        f_zone_learning_admin.has_change_permission(f_request),
        f_zone_learning_admin.has_delete_permission(f_request),
    ) == (True, False, False, False)


@pytest.mark.parametrize(
    ('f_learned_kitchen', 'in_effect'),
    [(Decimal('0.30'), Decimal('0.10')), (Decimal('0.80'), Decimal('0.45'))],
    ids=['held_to_the_minimum_band', 'wider_start_offset_leaves_room'],
    indirect=['f_learned_kitchen'],
)
def test_zone_learning_admin_shows_the_early_stop_offset_in_effect_beside_the_learned_one(
    f_zone_learning_admin: ZoneLearningAdmin,
    f_learned_kitchen: ZoneLearning,
    in_effect: Decimal,
) -> None:
    assert (
        f_zone_learning_admin.list_display[1:3],
        f_zone_learning_admin.early_stop_offset_in_effect(f_learned_kitchen),
    ) == (('early_stop_offset', 'early_stop_offset_in_effect'), in_effect)


@pytest.mark.parametrize(
    ('posted', 'errors'),
    [
        ({}, {}),
        ({'air_start_offset': '0.20'}, {}),
        ({'air_start_offset': '0.15'}, {'air_start_offset': [BELOW_MIN_BAND]}),
        ({'air_min_band': '0.40'}, {'air_start_offset': [BELOW_MIN_BAND]}),
    ],
    ids=['defaults', 'start_offset_at_the_band', 'start_offset_narrowed', 'band_widened'],
)
def test_climate_settings_page_refuses_a_start_offset_below_the_min_band(
    f_climate_settings_admin: ClimateSettingsAdmin,
    f_request: HttpRequest,
    posted: dict[str, str],
    errors: dict[str, list[str]],
) -> None:
    form = f_climate_settings_admin.get_form(f_request)({**CLIMATE_POSTED, **posted})

    assert (form.is_valid(), form.errors) == (not errors, errors)


@pytest.mark.usefixtures('russian')
def test_climate_settings_page_explains_a_start_offset_below_the_min_band_in_the_page_language(
    f_climate_settings_admin: ClimateSettingsAdmin,
    f_request: HttpRequest,
) -> None:
    form = f_climate_settings_admin.get_form(f_request)({**CLIMATE_POSTED, 'air_start_offset': '0.10'})

    assert (form.is_valid(), form.errors) == (
        False,
        {'air_start_offset': ['Не может быть меньше air min band, иначе обогрев остановится выше цели']},
    )


@pytest.mark.parametrize(
    ('posted', 'errors'),
    [
        ({}, {}),
        ({'idle_floor_setpoint': '29.9'}, {}),
        ({'idle_floor_setpoint': '30.0'}, {'__all__': [IDLE_NOT_BELOW_LIMIT]}),
        ({'floor_limit_setpoint': '17.5'}, {'__all__': [IDLE_NOT_BELOW_LIMIT]}),
    ],
    ids=['as_seeded', 'idle_just_below_the_limit', 'idle_at_the_limit', 'limit_lowered_below_idle'],
)
def test_zone_page_refuses_an_idle_floor_setpoint_not_below_the_floor_limit(
    f_zone_admin: ZoneAdmin,
    f_request: HttpRequest,
    f_kitchen: Zone,
    f_kitchen_posted: dict[str, str],
    posted: dict[str, str],
    errors: dict[str, list[str]],
) -> None:
    form = f_zone_admin.get_form(f_request, f_kitchen, change=True)({**f_kitchen_posted, **posted}, instance=f_kitchen)

    assert (form.is_valid(), form.errors) == (not errors, errors)


@pytest.mark.parametrize(
    ('f_placed_kitchen', 'inlines'),
    [('room_climate', []), ('room_without_climate', [ComfortPeriodInline]), ('no_room', [ComfortPeriodInline])],
    indirect=['f_placed_kitchen'],
)
def test_zone_page_edits_the_comfort_windows_only_of_a_zone_that_holds_its_own_comfort(
    f_zone_admin: ZoneAdmin,
    f_request: HttpRequest,
    f_placed_kitchen: Zone,
    inlines: list[type[ComfortPeriodInline]],
) -> None:
    assert (
        list(f_zone_admin.get_inlines(f_request, f_placed_kitchen)),
        list(f_zone_admin.get_inlines(f_request, None)),
    ) == (
        inlines,
        [ComfortPeriodInline],
    )


def test_climate_settings_page_leaves_out_the_second_floor_guard_fields(
    f_climate_settings_admin: ClimateSettingsAdmin,
    f_request: HttpRequest,
) -> None:
    form = f_climate_settings_admin.get_form(f_request)

    assert (set(GUARD_FIELDS) & set(form.base_fields), f_climate_settings_admin.list_display) == (
        set(),
        ('__str__', 'outdoor_sensor', 'manual_hold_live'),
    )


def test_climate_settings_admin_switches_the_manual_hold_in_the_list_and_on_the_page(
    f_climate_settings_admin: ClimateSettingsAdmin,
    f_request: HttpRequest,
) -> None:
    form = f_climate_settings_admin.get_form(f_request)

    assert (f_climate_settings_admin.list_editable, 'manual_hold_live' in form.base_fields) == (
        ('manual_hold_live',),
        True,
    )


def test_climate_settings_page_shows_the_guests_end_without_posting_it_back(
    f_climate_settings_admin: ClimateSettingsAdmin,
    f_request: HttpRequest,
) -> None:
    form = f_climate_settings_admin.get_form(f_request)

    assert (
        f_climate_settings_admin.get_readonly_fields(f_request),
        'guests_until' in form.base_fields,
        'guests_end_hour' in form.base_fields,
    ) == (('guests_until',), False, True)


def test_room_climate_admin_changes_the_rows_the_site_data_created_without_adding_or_deleting_any(
    f_room_climate_admin: RoomClimateAdmin,
    f_request: HttpRequest,
    f_bedroom_climate: RoomClimate,
) -> None:
    assert (
        f_room_climate_admin.has_add_permission(f_request),
        f_room_climate_admin.has_change_permission(f_request, f_bedroom_climate),
        f_room_climate_admin.has_delete_permission(f_request, f_bedroom_climate),
        f_room_climate_admin.get_readonly_fields(f_request, f_bedroom_climate),
    ) == (False, True, False, ('room', 'override_target', 'override_until', 'override_source'))


def test_room_climate_page_shows_the_override_without_posting_it_back_and_edits_the_guest_room(
    f_room_climate_admin: RoomClimateAdmin,
    f_request: HttpRequest,
    f_bedroom_climate: RoomClimate,
) -> None:
    form = f_room_climate_admin.get_form(f_request, f_bedroom_climate, change=True)

    assert (
        {'override_target', 'override_until', 'override_source'} & set(form.base_fields),
        'guest_room' in form.base_fields,
    ) == (set(), True)


@pytest.mark.parametrize(
    ('sensor', 'errors'),
    [
        ('air-second-floor', {}),
        ('air-attic', {}),
        ('air-kitchen', {'air_sensor': [SENSOR_OF_A_ZONE]}),
        ('air-office', {'air_sensor': [SENSOR_IN_ANOTHER_ROOM]}),
    ],
    ids=['placed_in_the_room', 'placed_nowhere', 'air_sensor_of_a_zone', 'placed_in_another_room'],
)
def test_room_climate_page_refuses_an_air_sensor_of_a_zone_or_of_another_room(
    f_room_climate_admin: RoomClimateAdmin,
    f_request: HttpRequest,
    f_bedroom_climate: RoomClimate,
    f_sensors: dict[str, Device],
    sensor: str,
    errors: dict[str, list[str]],
) -> None:
    form = f_room_climate_admin.get_form(f_request, f_bedroom_climate, change=True)(
        {
            **BEDROOM_POSTED,
            'backup_zone': str(f_bedroom_climate.backup_zone_id),
            'air_sensor': str(f_sensors[sensor].pk),
        },
        instance=f_bedroom_climate,
    )

    assert (form.is_valid(), form.errors) == (not errors, errors)


@pytest.mark.parametrize(
    ('posted', 'errors'),
    [
        ({}, {}),
        ({'setback_target': '21.0', 'backup_below': '18.0'}, {'__all__': [SETBACK_ABOVE_COMFORT]}),
        ({'backup_below': '19.1'}, {'__all__': [BACKUP_NOT_BELOW_SETBACK]}),
        ({'backup_below': '19.1', 'backup_zone': ''}, {}),
    ],
    ids=['as_seeded', 'night_above_comfort', 'backup_heat_too_close', 'no_backup_zone'],
)
def test_room_climate_page_refuses_a_night_target_above_comfort_and_backup_heat_close_to_it(
    f_room_climate_admin: RoomClimateAdmin,
    f_request: HttpRequest,
    f_bedroom_climate: RoomClimate,
    posted: dict[str, str],
    errors: dict[str, list[str]],
) -> None:
    form = f_room_climate_admin.get_form(f_request, f_bedroom_climate, change=True)(
        {**BEDROOM_POSTED, 'backup_zone': str(f_bedroom_climate.backup_zone_id), **posted},
        instance=f_bedroom_climate,
    )

    assert (form.is_valid(), form.errors) == (not errors, errors)


@pytest.mark.usefixtures('russian')
def test_room_climate_page_explains_its_refusals_in_the_page_language(
    f_room_climate_admin: RoomClimateAdmin,
    f_request: HttpRequest,
    f_bedroom_climate: RoomClimate,
    f_sensors: dict[str, Device],
) -> None:
    form_class = f_room_climate_admin.get_form(f_request, f_bedroom_climate, change=True)
    zone_sensor = form_class(
        {**BEDROOM_POSTED, 'air_sensor': str(f_sensors['air-kitchen'].pk)},
        instance=f_bedroom_climate,
    )
    office_sensor = form_class(
        {**BEDROOM_POSTED, 'air_sensor': str(f_sensors['air-office'].pk)},
        instance=f_bedroom_climate,
    )
    backup_too_close = form_class(
        {**BEDROOM_POSTED, 'backup_zone': str(f_bedroom_climate.backup_zone_id), 'backup_below': '19.5'},
        instance=f_bedroom_climate,
    )
    night_above_comfort = form_class({**BEDROOM_POSTED, 'setback_target': '21.0'}, instance=f_bedroom_climate)

    assert (zone_sensor.errors, office_sensor.errors, backup_too_close.errors, night_above_comfort.errors) == (
        {'air_sensor': ['Это датчик воздуха зоны']},
        {'air_sensor': ['Этот датчик стоит в другой комнате']},
        {'__all__': ['Резервный обогрев должен включаться хотя бы на 0,5° ниже ночной цели, иначе он спорит с ней']},
        {'__all__': ['Ночная цель не может быть выше комфортной']},
    )


@pytest.fixture
def f_heat_source_admin() -> HeatSourceAdmin:
    return HeatSourceAdmin(HeatSource, admin.site)


@pytest.fixture
def f_open_space() -> Room:
    return RoomFactory.create(slug='open-space', name='Гостиная, кухня и прихожая')


@pytest.fixture
def f_climate_unit() -> Device:
    return DeviceFactory.create(slug='heater-office', kind=DeviceKind.CLIMATE_UNIT, driver=DeviceDriver.TUYA_HEATER)


@pytest.fixture
def f_heat_source_posted(request: pytest.FixtureRequest, f_open_space: Room, f_climate_unit: Device) -> dict[str, str]:
    kind, with_device = request.param
    return {
        'slug': 'fireplace',
        'name': 'Камин',
        'room': str(f_open_space.pk),
        'kind': kind,
        'device': str(f_climate_unit.pk) if with_device else '',
        'priority': '1',
        **SOURCE_COMMANDS_POSTED,
    }


@pytest.fixture
def f_office() -> Room:
    return RoomFactory.create(slug='office', name='Кабинет')


@pytest.fixture
def f_heater_posted(request: pytest.FixtureRequest, f_office: Room, f_open_space: Room) -> dict[str, str]:
    kind, room, driver = request.param
    ZoneFactory.create(slug='kitchen', room=f_open_space)
    device = DeviceFactory.create(slug='heater-office', kind=DeviceKind.CLIMATE_UNIT, driver=driver)
    return {
        'slug': 'convector-office',
        'name': 'Конвектор: кабинет',
        'room': str((f_office if room == 'office' else f_open_space).pk),
        'kind': kind,
        'device': str(device.pk),
        'priority': '1',
        **SOURCE_COMMANDS_POSTED,
    }


@pytest.fixture
def f_convector(f_office: Room, f_climate_unit: Device) -> HeatSource:
    return HeatSourceFactory.create(
        slug='convector-office',
        name='Конвектор: кабинет',
        room=f_office,
        kind=HeatSourceKind.HEATER,
        device=f_climate_unit,
    )


@pytest.fixture
def f_convector_gates(request: pytest.FixtureRequest, f_convector: HeatSource) -> HeatSource:
    source_live, load_live, management_live = request.param
    HeatSource.objects.filter(pk=f_convector.pk).update(live=source_live)
    if load_live is not None:
        SourceLoadFactory.create(heat_source=f_convector, live=load_live)
    LoadSettingsFactory.create(load_management_live=management_live)
    return HeatSource.objects.get(pk=f_convector.pk)


@pytest.fixture
def f_admin_client() -> Client:
    client = Client(headers={'accept-language': 'ru'})
    client.force_login(User.objects.create_superuser(username='owner', password='owner-password'))  # noqa: S106
    return client


@pytest.fixture
def f_learned_zones() -> list[ZoneLearning]:
    return [
        ZoneLearningFactory.create(
            zone__slug='living-room',
            zone__name='Гостиная',
            dead_time=timedelta(minutes=35),
            heat_up_rates={'-5': '0.290'},
            mornings=4,
        ),
        ZoneLearningFactory.create(zone__slug='kitchen', zone__name='Кухня', early_stop_offset=Decimal('0.25')),
        ZoneLearningFactory.create(zone__slug='hallway', zone__name='Прихожая'),
    ]


def test_heat_source_admin_lists_the_sources_with_their_gate_and_shows_the_mark_read_only(
    f_heat_source_admin: HeatSourceAdmin,
    f_request: HttpRequest,
) -> None:
    form = f_heat_source_admin.get_form(f_request)

    assert (
        f_heat_source_admin.list_display,
        f_heat_source_admin.list_editable,
        f_heat_source_admin.get_readonly_fields(f_request),
        'lit_until' in form.base_fields,
    ) == (
        ('name', 'room', 'kind', 'device', 'priority', 'live', 'lit_until', 'commands'),
        ('live',),
        ('lit_until', *SOURCE_STAMP_FIELDS),
        False,
    )


def test_heater_page_edits_its_commands_and_shows_what_teplo_stored_read_only(
    f_heat_source_admin: HeatSourceAdmin,
    f_request: HttpRequest,
    f_convector: HeatSource,
) -> None:
    form = f_heat_source_admin.get_form(f_request, f_convector, change=True)

    assert (
        {'setpoint_bias', 'max_setpoint', 'min_outdoor', 'min_write_minutes', 'rated_watts'} <= set(form.base_fields),
        set(SOURCE_STAMP_FIELDS) & set(form.base_fields),
        set(SOURCE_STAMP_FIELDS) <= set(f_heat_source_admin.get_fields(f_request, f_convector)),
    ) == (True, set(), True)


@pytest.mark.parametrize(
    ('f_heater_posted', 'errors'),
    [
        ((HeatSourceKind.HEATER, 'office', DeviceDriver.TUYA_HEATER), {}),
        (
            (HeatSourceKind.HEATER, 'open-space', DeviceDriver.TUYA_HEATER),
            {'room': ['В этой комнате тёплый пол: teplo не управляет источником тепла рядом с ним']},
        ),
        (
            (HeatSourceKind.HEATER, 'office', DeviceDriver.UNSUPPORTED),
            {'device': ['Обогревателю нужен климатический прибор с управляющим драйвером, например tuya_heater']},
        ),
        (
            (HeatSourceKind.AIR_CONDITIONER, 'office', DeviceDriver.TUYA_HEATER),
            {'kind': ['У teplo пока нет драйвера кондиционера']},
        ),
    ],
    ids=['heater', 'beside_a_floor', 'driver_only_reads', 'air_conditioner'],
    indirect=['f_heater_posted'],
)
@pytest.mark.usefixtures('russian')
def test_heat_source_page_refuses_a_source_teplo_cannot_command(
    f_heat_source_admin: HeatSourceAdmin,
    f_request: HttpRequest,
    f_heater_posted: dict[str, str],
    errors: dict[str, list[str]],
) -> None:
    form = f_heat_source_admin.get_form(f_request)(f_heater_posted)

    assert (form.is_valid(), form.errors) == (not errors, errors)


@pytest.mark.parametrize(
    ('f_convector_gates', 'commands'),
    [
        ((True, True, True), 'Не доходят: TEPLO_DRY_RUN'),
        (
            (False, False, False),
            'Не доходят: TEPLO_DRY_RUN, live источника, load management live, live нагрузки',
        ),
        ((True, None, True), 'Не доходят: TEPLO_DRY_RUN, нет нагрузки'),
    ],
    ids=['every_gate_but_dry_run', 'every_gate_closed', 'no_load'],
    indirect=['f_convector_gates'],
)
@pytest.mark.usefixtures('russian')
def test_heat_source_list_names_the_gates_that_hold_its_commands_back(
    f_heat_source_admin: HeatSourceAdmin,
    f_convector_gates: HeatSource,
    commands: str,
) -> None:
    assert f_heat_source_admin.commands(f_convector_gates) == commands


def test_saving_a_heat_source_places_its_device_in_its_room(
    f_admin_client: Client,
    f_convector: HeatSource,
    f_office: Room,
) -> None:
    f_admin_client.post(
        f'/admin/climate/heatsource/{f_convector.pk}/change/',
        {
            'slug': 'convector-office',
            'name': 'Конвектор: кабинет',
            'room': str(f_office.pk),
            'kind': HeatSourceKind.HEATER,
            'device': str(f_convector.device_id),
            'priority': '1',
            **SOURCE_COMMANDS_POSTED,
        },
    )

    assert Device.objects.get(slug='heater-office').room_id == f_office.pk


@pytest.mark.parametrize(
    ('f_heat_source_posted', 'errors'),
    [
        ((HeatSourceKind.FIREPLACE, False), {}),
        ((HeatSourceKind.FIREPLACE, True), {'__all__': [DEVICE_UNLESS_FIREPLACE]}),
        ((HeatSourceKind.HEATER, False), {'__all__': [DEVICE_UNLESS_FIREPLACE]}),
        ((HeatSourceKind.HEATER, True), {}),
    ],
    ids=['fireplace', 'fireplace_with_a_device', 'heater_without_a_device', 'heater'],
    indirect=['f_heat_source_posted'],
)
def test_heat_source_page_refuses_a_device_on_a_fireplace_and_a_source_without_one(
    f_heat_source_admin: HeatSourceAdmin,
    f_request: HttpRequest,
    f_heat_source_posted: dict[str, str],
    errors: dict[str, list[str]],
) -> None:
    form = f_heat_source_admin.get_form(f_request)(f_heat_source_posted)

    assert (form.is_valid(), form.errors) == (not errors, errors)


@pytest.mark.usefixtures('russian')
@pytest.mark.parametrize('f_heat_source_posted', [(HeatSourceKind.FIREPLACE, True)], indirect=True)
def test_heat_source_page_explains_its_refusal_in_the_page_language(
    f_heat_source_admin: HeatSourceAdmin,
    f_request: HttpRequest,
    f_heat_source_posted: dict[str, str],
) -> None:
    form = f_heat_source_admin.get_form(f_request)(f_heat_source_posted)

    assert form.errors == {
        '__all__': ['У камина нет устройства, а любому другому источнику тепла нужно его устройство'],
    }


def test_reset_learning_action_resets_the_zones_that_learned_and_names_them(
    f_admin_client: Client,
    f_learned_zones: list[ZoneLearning],
) -> None:
    response = f_admin_client.post(
        LEARNING_URL,
        {'action': 'reset_learning', 'index': '0', '_selected_action': [learning.pk for learning in f_learned_zones]},
        follow=True,
    )

    assert (
        response.status_code,
        [str(message) for message in response.context['messages']],
        sorted(ZoneLearning.objects.values_list('zone__slug', 'early_stop_offset', 'mornings', 'resets')),
        sorted(JournalEntry.objects.values_list('kind', 'subject')),
    ) == (
        200,
        ['Обучение сброшено: Гостиная, Кухня', 'Сбрасывать нечего: Прихожая ещё ничего не выучили'],
        [('hallway', None, 0, 0), ('kitchen', None, 0, 1), ('living-room', None, 0, 1)],
        [('zone_learning_reset', 'kitchen'), ('zone_learning_reset', 'living-room')],
    )
