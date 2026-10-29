from datetime import UTC, datetime

import pytest

from apps.telemetry.domain.samples import SampleValue
from apps.telemetry.services.sample_buffer import SampleBuffer


@pytest.fixture
def f_sample() -> SampleValue:
    return SampleValue(
        metric='floor_temperature',
        subject='thermostat-kitchen',
        value=24.5,
        recorded_at=datetime(2026, 10, 29, 6, 0, tzinfo=UTC),
    )


@pytest.fixture
def f_buffer(f_sample: SampleValue) -> SampleBuffer:
    buffer = SampleBuffer()
    buffer.add(f_sample)
    return buffer


def test_drain_returns_samples_once(f_buffer: SampleBuffer, f_sample: SampleValue) -> None:
    assert (f_buffer.drain(), f_buffer.drain()) == ([f_sample], [])
