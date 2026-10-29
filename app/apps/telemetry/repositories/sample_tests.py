from datetime import UTC, datetime, timedelta

import pytest

from apps.telemetry.domain.samples import SampleValue
from apps.telemetry.models import Sample
from apps.telemetry.repositories.sample import SampleRepository

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 10, 29, 6, 0, tzinfo=UTC)


@pytest.fixture
def f_samples() -> list[SampleValue]:
    return [
        SampleValue(metric='floor_temperature', subject='thermostat-kitchen', value=24.5, recorded_at=NOW),
        SampleValue(
            metric='floor_temperature',
            subject='thermostat-kitchen',
            value=24.0,
            recorded_at=NOW - timedelta(minutes=1),
        ),
    ]


def test_add_many_persists_samples(f_samples: list[SampleValue]) -> None:
    created = SampleRepository.add_many(f_samples)

    assert (created, Sample.objects.count(), SampleRepository.last_recorded_at()) == (2, 2, NOW)


def test_last_recorded_at_is_none_without_samples() -> None:
    assert SampleRepository.last_recorded_at() is None
