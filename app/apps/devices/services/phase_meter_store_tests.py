from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from apps.core.stubs import MonotonicClockStub
from apps.devices.domain.readings import Phase, PhaseCurrents
from apps.devices.services.phase_meter_store import CurrentStatistics, PhaseMeterSnapshot, PhaseMeterStore

NOW = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
SECOND = timedelta(seconds=1)


@pytest.fixture
def clock() -> MonotonicClockStub:
    return MonotonicClockStub(now=1000.0)


@pytest.fixture
def f_store(clock: MonotonicClockStub) -> PhaseMeterStore:
    store = PhaseMeterStore(stale_after=timedelta(seconds=10), unavailable_after=timedelta(minutes=10), clock=clock)
    store.record_current('phase-meter', Phase.B, Decimal('4.10'), NOW)
    clock.advance(1)
    store.record_current('phase-meter', Phase.A, Decimal('12.34'), NOW + SECOND)
    store.record_current('phase-meter', Phase.C, Decimal('0.52'), NOW + SECOND)
    clock.advance(2)
    return store


@pytest.fixture
def f_aged_store(
    f_store: PhaseMeterStore,
    clock: MonotonicClockStub,
    request: pytest.FixtureRequest,
) -> PhaseMeterStore:
    clock.advance(request.param)
    return f_store


@pytest.fixture
def f_two_phase_store(clock: MonotonicClockStub) -> PhaseMeterStore:
    store = PhaseMeterStore(stale_after=timedelta(seconds=10), unavailable_after=timedelta(minutes=10), clock=clock)
    store.record_current('phase-meter', Phase.A, Decimal('12.34'), NOW)
    store.record_current('phase-meter', Phase.C, Decimal('0.52'), NOW)
    return store


@pytest.fixture
def f_failed_store(f_store: PhaseMeterStore) -> PhaseMeterStore:
    f_store.record_failure('phase-meter', error_code='sensor_reading_failed', error='l2_current: the node reported nan')
    return f_store


@pytest.fixture
def f_recovered_store(f_failed_store: PhaseMeterStore) -> PhaseMeterStore:
    f_failed_store.record_current('phase-meter', Phase.B, Decimal('4.20'), NOW + 3 * SECOND)
    return f_failed_store


@pytest.fixture
def f_busy_store(clock: MonotonicClockStub) -> PhaseMeterStore:
    store = PhaseMeterStore(stale_after=timedelta(seconds=10), unavailable_after=timedelta(minutes=10), clock=clock)
    for amps in ('10.00', '12.00', '11.00'):
        store.record_current('phase-meter', Phase.A, Decimal(amps), NOW)
    for amps in ('1.00', '1.00', '1.01'):
        store.record_current('phase-meter', Phase.B, Decimal(amps), NOW)
    return store


@pytest.fixture
def f_captured_store(f_busy_store: PhaseMeterStore) -> PhaseMeterStore:
    f_busy_store.capture_statistics('phase-meter')
    f_busy_store.record_current('phase-meter', Phase.A, Decimal('5.5'), NOW)
    return f_busy_store


@pytest.fixture
def f_reported_store(f_store: PhaseMeterStore) -> PhaseMeterStore:
    f_store.mark_reported('phase-meter', True)
    return f_store


def test_snapshot_holds_the_latest_current_of_every_phase(f_store: PhaseMeterStore) -> None:
    assert f_store.snapshot('phase-meter') == PhaseMeterSnapshot(
        slug='phase-meter',
        currents=PhaseCurrents(
            amps={Phase.A: Decimal('12.34'), Phase.B: Decimal('4.10'), Phase.C: Decimal('0.52')},
            read_at=NOW,
        ),
        ages={Phase.A: 2 * SECOND, Phase.B: 3 * SECOND, Phase.C: 2 * SECOND},
        age=2 * SECOND,
        available=True,
        stale=False,
        stale_phases=frozenset(),
        error_code=None,
        error=None,
    )


@pytest.mark.parametrize(
    ('f_aged_store', 'stale', 'stale_phases'),
    [(7.0, False, frozenset()), (7.5, True, frozenset({Phase.B}))],
    ids=['phase-b-10-s-old', 'phase-b-older-than-10-s'],
    indirect=['f_aged_store'],
)
def test_meter_is_stale_once_a_phase_is_older_than_10_seconds(
    f_aged_store: PhaseMeterStore,
    stale: bool,
    stale_phases: frozenset[Phase],
) -> None:
    snapshot = f_aged_store.snapshot('phase-meter')

    assert (snapshot.stale, snapshot.stale_phases, snapshot.available) == (stale, stale_phases, True)


def test_meter_without_a_phase_is_stale(f_two_phase_store: PhaseMeterStore) -> None:
    snapshot = f_two_phase_store.snapshot('phase-meter')

    assert (snapshot.stale, snapshot.stale_phases, snapshot.currents, snapshot.ages) == (
        True,
        frozenset({Phase.B}),
        PhaseCurrents(amps={Phase.A: Decimal('12.34'), Phase.C: Decimal('0.52')}, read_at=NOW),
        {Phase.A: timedelta(0), Phase.C: timedelta(0)},
    )


@pytest.mark.parametrize(
    ('f_aged_store', 'available'),
    [(598.0, True), (598.5, False)],
    ids=['newest-10-min-old', 'newest-older-than-10-min'],
    indirect=['f_aged_store'],
)
def test_meter_is_unavailable_10_minutes_after_its_newest_current(
    f_aged_store: PhaseMeterStore,
    available: bool,
) -> None:
    snapshot = f_aged_store.snapshot('phase-meter')

    assert (snapshot.available, snapshot.stale) == (available, True)


def test_failure_keeps_the_last_currents(f_failed_store: PhaseMeterStore) -> None:
    snapshot = f_failed_store.snapshot('phase-meter')

    assert (snapshot.currents, snapshot.stale, snapshot.error_code, snapshot.error) == (
        PhaseCurrents(
            amps={Phase.A: Decimal('12.34'), Phase.B: Decimal('4.10'), Phase.C: Decimal('0.52')},
            read_at=NOW,
        ),
        False,
        'sensor_reading_failed',
        'l2_current: the node reported nan',
    )


def test_current_clears_the_failure(f_recovered_store: PhaseMeterStore) -> None:
    snapshot = f_recovered_store.snapshot('phase-meter')

    assert (snapshot.error_code, snapshot.error, snapshot.ages[Phase.B]) == (None, None, timedelta(0))


def test_statistics_give_mean_and_peak_per_phase(f_busy_store: PhaseMeterStore) -> None:
    assert f_busy_store.capture_statistics('phase-meter') == {
        Phase.A: CurrentStatistics(mean=Decimal('11.00'), peak=Decimal('12.00'), count=3),
        Phase.B: CurrentStatistics(mean=Decimal('1.00'), peak=Decimal('1.01'), count=3),
    }


def test_statistics_start_over_after_a_capture(f_captured_store: PhaseMeterStore) -> None:
    assert (f_captured_store.capture_statistics('phase-meter'), f_captured_store.capture_statistics('phase-meter')) == (
        {Phase.A: CurrentStatistics(mean=Decimal('5.50'), peak=Decimal('5.5'), count=1)},
        {},
    )


def test_unknown_meter_is_unavailable_and_stale(f_store: PhaseMeterStore) -> None:
    assert f_store.snapshot('phase-meter-2') == PhaseMeterSnapshot(
        slug='phase-meter-2',
        currents=None,
        ages={},
        age=None,
        available=False,
        stale=True,
        stale_phases=frozenset(Phase),
        error_code=None,
        error=None,
    )


def test_reported_availability_is_remembered(f_reported_store: PhaseMeterStore) -> None:
    assert (f_reported_store.last_reported('phase-meter'), f_reported_store.last_reported('phase-meter-2')) == (
        True,
        None,
    )
