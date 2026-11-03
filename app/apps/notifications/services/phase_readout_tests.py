from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from apps.core.stubs import MonotonicClockStub
from apps.devices.domain.kinds import DeviceDriver, DeviceKind
from apps.devices.domain.readings import Phase
from apps.devices.factories import DeviceFactory
from apps.devices.repositories.device import DeviceRepository
from apps.devices.services.phase_meter_store import PhaseMeterStore
from apps.notifications.services.phase_readout import PhaseMeterReadout

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 11, 4, 13, 40, tzinfo=UTC)


@pytest.fixture
def clock() -> MonotonicClockStub:
    return MonotonicClockStub(now=0.0)


@pytest.fixture
def store(clock: MonotonicClockStub) -> PhaseMeterStore:
    return PhaseMeterStore(stale_after=timedelta(seconds=10), unavailable_after=timedelta(minutes=10), clock=clock)


@pytest.fixture
def f_readout(store: PhaseMeterStore, clock: MonotonicClockStub) -> PhaseMeterReadout:
    DeviceFactory.create(slug='phase-meter', kind=DeviceKind.PHASE_METER, driver=DeviceDriver.ESPHOME_PHASE_METER)
    store.record_current('phase-meter', Phase.B, Decimal('4.10'), NOW)
    clock.advance(13)
    store.record_current('phase-meter', Phase.A, Decimal('12.43'), NOW + timedelta(seconds=13))
    clock.advance(2)
    return PhaseMeterReadout(devices=DeviceRepository(), store=store)


@pytest.fixture
def f_meterless_readout(store: PhaseMeterStore) -> PhaseMeterReadout:
    DeviceFactory.create(
        slug='air-kitchen',
        kind=DeviceKind.TEMPERATURE_SENSOR,
        driver=DeviceDriver.ESPHOME_TEMPERATURE,
    )
    return PhaseMeterReadout(devices=DeviceRepository(), store=store)


def test_phases_give_each_fresh_current_and_leave_a_stale_or_silent_phase_empty(f_readout: PhaseMeterReadout) -> None:
    assert f_readout.phases() == (('l1', Decimal('12.43')), ('l2', None), ('l3', None))


def test_phases_are_unknown_without_a_phase_meter(f_meterless_readout: PhaseMeterReadout) -> None:
    assert f_meterless_readout.phases() is None
