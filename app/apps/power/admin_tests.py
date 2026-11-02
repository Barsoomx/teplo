import pytest
from django.contrib import admin
from django.contrib.auth.models import User
from django.http import HttpRequest
from django.test import RequestFactory

from apps.power.admin import LoadAdmin, LoadSettingsAdmin, OutageAdmin
from apps.power.factories import LoadSettingsFactory, OutageFactory
from apps.power.models import Load, LoadSettings, Outage

pytestmark = pytest.mark.django_db

BOOT_ID = '5d1c2a7e-0b4f-4c8a-9e3d-6f2b1a0c9d8e'
GUARD_FIELDS = {
    'learned_phase',
    'learned_amps',
    'learning_samples',
    'shed_at',
    'shed_reason',
    'shed_phase',
    'shed_was_on',
}


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
def f_load_admin() -> LoadAdmin:
    return LoadAdmin(Load, admin.site)


@pytest.fixture
def f_load_settings_admin() -> LoadSettingsAdmin:
    return LoadSettingsAdmin(LoadSettings, admin.site)


@pytest.fixture
def stored_load_settings() -> None:
    LoadSettingsFactory.create()


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


def test_load_page_shows_what_learning_and_shedding_stored_without_posting_it_back(
    f_load_admin: LoadAdmin,
    f_owner_request: HttpRequest,
) -> None:
    form = f_load_admin.get_form(f_owner_request, change=True)

    assert (
        set(f_load_admin.get_readonly_fields(f_owner_request)),
        GUARD_FIELDS & set(form.base_fields),
        {'phase', 'nominal_amps', 'live'} <= set(form.base_fields),
    ) == (GUARD_FIELDS, set(), True)


def test_load_settings_admin_adds_the_settings_once_and_never_deletes_them(
    f_load_settings_admin: LoadSettingsAdmin,
    f_owner_request: HttpRequest,
) -> None:
    assert (
        f_load_settings_admin.has_add_permission(f_owner_request),
        f_load_settings_admin.has_delete_permission(f_owner_request),
    ) == (True, False)


@pytest.mark.usefixtures('stored_load_settings')
def test_load_settings_admin_adds_no_second_settings(
    f_load_settings_admin: LoadSettingsAdmin,
    f_owner_request: HttpRequest,
) -> None:
    assert f_load_settings_admin.has_add_permission(f_owner_request) is False
