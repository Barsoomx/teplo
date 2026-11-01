from datetime import UTC, datetime, timedelta

import pytest

from apps.telemetry.domain.samples import SampleValue
from apps.telemetry.models import Sample
from apps.telemetry.repositories.sample import RECENT_SAMPLES, SampleRepository

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 10, 29, 6, 0, tzinfo=UTC)
LAGGING_BOOT = NOW - timedelta(hours=5)


def _floor_sample(recorded_at: datetime) -> SampleValue:
    return SampleValue(metric='floor_temperature', subject='thermostat-kitchen', value=24.5, recorded_at=recorded_at)


@pytest.fixture
def f_samples() -> list[SampleValue]:
    return [
        SampleValue(
            metric='floor_temperature',
            subject='thermostat-kitchen',
            value=24.0,
            recorded_at=NOW - timedelta(minutes=1),
        ),
        SampleValue(metric='floor_temperature', subject='thermostat-kitchen', value=24.5, recorded_at=NOW),
    ]


@pytest.fixture
def samples_after_a_boot_on_a_lagging_clock(f_samples: list[SampleValue]) -> None:
    SampleRepository.add_many(f_samples)
    SampleRepository.add_many([_floor_sample(LAGGING_BOOT + timedelta(minutes=minute)) for minute in range(12)])


@pytest.fixture
def later_sample_beyond_the_recent_ones() -> None:
    SampleRepository.add_many([_floor_sample(NOW + timedelta(hours=1))])
    SampleRepository.add_many([_floor_sample(NOW) for _ in range(RECENT_SAMPLES)])


def test_add_many_persists_samples(f_samples: list[SampleValue]) -> None:
    created = SampleRepository.add_many(f_samples)

    assert (created, Sample.objects.count(), SampleRepository.last_recorded_at()) == (2, 2, NOW)


@pytest.mark.usefixtures('samples_after_a_boot_on_a_lagging_clock')
def test_last_recorded_at_passes_over_the_newest_samples_stamped_by_a_lagging_clock() -> None:
    assert SampleRepository.last_recorded_at() == NOW


@pytest.mark.usefixtures('later_sample_beyond_the_recent_ones')
def test_last_recorded_at_reads_only_the_recent_samples() -> None:
    assert SampleRepository.last_recorded_at() == NOW


def test_last_recorded_at_is_none_without_samples() -> None:
    assert SampleRepository.last_recorded_at() is None
