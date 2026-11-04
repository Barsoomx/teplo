from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from apps.core.stubs import MonotonicClockStub
from apps.devices.domain.kinds import DeviceDriver, DeviceKind
from apps.devices.domain.readings import Phase
from apps.devices.factories import DeviceFactory
from apps.devices.repositories.device import DeviceRepository
from apps.devices.services.phase_meter_store import PhaseMeterStore
from apps.notifications.domain.alerts import PhaseReading
from apps.notifications.services.phase_readout import PhaseMeterReadout
from apps.power.domain.estimate import EstimateBasis, EstimatePart, PhaseEstimate
from apps.power.domain.guard import GuardLoads, GuardState
from apps.power.domain.loads import LoadPhase
from apps.power.repositories.load_settings import LoadSettingsRepository
from apps.power.services.guard_book import GuardBook

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 11, 4, 13, 40, tzinfo=UTC)
GUARD_AT = 1000.0
L2_ESTIMATE = PhaseEstimate(
    phase=LoadPhase.L2,
    amps=Decimal('12.42'),
    parts=(
        EstimatePart(load=None, amps=Decimal('2.0'), basis=EstimateBasis.BASE),
        EstimatePart(load='boiler-2f', amps=Decimal('6.42'), basis=EstimateBasis.MEASURED),
        EstimatePart(load='bathroom-1f', amps=Decimal('4.0'), basis=EstimateBasis.DRAWING),
    ),
)


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
    return PhaseMeterReadout(devices=DeviceRepository(), store=store, book=GuardBook())


@pytest.fixture
def estimating_book() -> GuardBook:
    loads = GuardLoads(
        records=(),
        control=LoadSettingsRepository.control(),
        live_zones=frozenset(),
        meter='phase-meter',
        read_at=GUARD_AT,
    )
    started = GuardState.started(loads, GUARD_AT)
    book = GuardBook()
    book.remember(replace(started, phases=replace(started.phases, estimate=L2_ESTIMATE)))
    return book


@pytest.fixture
def f_estimating_readout(
    store: PhaseMeterStore,
    clock: MonotonicClockStub,
    estimating_book: GuardBook,
) -> PhaseMeterReadout:
    DeviceFactory.create(slug='phase-meter', kind=DeviceKind.PHASE_METER, driver=DeviceDriver.ESPHOME_PHASE_METER)
    for phase, amps in ((Phase.A, '12.43'), (Phase.B, '0.21'), (Phase.C, '3.02')):
        store.record_current('phase-meter', phase, Decimal(amps), NOW)
    clock.advance(1)
    return PhaseMeterReadout(devices=DeviceRepository(), store=store, book=estimating_book)


@pytest.fixture
def f_estimating_readout_whose_l2_went_silent(
    store: PhaseMeterStore,
    clock: MonotonicClockStub,
    estimating_book: GuardBook,
) -> PhaseMeterReadout:
    DeviceFactory.create(slug='phase-meter', kind=DeviceKind.PHASE_METER, driver=DeviceDriver.ESPHOME_PHASE_METER)
    store.record_current('phase-meter', Phase.B, Decimal('0.21'), NOW)
    clock.advance(13)
    store.record_current('phase-meter', Phase.A, Decimal('12.43'), NOW + timedelta(seconds=13))
    store.record_current('phase-meter', Phase.C, Decimal('3.02'), NOW + timedelta(seconds=13))
    return PhaseMeterReadout(devices=DeviceRepository(), store=store, book=estimating_book)


@pytest.fixture
def f_meterless_readout(store: PhaseMeterStore) -> PhaseMeterReadout:
    DeviceFactory.create(
        slug='air-kitchen',
        kind=DeviceKind.TEMPERATURE_SENSOR,
        driver=DeviceDriver.ESPHOME_TEMPERATURE,
    )
    return PhaseMeterReadout(devices=DeviceRepository(), store=store, book=GuardBook())


def test_phases_give_each_fresh_current_and_leave_a_stale_or_silent_phase_empty(f_readout: PhaseMeterReadout) -> None:
    assert f_readout.phases() == (
        PhaseReading(phase='l1', amps=Decimal('12.43'), estimated=False),
        PhaseReading(phase='l2', amps=None, estimated=False),
        PhaseReading(phase='l3', amps=None, estimated=False),
    )


def test_estimated_phase_gives_the_estimate_of_the_guard(f_estimating_readout: PhaseMeterReadout) -> None:
    assert f_estimating_readout.phases() == (
        PhaseReading(phase='l1', amps=Decimal('12.43'), estimated=False),
        PhaseReading(phase='l2', amps=Decimal('12.42'), estimated=True),
        PhaseReading(phase='l3', amps=Decimal('3.02'), estimated=False),
    )


def test_estimated_phase_that_went_silent_stays_empty(
    f_estimating_readout_whose_l2_went_silent: PhaseMeterReadout,
) -> None:
    assert f_estimating_readout_whose_l2_went_silent.phases() == (
        PhaseReading(phase='l1', amps=Decimal('12.43'), estimated=False),
        PhaseReading(phase='l2', amps=None, estimated=False),
        PhaseReading(phase='l3', amps=Decimal('3.02'), estimated=False),
    )


def test_phases_are_unknown_without_a_phase_meter(f_meterless_readout: PhaseMeterReadout) -> None:
    assert f_meterless_readout.phases() is None
