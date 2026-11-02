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
from apps.panel.usecases.get_phase_currents import (
    GetPhaseCurrentsInputDTO,
    GetPhaseCurrentsOutputDTO,
    GetPhaseCurrentsUseCase,
    PhaseCurrent,
    PhaseMeterOverview,
)
from apps.power.domain.ev_detection import EvWatch
from apps.power.domain.guard import GuardLoads, GuardState
from apps.power.domain.loads import LoadPhase
from apps.power.factories import LoadSettingsFactory
from apps.power.repositories.load_settings import LoadSettingsRepository
from apps.power.services.guard_book import GuardBook

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
GUARD_AT = 1000.0


@pytest.fixture
def monotonic() -> MonotonicClockStub:
    return MonotonicClockStub(now=1000.0)


@pytest.fixture
def store(monotonic: MonotonicClockStub) -> PhaseMeterStore:
    return PhaseMeterStore(stale_after=timedelta(seconds=10), unavailable_after=timedelta(minutes=10), clock=monotonic)


@pytest.fixture
def f_use_case(store: PhaseMeterStore, monotonic: MonotonicClockStub) -> GetPhaseCurrentsUseCase:
    DeviceFactory.create(slug='phase-meter', kind=DeviceKind.PHASE_METER, driver=DeviceDriver.ESPHOME_PHASE_METER)
    store.record_current('phase-meter', Phase.B, Decimal('4.10'), NOW)
    monotonic.advance(1)
    store.record_current('phase-meter', Phase.A, Decimal('12.34'), NOW)
    store.record_current('phase-meter', Phase.C, Decimal('0.52'), NOW)
    monotonic.advance(1.5)
    return GetPhaseCurrentsUseCase(
        devices=DeviceRepository(),
        store=store,
        load_settings=LoadSettingsRepository(),
        guard_book=GuardBook(),
    )


@pytest.fixture
def f_guarded_use_case(store: PhaseMeterStore, monotonic: MonotonicClockStub) -> GetPhaseCurrentsUseCase:
    DeviceFactory.create(slug='phase-meter', kind=DeviceKind.PHASE_METER, driver=DeviceDriver.ESPHOME_PHASE_METER)
    for phase, amps in ((Phase.A, '12.34'), (Phase.B, '4.10'), (Phase.C, '22.52')):
        store.record_current('phase-meter', phase, Decimal(amps), NOW)
    monotonic.advance(1)
    LoadSettingsFactory.create(start_ceiling_amps=Decimal('21.0'), shed_amps=Decimal('26.0'))
    guard_loads = GuardLoads(
        records=(),
        control=LoadSettingsRepository.control(),
        live_zones=frozenset(),
        meter='phase-meter',
        read_at=GUARD_AT,
    )
    charging = EvWatch(
        phase=LoadPhase.L3,
        since=GUARD_AT - 400,
        until=GUARD_AT - 390,
        before=Decimal('6.4'),
        step=Decimal('16.1'),
        charging=True,
    )
    rising = EvWatch(
        phase=LoadPhase.L1,
        since=GUARD_AT - 20,
        until=GUARD_AT - 10,
        before=Decimal('1.2'),
        step=Decimal('11.1'),
        charging=False,
    )
    guard_book = GuardBook()
    guard_book.remember(replace(GuardState.started(guard_loads, GUARD_AT), ev=(rising, charging)))
    return GetPhaseCurrentsUseCase(
        devices=DeviceRepository(),
        store=store,
        load_settings=LoadSettingsRepository(),
        guard_book=guard_book,
    )


@pytest.fixture
def f_silent_phase_use_case(store: PhaseMeterStore, monotonic: MonotonicClockStub) -> GetPhaseCurrentsUseCase:
    DeviceFactory.create(slug='phase-meter', kind=DeviceKind.PHASE_METER, driver=DeviceDriver.ESPHOME_PHASE_METER)
    for phase, amps in ((Phase.A, '12.34'), (Phase.B, '4.10'), (Phase.C, '0.52')):
        store.record_current('phase-meter', phase, Decimal(amps), NOW)
    monotonic.advance(11)
    store.record_current('phase-meter', Phase.A, Decimal('12.40'), NOW + timedelta(seconds=11))
    store.record_current('phase-meter', Phase.B, Decimal('4.05'), NOW + timedelta(seconds=11))
    store.record_failure('phase-meter', error_code='sensor_reading_failed', error='l3_current: the node reported nan')
    return GetPhaseCurrentsUseCase(
        devices=DeviceRepository(),
        store=store,
        load_settings=LoadSettingsRepository(),
        guard_book=GuardBook(),
    )


@pytest.fixture
def f_unheard_use_case(store: PhaseMeterStore) -> GetPhaseCurrentsUseCase:
    DeviceFactory.create(slug='phase-meter', kind=DeviceKind.PHASE_METER, driver=DeviceDriver.ESPHOME_PHASE_METER)
    return GetPhaseCurrentsUseCase(
        devices=DeviceRepository(),
        store=store,
        load_settings=LoadSettingsRepository(),
        guard_book=GuardBook(),
    )


@pytest.fixture
def f_meterless_use_case(store: PhaseMeterStore) -> GetPhaseCurrentsUseCase:
    DeviceFactory.create(slug='thermostat-kitchen')
    return GetPhaseCurrentsUseCase(
        devices=DeviceRepository(),
        store=store,
        load_settings=LoadSettingsRepository(),
        guard_book=GuardBook(),
    )


def test_phase_currents_give_every_phase_with_its_age(f_use_case: GetPhaseCurrentsUseCase) -> None:
    output = f_use_case.execute(GetPhaseCurrentsInputDTO())

    assert output == GetPhaseCurrentsOutputDTO(
        meter=PhaseMeterOverview(
            stale=False,
            error_code=None,
            error=None,
            phases=(
                PhaseCurrent(phase='l1', amps=Decimal('12.34'), stale=False, age_seconds=1),
                PhaseCurrent(phase='l2', amps=Decimal('4.10'), stale=False, age_seconds=2),
                PhaseCurrent(phase='l3', amps=Decimal('0.52'), stale=False, age_seconds=1),
            ),
        ),
        scale_amps=Decimal(25),
        start_ceiling_amps=Decimal(20),
        shed_amps=Decimal(24),
    )


def test_phase_silent_for_over_10_seconds_makes_the_meter_stale(
    f_silent_phase_use_case: GetPhaseCurrentsUseCase,
) -> None:
    output = f_silent_phase_use_case.execute(GetPhaseCurrentsInputDTO())

    assert output.meter == PhaseMeterOverview(
        stale=True,
        error_code='sensor_reading_failed',
        error='l3_current: the node reported nan',
        phases=(
            PhaseCurrent(phase='l1', amps=Decimal('12.40'), stale=False, age_seconds=0),
            PhaseCurrent(phase='l2', amps=Decimal('4.05'), stale=False, age_seconds=0),
            PhaseCurrent(phase='l3', amps=Decimal('0.52'), stale=True, age_seconds=11),
        ),
    )


def test_meter_not_heard_since_the_start_is_stale_without_currents(
    f_unheard_use_case: GetPhaseCurrentsUseCase,
) -> None:
    output = f_unheard_use_case.execute(GetPhaseCurrentsInputDTO())

    assert output.meter == PhaseMeterOverview(
        stale=True,
        error_code=None,
        error=None,
        phases=(
            PhaseCurrent(phase='l1', amps=None, stale=True, age_seconds=None),
            PhaseCurrent(phase='l2', amps=None, stale=True, age_seconds=None),
            PhaseCurrent(phase='l3', amps=None, stale=True, age_seconds=None),
        ),
    )


def test_house_without_a_phase_meter_has_no_meter(f_meterless_use_case: GetPhaseCurrentsUseCase) -> None:
    assert f_meterless_use_case.execute(GetPhaseCurrentsInputDTO()).meter is None


def test_phase_bars_mark_the_thresholds_of_the_load_settings_and_an_ev_charging(
    f_guarded_use_case: GetPhaseCurrentsUseCase,
) -> None:
    output = f_guarded_use_case.execute(GetPhaseCurrentsInputDTO())

    assert output == GetPhaseCurrentsOutputDTO(
        meter=PhaseMeterOverview(
            stale=False,
            error_code=None,
            error=None,
            phases=(
                PhaseCurrent(phase='l1', amps=Decimal('12.34'), stale=False, age_seconds=1, ev_amps=None),
                PhaseCurrent(phase='l2', amps=Decimal('4.10'), stale=False, age_seconds=1, ev_amps=None),
                PhaseCurrent(phase='l3', amps=Decimal('22.52'), stale=False, age_seconds=1, ev_amps=Decimal('16.1')),
            ),
        ),
        scale_amps=Decimal(27),
        start_ceiling_amps=Decimal('21.0'),
        shed_amps=Decimal('26.0'),
    )
