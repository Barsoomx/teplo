from datetime import UTC, datetime, timedelta

import pytest

from apps.core.domain.errors import DomainError
from apps.core.stubs import MonotonicClockStub
from apps.devices.domain.readings import PresenceReading
from apps.devices.drivers.esphome_presence import EsphomePresenceDriver
from apps.devices.services.presence_store import PresenceStore

NOW = datetime(2026, 11, 5, 9, 0, tzinfo=UTC)


@pytest.fixture
def store() -> PresenceStore:
    clock = MonotonicClockStub(now=1000.0)
    store = PresenceStore(unavailable_after=timedelta(minutes=10), clock=clock)
    store.record('pc-office', present=True, read_at=NOW)
    store.record('presence-office', present=False, read_at=NOW)
    clock.advance(30)
    store.record('pc-office', present=False, read_at=NOW + timedelta(seconds=30))
    store.record_failure('presence-office', error_code='sensor_reading_failed', error='presence: the node reported nan')
    clock.advance(30)
    return store


@pytest.fixture
def f_driver(store: PresenceStore) -> EsphomePresenceDriver:
    return EsphomePresenceDriver(slug='pc-office', store=store)


@pytest.fixture
def f_unavailable_driver(store: PresenceStore, request: pytest.FixtureRequest) -> EsphomePresenceDriver:
    return EsphomePresenceDriver(slug=request.param, store=store)


def test_read_gives_the_latest_report_with_the_last_sighting_and_the_watch(f_driver: EsphomePresenceDriver) -> None:
    assert f_driver.read_presence() == PresenceReading(
        present=False,
        seen_ago=timedelta(minutes=1),
        watched_for=timedelta(minutes=1),
        read_at=NOW + timedelta(seconds=30),
    )


@pytest.mark.parametrize(
    ('f_unavailable_driver', 'failure'),
    [
        ('presence-office', ('sensor_reading_failed', 'presence: the node reported nan')),
        ('presence-bedroom', ('device_unavailable', 'no recent presence report')),
    ],
    ids=['failed', 'never-heard'],
    indirect=['f_unavailable_driver'],
)
def test_read_of_an_unavailable_source_raises_the_recorded_failure_with_its_code(
    f_unavailable_driver: EsphomePresenceDriver,
    failure: tuple[str, str],
) -> None:
    with pytest.raises(DomainError) as raised:
        f_unavailable_driver.read_presence()

    assert (raised.value.error_code, raised.value.detail) == failure
