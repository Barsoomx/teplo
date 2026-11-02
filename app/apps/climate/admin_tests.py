import pytest
from django.contrib import admin
from django.contrib.auth.models import User
from django.http import HttpRequest
from django.test import RequestFactory

from apps.climate.admin import ZoneAdmin, ZoneLearningAdmin
from apps.climate.models import Zone, ZoneLearning

CYCLE_FIELDS = {'boost_until', 'last_command_at', 'last_dry_run_at', 'last_air_dry_run_at'}


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


def test_zone_page_shows_the_boost_and_the_write_stamps_without_posting_them_back(
    f_zone_admin: ZoneAdmin,
    f_request: HttpRequest,
) -> None:
    form = f_zone_admin.get_form(f_request, change=True)

    assert (set(f_zone_admin.get_readonly_fields(f_request)), CYCLE_FIELDS & set(form.base_fields)) == (
        CYCLE_FIELDS,
        set(),
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
