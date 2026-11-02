from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from apps.core.stubs import MonotonicClockStub
from apps.devices.domain.errors import DeviceUnavailableError
from apps.devices.domain.readings import Phase, PhaseCurrents
from apps.devices.drivers.esphome_phase_meter import EsphomePhaseMeterDriver
from apps.devices.services.phase_meter_store import PhaseMeterStore

NOW = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
CURRENTS = {Phase.A: Decimal('12.34'), Phase.B: Decimal('4.10'), Phase.C: Decimal('0.52')}


@pytest.fixture
def store() -> PhaseMeterStore:
    clock = MonotonicClockStub(now=1000.0)
    store = PhaseMeterStore(stale_after=timedelta(seconds=10), unavailable_after=timedelta(minutes=10), clock=clock)
    for phase, amps in CURRENTS.items():
        store.record_current('phase-meter-lagging', phase, amps, NOW)
    clock.advance(11)
    for phase, amps in CURRENTS.items():
        store.record_current('phase-meter', phase, amps, NOW)
    store.record_current('phase-meter-two-clamps', Phase.A, CURRENTS[Phase.A], NOW)
    store.record_current('phase-meter-two-clamps', Phase.B, CURRENTS[Phase.B], NOW)
    store.record_failure('phase-meter-failed', error_code='broker_unavailable', error='mqtt broker unreachable')
    return store


@pytest.fixture
def f_driver(store: PhaseMeterStore) -> EsphomePhaseMeterDriver:
    return EsphomePhaseMeterDriver(slug='phase-meter', store=store)


@pytest.fixture
def f_stale_driver(store: PhaseMeterStore, request: pytest.FixtureRequest) -> EsphomePhaseMeterDriver:
    return EsphomePhaseMeterDriver(slug=request.param, store=store)


def test_read_gives_the_currents_of_every_phase(f_driver: EsphomePhaseMeterDriver) -> None:
    assert f_driver.read_currents() == PhaseCurrents(amps=CURRENTS, read_at=NOW)


@pytest.mark.parametrize(
    ('f_stale_driver', 'detail'),
    [
        ('phase-meter-lagging', 'phase meter stale'),
        ('phase-meter-two-clamps', 'phase meter stale'),
        ('phase-meter-failed', 'mqtt broker unreachable'),
    ],
    ids=['older-than-10-s', 'phase-missing', 'failed-without-currents'],
    indirect=['f_stale_driver'],
)
def test_read_of_a_stale_meter_raises(f_stale_driver: EsphomePhaseMeterDriver, detail: str) -> None:
    with pytest.raises(DeviceUnavailableError) as raised:
        f_stale_driver.read_currents()

    assert (raised.value.error_code, raised.value.detail) == ('device_unavailable', detail)
