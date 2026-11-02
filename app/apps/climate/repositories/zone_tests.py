from datetime import UTC, datetime, time, timedelta
from decimal import Decimal

import pytest
from django.db import IntegrityError

from apps.climate.domain.errors import ZoneNotFoundError
from apps.climate.domain.modes import ZoneKind
from apps.climate.domain.zone import AirTargets, ComfortWindow, FloorSetpoints, ZoneSettings
from apps.climate.factories import ComfortPeriodFactory, ZoneFactory
from apps.climate.models import Zone
from apps.climate.repositories.zone import ZoneRepository
from apps.devices.domain.kinds import DeviceDriver, DeviceKind
from apps.devices.factories import DeviceFactory

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 10, 29, 6, 0, tzinfo=UTC)


@pytest.fixture
def f_zone() -> Zone:
    zone = ZoneFactory.create(slug='kitchen', name='Kitchen', thermostat__slug='thermostat-kitchen')
    ComfortPeriodFactory(zone=zone, weekday=0, start=time(6, 30), end=time(23, 30))
    return zone


@pytest.fixture
def f_air_zones() -> list[Zone]:
    sensor = DeviceFactory.create(
        slug='air-living-room',
        kind=DeviceKind.TEMPERATURE_SENSOR,
        driver=DeviceDriver.ESPHOME_TEMPERATURE,
    )
    return [
        ZoneFactory.create(slug='living-room', air_sensor=sensor),
        ZoneFactory.create(slug='bathroom-1f'),
    ]


@pytest.fixture
def f_air_controlled_zone() -> Zone:
    return ZoneFactory.create(
        slug='living-room',
        air_control_live=True,
        comfort_air_target=Decimal('21.5'),
        night_air_target=Decimal('19.0'),
        idle_floor_setpoint=Decimal('17.0'),
        floor_limit_setpoint=Decimal('29.0'),
        last_air_dry_run_at=NOW,
    )


@pytest.fixture
def f_commanded_zone(f_zone: Zone) -> Zone:
    Zone.objects.filter(pk=f_zone.pk).update(
        last_command_at=NOW - timedelta(minutes=5),
        last_dry_run_at=NOW - timedelta(minutes=5),
        last_air_dry_run_at=NOW - timedelta(minutes=5),
    )
    return f_zone


def test_all_settings_maps_zone(f_zone: Zone) -> None:
    assert ZoneRepository.all_settings() == [
        ZoneSettings(
            slug=f_zone.slug,
            name='Kitchen',
            kind=ZoneKind.AIR,
            thermostat_slug='thermostat-kitchen',
            air_sensor_slug=None,
            heating_enabled=True,
            live_control=False,
            air_control_live=False,
            setpoints=FloorSetpoints(
                comfort=Decimal('28.0'),
                night=Decimal('22.0'),
                boost=Decimal('30.0'),
                idle=Decimal('18.0'),
                limit=Decimal('30.0'),
            ),
            air_targets=AirTargets(comfort=Decimal('22.0'), night=Decimal('19.5')),
            preheat_lead=timedelta(minutes=180),
            windows=(ComfortWindow(weekday=0, start=time(6, 30), end=time(23, 30)),),
            boost_until=None,
            last_command_at=None,
            last_dry_run_at=None,
            last_air_dry_run_at=None,
        ),
    ]


def test_all_settings_names_the_air_sensor_of_a_zone_that_has_one(f_air_zones: list[Zone]) -> None:
    assert [(zone.slug, zone.air_sensor_slug) for zone in ZoneRepository.all_settings()] == [
        (f_air_zones[1].slug, None),
        (f_air_zones[0].slug, 'air-living-room'),
    ]


def test_all_settings_maps_air_control(f_air_controlled_zone: Zone) -> None:
    [zone] = ZoneRepository.all_settings()

    assert (zone.slug, zone.air_control_live, zone.air_targets, zone.setpoints, zone.last_air_dry_run_at) == (
        f_air_controlled_zone.slug,
        True,
        AirTargets(comfort=Decimal('21.5'), night=Decimal('19.0')),
        FloorSetpoints(
            comfort=Decimal('28.0'),
            night=Decimal('22.0'),
            boost=Decimal('30.0'),
            idle=Decimal('17.0'),
            limit=Decimal('29.0'),
        ),
        NOW,
    )


def test_mark_commanded_stores_time(f_zone: Zone) -> None:
    ZoneRepository.mark_commanded(f_zone.slug, NOW)

    assert Zone.objects.get(pk=f_zone.pk).last_command_at == NOW


def test_mark_dry_run_stores_time(f_zone: Zone) -> None:
    ZoneRepository.mark_dry_run(f_zone.slug, NOW)

    zone = Zone.objects.get(pk=f_zone.pk)
    assert (zone.last_dry_run_at, zone.last_command_at) == (NOW, None)


def test_mark_air_dry_run_stores_time_apart_from_write_stamps(f_zone: Zone) -> None:
    ZoneRepository.mark_air_dry_run(f_zone.slug, NOW)

    zone = Zone.objects.get(pk=f_zone.pk)
    assert (zone.last_air_dry_run_at, zone.last_dry_run_at, zone.last_command_at) == (NOW, None, None)


def test_boost_sets_until_and_clears_write_stamps(f_commanded_zone: Zone) -> None:
    ZoneRepository.boost(f_commanded_zone.slug, NOW + timedelta(hours=2))

    zone = Zone.objects.get(pk=f_commanded_zone.pk)
    assert (zone.boost_until, zone.last_command_at, zone.last_dry_run_at, zone.last_air_dry_run_at) == (
        NOW + timedelta(hours=2),
        None,
        None,
        None,
    )


def test_boost_unknown_zone_raises() -> None:
    with pytest.raises(ZoneNotFoundError):
        ZoneRepository.boost('attic', NOW)


def test_comfort_period_must_end_after_start(f_zone: Zone) -> None:
    with pytest.raises(IntegrityError):
        ComfortPeriodFactory(zone=f_zone, weekday=1, start=time(23, 0), end=time(6, 0))
