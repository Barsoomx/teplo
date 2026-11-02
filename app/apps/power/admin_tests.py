import pytest
from django.contrib import admin
from django.contrib.auth.models import User
from django.http import HttpRequest
from django.test import RequestFactory

from apps.power.admin import OutageAdmin
from apps.power.factories import OutageFactory
from apps.power.models import Outage

pytestmark = pytest.mark.django_db

BOOT_ID = '5d1c2a7e-0b4f-4c8a-9e3d-6f2b1a0c9d8e'


@pytest.fixture
def f_outage_admin() -> OutageAdmin:
    return OutageAdmin(Outage, admin.site)


@pytest.fixture
def f_owner_request() -> HttpRequest:
    request = RequestFactory().get('/admin/power/outage/')
    request.user = User(username='owner', is_staff=True, is_superuser=True)
    return request


@pytest.fixture
def f_outage(request: pytest.FixtureRequest) -> Outage:
    return OutageFactory.create(boot_id=request.param)


@pytest.fixture
def outages_entered_by_hand_and_recorded_for_a_boot() -> None:
    OutageFactory.create(boot_id=None)
    OutageFactory.create(boot_id=BOOT_ID)


@pytest.mark.parametrize(
    ('f_outage', 'deletable'),
    [(None, True), (BOOT_ID, False)],
    ids=['entered_by_hand', 'recorded_for_a_boot'],
    indirect=['f_outage'],
)
def test_admin_deletes_an_outage_only_when_it_carries_no_boot_id(
    f_outage_admin: OutageAdmin,
    f_owner_request: HttpRequest,
    f_outage: Outage,
    deletable: bool,
) -> None:
    assert f_outage_admin.has_delete_permission(f_owner_request, f_outage) is deletable


def test_admin_offers_to_delete_outages_from_their_list(
    f_outage_admin: OutageAdmin,
    f_owner_request: HttpRequest,
) -> None:
    assert f_outage_admin.has_delete_permission(f_owner_request) is True


@pytest.mark.usefixtures('outages_entered_by_hand_and_recorded_for_a_boot')
def test_admin_refuses_to_delete_a_selection_holding_an_outage_recorded_for_a_boot(
    f_outage_admin: OutageAdmin,
    f_owner_request: HttpRequest,
) -> None:
    _, _, perms_needed, _ = f_outage_admin.get_deleted_objects(Outage.objects.all(), f_owner_request)

    assert perms_needed == {'outage'}
