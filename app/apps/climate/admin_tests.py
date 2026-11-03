from datetime import UTC, datetime
from decimal import Decimal

import pytest
from django.contrib import admin
from django.contrib.auth.models import User
from django.http import HttpRequest
from django.test import RequestFactory

from apps.climate.admin import ZoneAdmin, ZoneLearningAdmin
from apps.climate.factories import ClimateSettingsFactory, ZoneFactory, ZoneLearningFactory
from apps.climate.models import Zone, ZoneLearning
from apps.power.factories import ZoneLoadFactory

pytestmark = pytest.mark.django_db

CYCLE_FIELDS = {'boost_until', 'last_command_at', 'last_dry_run_at', 'last_air_dry_run_at'}
SHED_AT = datetime(2026, 11, 2, 18, 40, tzinfo=UTC)


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
def f_kitchen() -> Zone:
    return ZoneFactory.create(slug='kitchen', thermostat__slug='thermostat-kitchen')


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
