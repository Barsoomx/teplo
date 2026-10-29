from datetime import UTC, datetime

import pytest

from apps.telemetry.domain.samples import SampleValue
from apps.telemetry.models import Sample
from apps.telemetry.repositories.sample import SampleRepository
from apps.telemetry.services.sample_buffer import SampleBuffer
from apps.telemetry.usecases.flush_samples import FlushSamplesInputDTO, FlushSamplesOutputDTO, FlushSamplesUseCase

pytestmark = pytest.mark.django_db


@pytest.fixture
def f_use_case() -> FlushSamplesUseCase:
    buffer = SampleBuffer()
    buffer.add(
        SampleValue(
            metric='floor_temperature',
            subject='thermostat-kitchen',
            value=24.5,
            recorded_at=datetime(2026, 10, 29, 6, 0, tzinfo=UTC),
        ),
    )
    return FlushSamplesUseCase(buffer=buffer, samples=SampleRepository())


def test_flush_persists_buffered_samples(f_use_case: FlushSamplesUseCase) -> None:
    output = f_use_case.execute(FlushSamplesInputDTO())

    assert (output, list(Sample.objects.values_list('metric', 'subject', 'value'))) == (
        FlushSamplesOutputDTO(flushed=1),
        [('floor_temperature', 'thermostat-kitchen', 24.5)],
    )
