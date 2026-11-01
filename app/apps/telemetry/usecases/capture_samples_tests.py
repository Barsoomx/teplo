from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from apps.devices.domain.kinds import DeviceDriver, DeviceKind
from apps.devices.domain.readings import PlugState, ThermostatMode, ThermostatState
from apps.devices.factories import DeviceFactory
from apps.devices.repositories.device import DeviceRepository
from apps.devices.services.state_store import PlugStateStore, ThermostatStateStore
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
    DeviceFactory(slug='plug-boiler-1f', kind=DeviceKind.PLUG, driver=DeviceDriver.TUYA_PLUG)
    DeviceFactory(slug='plug-boiler-2f', kind=DeviceKind.PLUG, driver=DeviceDriver.TUYA_PLUG)
    thermostat_store = ThermostatStateStore(unavailable_after=timedelta(minutes=10))
    thermostat_store.record_state(
        'thermostat-kitchen',
        ThermostatState(
            power=True,
            mode=ThermostatMode.HOLD,
            setpoint=Decimal('28.0'),
            floor_temperature=Decimal('24.5'),
            read_at=NOW,
        ),
    )
    plug_store = PlugStateStore(unavailable_after=timedelta(minutes=10))
    plug_store.record_state(
        'plug-boiler-1f',
        PlugState(
            on=True,
            power_watts=Decimal('1305.4'),
            current_amps=Decimal('6.02'),
            voltage_volts=Decimal('216.8'),
            read_at=NOW,
        ),
    )
    return CaptureSamplesUseCase(
        devices=DeviceRepository(),
        thermostat_store=thermostat_store,
        plug_store=plug_store,
        buffer=f_buffer,
    )


def test_capture_buffers_available_devices_only(f_use_case: CaptureSamplesUseCase, f_buffer: SampleBuffer) -> None:
    output = f_use_case.execute(CaptureSamplesInputDTO(now=NOW))

    assert (output, f_buffer.drain()) == (
        CaptureSamplesOutputDTO(captured=7),
        [
            SampleValue(metric='floor_temperature', subject='thermostat-kitchen', value=24.5, recorded_at=NOW),
            SampleValue(metric='floor_setpoint', subject='thermostat-kitchen', value=28.0, recorded_at=NOW),
            SampleValue(metric='thermostat_power', subject='thermostat-kitchen', value=1.0, recorded_at=NOW),
            SampleValue(metric='plug_on', subject='plug-boiler-1f', value=1.0, recorded_at=NOW),
            SampleValue(metric='plug_power', subject='plug-boiler-1f', value=1305.4, recorded_at=NOW),
            SampleValue(metric='plug_current', subject='plug-boiler-1f', value=6.02, recorded_at=NOW),
            SampleValue(metric='plug_voltage', subject='plug-boiler-1f', value=216.8, recorded_at=NOW),
        ],
    )
