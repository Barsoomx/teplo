from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from apps.core.stubs import MonotonicClockStub
from apps.devices.domain.kinds import DeviceDriver, DeviceKind
from apps.devices.domain.readings import Phase
from apps.devices.factories import DeviceFactory
from apps.devices.repositories.device import DeviceRepository
from apps.devices.services.phase_meter_store import PhaseMeterStore
from apps.notifications.domain.alerts import MonitoredDevice
from apps.notifications.services.phase_meter_source import PhaseMeterSource

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 11, 2, 17, 40, tzinfo=UTC)
SECOND = timedelta(seconds=1)


@pytest.fixture
def clock() -> MonotonicClockStub:
    return MonotonicClockStub(now=0.0)


@pytest.fixture
def store(clock: MonotonicClockStub) -> PhaseMeterStore:
    DeviceFactory.create(
        slug='phase-meter',
        name='Фазометр',
        kind=DeviceKind.PHASE_METER,
        driver=DeviceDriver.ESPHOME_PHASE_METER,
    )
    DeviceFactory.create(
        slug='air-kitchen',
        name='Воздух: кухня',
        kind=DeviceKind.TEMPERATURE_SENSOR,
        driver=DeviceDriver.ESPHOME_TEMPERATURE,
    )
    return PhaseMeterStore(stale_after=timedelta(seconds=10), unavailable_after=timedelta(minutes=10), clock=clock)


@pytest.fixture
def f_stale_source(store: PhaseMeterStore, clock: MonotonicClockStub) -> PhaseMeterSource:
    store.record_current('phase-meter', Phase.B, Decimal('4.10'), NOW)
    clock.advance(13)
    store.record_current('phase-meter', Phase.A, Decimal('12.34'), NOW + 13 * SECOND)
    store.record_current('phase-meter', Phase.C, Decimal('0.52'), NOW + 13 * SECOND)
    clock.advance(2)
    return PhaseMeterSource(devices=DeviceRepository(), store=store)


@pytest.fixture
def f_fresh_source(f_stale_source: PhaseMeterSource, store: PhaseMeterStore) -> PhaseMeterSource:
    store.record_current('phase-meter', Phase.B, Decimal('4.20'), NOW + 15 * SECOND)
    return f_stale_source


@pytest.fixture
def f_incomplete_source(store: PhaseMeterStore, clock: MonotonicClockStub) -> PhaseMeterSource:
    store.record_current('phase-meter', Phase.A, Decimal('12.34'), NOW)
    store.record_current('phase-meter', Phase.C, Decimal('0.52'), NOW)
    clock.advance(1)
    return PhaseMeterSource(devices=DeviceRepository(), store=store)


def test_stale_phase_meter_is_lost_since_the_last_current_of_its_oldest_phase(
    f_stale_source: PhaseMeterSource,
) -> None:
    assert f_stale_source.devices() == [
        MonitoredDevice(
            slug='phase-meter',
            name='Фазометр',
            kind=DeviceKind.PHASE_METER,
            available=False,
            last_seen_age=15 * SECOND,
        ),
    ]


def test_phase_meter_with_every_phase_fresh_is_present(f_fresh_source: PhaseMeterSource) -> None:
    assert f_fresh_source.devices() == [
        MonitoredDevice(
            slug='phase-meter',
            name='Фазометр',
            kind=DeviceKind.PHASE_METER,
            available=True,
            last_seen_age=2 * SECOND,
        ),
    ]


def test_phase_meter_missing_a_phase_has_no_reading_to_date_its_loss(f_incomplete_source: PhaseMeterSource) -> None:
    assert f_incomplete_source.devices() == [
        MonitoredDevice(
            slug='phase-meter',
            name='Фазометр',
            kind=DeviceKind.PHASE_METER,
            available=False,
            last_seen_age=None,
        ),
    ]
