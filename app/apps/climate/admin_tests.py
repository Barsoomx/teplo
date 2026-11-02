import pytest
from django.contrib import admin
from django.contrib.auth.models import User
from django.http import HttpRequest
from django.test import RequestFactory

from apps.climate.admin import ZoneLearningAdmin
from apps.climate.models import ZoneLearning


@pytest.fixture
def f_request() -> HttpRequest:
    request = RequestFactory().get('/admin/climate/zonelearning/')
    request.user = User(username='owner', is_staff=True, is_superuser=True)
    return request


@pytest.fixture
def f_zone_learning_admin() -> ZoneLearningAdmin:
    return ZoneLearningAdmin(ZoneLearning, admin.site)


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
