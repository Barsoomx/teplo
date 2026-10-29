from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from apps.devices.domain.readings import ThermostatMode, ThermostatState
from apps.devices.factories import DeviceFactory
from apps.devices.repositories.device import DeviceRepository
from apps.devices.services.state_store import ThermostatStateStore
from apps.telemetry.domain.samples import SampleValue
from apps.telemetry.services.sample_buffer import SampleBuffer
from apps.telemetry.usecases.capture_samples import (
    CaptureSamplesInputDTO,
    CaptureSamplesOutputDTO,
    CaptureSamplesUseCase,
)

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 10, 29, 6, 0, tzinfo=UTC)


@pytest.fixture
def f_buffer() -> SampleBuffer:
    return SampleBuffer()


@pytest.fixture
def f_use_case(f_buffer: SampleBuffer) -> CaptureSamplesUseCase:
    DeviceFactory(slug='thermostat-kitchen')
    DeviceFactory(slug='thermostat-hallway')
    store = ThermostatStateStore(unavailable_after=timedelta(minutes=10))
    store.record_state(
        'thermostat-kitchen',
        ThermostatState(
            power=True,
            mode=ThermostatMode.HOLD,
            setpoint=Decimal('28.0'),
            floor_temperature=Decimal('24.5'),
            read_at=NOW,
        ),
    )
    return CaptureSamplesUseCase(devices=DeviceRepository(), store=store, buffer=f_buffer)


def test_capture_buffers_available_thermostats_only(f_use_case: CaptureSamplesUseCase, f_buffer: SampleBuffer) -> None:
    output = f_use_case.execute(CaptureSamplesInputDTO(now=NOW))

    assert (output, f_buffer.drain()) == (
        CaptureSamplesOutputDTO(captured=3),
        [
            SampleValue(metric='floor_temperature', subject='thermostat-kitchen', value=24.5, recorded_at=NOW),
            SampleValue(metric='floor_setpoint', subject='thermostat-kitchen', value=28.0, recorded_at=NOW),
            SampleValue(metric='thermostat_power', subject='thermostat-kitchen', value=1.0, recorded_at=NOW),
        ],
    )
