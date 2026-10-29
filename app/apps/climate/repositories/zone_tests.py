from datetime import UTC, datetime, time, timedelta
from decimal import Decimal

import pytest
from django.db import IntegrityError

from apps.climate.domain.errors import ZoneNotFoundError
from apps.climate.domain.modes import ZoneKind
from apps.climate.domain.zone import ComfortWindow, FloorSetpoints, ZoneSettings
from apps.climate.factories import ComfortPeriodFactory, ZoneFactory
from apps.climate.models import Zone
from apps.climate.repositories.zone import ZoneRepository

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 10, 29, 6, 0, tzinfo=UTC)


@pytest.fixture
def f_zone() -> Zone:
    zone = ZoneFactory.create(slug='kitchen', name='Kitchen', thermostat__slug='thermostat-kitchen')
    ComfortPeriodFactory(zone=zone, weekday=0, start=time(6, 30), end=time(23, 30))
    return zone


@pytest.fixture
def f_commanded_zone(f_zone: Zone) -> Zone:
    Zone.objects.filter(pk=f_zone.pk).update(
        last_command_at=NOW - timedelta(minutes=5),
        last_dry_run_at=NOW - timedelta(minutes=5),
    )
    return f_zone


def test_all_settings_maps_zone(f_zone: Zone) -> None:
    assert ZoneRepository.all_settings() == [
        ZoneSettings(
            slug=f_zone.slug,
            name='Kitchen',
            kind=ZoneKind.AIR,
            thermostat_slug='thermostat-kitchen',
            heating_enabled=True,
            live_control=False,
            setpoints=FloorSetpoints(comfort=Decimal('28.0'), night=Decimal('22.0'), boost=Decimal('30.0')),
            preheat_lead=timedelta(minutes=180),
            windows=(ComfortWindow(weekday=0, start=time(6, 30), end=time(23, 30)),),
            boost_until=None,
            last_command_at=None,
            last_dry_run_at=None,
        ),
    ]


def test_mark_commanded_stores_time(f_zone: Zone) -> None:
    ZoneRepository.mark_commanded(f_zone.slug, NOW)

    assert Zone.objects.get(pk=f_zone.pk).last_command_at == NOW


def test_mark_dry_run_stores_time(f_zone: Zone) -> None:
    ZoneRepository.mark_dry_run(f_zone.slug, NOW)

    zone = Zone.objects.get(pk=f_zone.pk)
    assert (zone.last_dry_run_at, zone.last_command_at) == (NOW, None)


def test_boost_sets_until_and_clears_write_stamps(f_commanded_zone: Zone) -> None:
    ZoneRepository.boost(f_commanded_zone.slug, NOW + timedelta(hours=2))

    zone = Zone.objects.get(pk=f_commanded_zone.pk)
    assert (zone.boost_until, zone.last_command_at, zone.last_dry_run_at) == (NOW + timedelta(hours=2), None, None)


def test_boost_unknown_zone_raises() -> None:
    with pytest.raises(ZoneNotFoundError):
        ZoneRepository.boost('attic', NOW)


def test_comfort_period_must_end_after_start(f_zone: Zone) -> None:
    with pytest.raises(IntegrityError):
        ComfortPeriodFactory(zone=f_zone, weekday=1, start=time(23, 0), end=time(6, 0))
