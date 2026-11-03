import pytest
from django.contrib import admin
from django.contrib.auth.models import User
from django.http import HttpRequest
from django.test import RequestFactory

from apps.climate.factories import ZoneFactory
from apps.climate.models import Zone
from apps.devices.admin import DeviceAdmin
from apps.devices.domain.kinds import DeviceDriver, DeviceKind
from apps.devices.factories import DeviceFactory
from apps.devices.models import Device

pytestmark = pytest.mark.django_db


@pytest.fixture
def f_device_admin() -> DeviceAdmin:
    return DeviceAdmin(Device, admin.site)


@pytest.fixture
def f_request() -> HttpRequest:
    request = RequestFactory().get('/admin/devices/device/')
    request.user = User(username='owner', is_staff=True, is_superuser=True)
    return request


@pytest.fixture
def f_kitchen() -> Zone:
    return ZoneFactory.create(
        slug='kitchen',
        thermostat__slug='thermostat-kitchen',
        air_sensor=DeviceFactory.create(
            slug='air-kitchen',
            kind=DeviceKind.TEMPERATURE_SENSOR,
            driver=DeviceDriver.ESPHOME_TEMPERATURE,
        ),
    )


@pytest.fixture
def f_oven() -> Device:
    return DeviceFactory.create(
        slug='plug-oven',
        kind=DeviceKind.PLUG,
        driver=DeviceDriver.TUYA_PLUG,
        monitor_only=True,
    )


def test_room_is_read_only_for_a_device_that_serves_a_zone(
    f_device_admin: DeviceAdmin,
    f_request: HttpRequest,
    f_kitchen: Zone,
) -> None:
    devices = (f_kitchen.thermostat, f_kitchen.air_sensor)

    assert [
        (
            'room' in f_device_admin.get_readonly_fields(f_request, device),
            'room' in f_device_admin.get_form(f_request, device, change=True).base_fields,
        )
        for device in devices
    ] == [(True, False), (True, False)]


def test_room_is_editable_for_a_plug(f_device_admin: DeviceAdmin, f_request: HttpRequest, f_oven: Device) -> None:
    form = f_device_admin.get_form(f_request, f_oven, change=True)

    assert (
        'room' in f_device_admin.get_readonly_fields(f_request, f_oven),
        'room' in f_device_admin.get_readonly_fields(f_request),
        'room' in form.base_fields,
    ) == (False, False, True)
