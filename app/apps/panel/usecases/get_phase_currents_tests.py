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
    EstimatePartOverview,
    GetPhaseCurrentsInputDTO,
    GetPhaseCurrentsOutputDTO,
    GetPhaseCurrentsUseCase,
    PhaseCurrent,
    PhaseMeterOverview,
)
from apps.power.domain.estimate import EstimateBasis, EstimatePart, PhaseEstimate
from apps.power.domain.ev_detection import EvWatch
from apps.power.domain.guard import GuardLoads, GuardState
from apps.power.domain.loads import LoadKind, LoadPhase, LoadRecord
from apps.power.factories import LoadSettingsFactory
from apps.power.repositories.load_settings import LoadSettingsRepository
from apps.power.services.guard_book import GuardBook

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
GUARD_AT = 1000.0
BOILER_2F = LoadRecord(
    slug='boiler-2f',
    name='Бойлер 2 этаж',
    kind=LoadKind.PLUG,
    zone_slug=None,
    device_slug='plug-boiler-2f',
    priority=2,
    phase=LoadPhase.L2,
    nominal_amps=Decimal('6.0'),
    learned=None,
    live=False,
    shed=None,
)
BATHROOM = LoadRecord(
    slug='bathroom-1f',
    name='Санузел 1 этаж',
    kind=LoadKind.ZONE,
    zone_slug='bathroom-1f',
    device_slug='thermostat-bathroom-1f',
    priority=3,
    phase=LoadPhase.L2,
    nominal_amps=None,
    learned=None,
    live=False,
    shed=None,
)
L2_ESTIMATE = PhaseEstimate(
    phase=LoadPhase.L2,
    amps=Decimal('14.42'),
    parts=(
        EstimatePart(load=None, amps=Decimal('2.0'), basis=EstimateBasis.BASE),
        EstimatePart(load='boiler-2f', amps=Decimal('6.42'), basis=EstimateBasis.MEASURED),
        EstimatePart(load='bathroom-1f', amps=Decimal('6.0'), basis=EstimateBasis.DRAWING),
    ),
)


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


def _estimating_book(store: PhaseMeterStore) -> GuardBook:
    DeviceFactory.create(slug='phase-meter', kind=DeviceKind.PHASE_METER, driver=DeviceDriver.ESPHOME_PHASE_METER)
    guard_loads = GuardLoads(
        records=(BOILER_2F, BATHROOM),
        control=LoadSettingsRepository.control(),
        live_zones=frozenset(),
        meter='phase-meter',
        read_at=GUARD_AT,
    )
    started = GuardState.started(guard_loads, GUARD_AT)
    guard_book = GuardBook()
    guard_book.remember(replace(started, phases=replace(started.phases, estimate=L2_ESTIMATE)))
    for phase, amps in ((Phase.A, '12.34'), (Phase.B, '0.21'), (Phase.C, '0.52')):
        store.record_current('phase-meter', phase, Decimal(amps), NOW)
    return guard_book


@pytest.fixture
def f_estimating_use_case(store: PhaseMeterStore, monotonic: MonotonicClockStub) -> GetPhaseCurrentsUseCase:
    guard_book = _estimating_book(store)
    monotonic.advance(1)
    return GetPhaseCurrentsUseCase(
        devices=DeviceRepository(),
        store=store,
        load_settings=LoadSettingsRepository(),
        guard_book=guard_book,
    )


@pytest.fixture
def f_estimating_use_case_whose_l2_went_silent(
    store: PhaseMeterStore,
    monotonic: MonotonicClockStub,
) -> GetPhaseCurrentsUseCase:
    guard_book = _estimating_book(store)
    monotonic.advance(11)
    store.record_current('phase-meter', Phase.A, Decimal('12.40'), NOW + timedelta(seconds=11))
    store.record_current('phase-meter', Phase.C, Decimal('0.55'), NOW + timedelta(seconds=11))
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


def test_estimated_phase_gives_the_estimate_with_its_parts_by_the_names_of_their_loads(
    f_estimating_use_case: GetPhaseCurrentsUseCase,
) -> None:
    output = f_estimating_use_case.execute(GetPhaseCurrentsInputDTO())

    assert output.meter == PhaseMeterOverview(
        stale=False,
        error_code=None,
        error=None,
        phases=(
            PhaseCurrent(phase='l1', amps=Decimal('12.34'), stale=False, age_seconds=1),
            PhaseCurrent(
                phase='l2',
                amps=Decimal('14.42'),
                stale=False,
                age_seconds=1,
                estimated=True,
                estimate_parts=(
                    EstimatePartOverview(name='base', amps=Decimal('2.0')),
                    EstimatePartOverview(name='Бойлер 2 этаж', amps=Decimal('6.42')),
                    EstimatePartOverview(name='Санузел 1 этаж', amps=Decimal('6.0')),
                ),
            ),
            PhaseCurrent(phase='l3', amps=Decimal('0.52'), stale=False, age_seconds=1),
        ),
    )


def test_estimated_phase_that_went_silent_shows_no_estimate(
    f_estimating_use_case_whose_l2_went_silent: GetPhaseCurrentsUseCase,
) -> None:
    output = f_estimating_use_case_whose_l2_went_silent.execute(GetPhaseCurrentsInputDTO())

    assert output.meter == PhaseMeterOverview(
        stale=True,
        error_code=None,
        error=None,
        phases=(
            PhaseCurrent(phase='l1', amps=Decimal('12.40'), stale=False, age_seconds=0),
            PhaseCurrent(phase='l2', amps=Decimal('0.21'), stale=True, age_seconds=11),
            PhaseCurrent(phase='l3', amps=Decimal('0.55'), stale=False, age_seconds=0),
        ),
    )
