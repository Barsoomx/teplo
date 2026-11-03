from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from django import forms
from django.contrib import admin
from django.contrib.auth.models import User
from django.http import HttpRequest
from django.test import RequestFactory
from django.utils import translation

from apps.climate.admin import (
    GUARD_FIELDS,
    ClimateSettingsAdmin,
    ComfortPeriodInline,
    RoomClimateAdmin,
    ZoneAdmin,
    ZoneLearningAdmin,
)
from apps.climate.factories import ClimateSettingsFactory, RoomClimateFactory, ZoneFactory, ZoneLearningFactory
from apps.climate.models import ClimateSettings, RoomClimate, Zone, ZoneLearning
from apps.devices.domain.kinds import DeviceDriver, DeviceKind
from apps.devices.factories import DeviceFactory
from apps.devices.models import Device
from apps.house.factories import RoomFactory
from apps.power.factories import ZoneLoadFactory

pytestmark = pytest.mark.django_db

CYCLE_FIELDS = {'boost_until', 'last_command_at', 'last_dry_run_at', 'last_air_dry_run_at'}
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
        ('__str__', 'outdoor_sensor'),
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
