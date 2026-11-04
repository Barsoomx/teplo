from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from apps.core.stubs import MonotonicClockStub
from apps.devices.domain.kinds import DeviceDriver, DeviceKind
from apps.devices.domain.readings import (
    ClimateMode,
    ClimateUnitState,
    Phase,
    PlugState,
    TemperatureReading,
    ThermostatMode,
    ThermostatState,
)
from apps.devices.factories import DeviceFactory
from apps.devices.repositories.device import DeviceRepository
from apps.devices.services.phase_meter_store import PhaseMeterStore
from apps.devices.services.state_store import (
    ClimateUnitStateStore,
    PlugStateStore,
    TemperatureStateStore,
    ThermostatStateStore,
)
from apps.telemetry.domain.samples import SampleValue
from apps.telemetry.services.sample_buffer import SampleBuffer
from apps.telemetry.usecases.capture_samples import (
    CaptureSamplesInputDTO,
    CaptureSamplesOutputDTO,
    CaptureSamplesUseCase,
)

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 10, 29, 6, 0, tzinfo=UTC)
NEXT_CAPTURE = NOW + timedelta(minutes=1)
CURRENTS = (
    (Phase.A, '10.00'),
    (Phase.A, '12.00'),
    (Phase.B, '4.10'),
    (Phase.C, '0.52'),
    (Phase.C, '0.50'),
)
AIR_SAMPLES = [
    SampleValue(metric='air_temperature', subject='air-kitchen', value=21.5, recorded_at=NOW),
    SampleValue(metric='air_humidity', subject='air-kitchen', value=41.2, recorded_at=NOW),
    SampleValue(metric='air_temperature', subject='outdoor', value=-3.5, recorded_at=NOW),
]


@pytest.fixture
def f_buffer() -> SampleBuffer:
    return SampleBuffer()


@pytest.fixture
def clock() -> MonotonicClockStub:
    return MonotonicClockStub(now=1000.0)


@pytest.fixture
def temperature_store(clock: MonotonicClockStub) -> TemperatureStateStore:
    return TemperatureStateStore(unavailable_after=timedelta(minutes=10), clock=clock)


@pytest.fixture
def f_phase_meter_store(clock: MonotonicClockStub) -> PhaseMeterStore:
    return PhaseMeterStore(stale_after=timedelta(seconds=10), unavailable_after=timedelta(minutes=10), clock=clock)


@pytest.fixture
def f_use_case(
    f_buffer: SampleBuffer,
    temperature_store: TemperatureStateStore,
    f_phase_meter_store: PhaseMeterStore,
) -> CaptureSamplesUseCase:
    DeviceFactory(slug='thermostat-kitchen')
    DeviceFactory(slug='thermostat-hallway')
    DeviceFactory(slug='plug-boiler-1f', kind=DeviceKind.PLUG, driver=DeviceDriver.TUYA_PLUG)
    DeviceFactory(slug='plug-boiler-2f', kind=DeviceKind.PLUG, driver=DeviceDriver.TUYA_PLUG)
    DeviceFactory(slug='heater-office', kind=DeviceKind.CLIMATE_UNIT, driver=DeviceDriver.TUYA_HEATER)
    DeviceFactory(slug='heater-bedroom', kind=DeviceKind.CLIMATE_UNIT, driver=DeviceDriver.TUYA_HEATER)
    thermostat_store = ThermostatStateStore(unavailable_after=timedelta(minutes=10))
    thermostat_store.record_state(
        'thermostat-kitchen',
        ThermostatState(
            power=True,
            mode=ThermostatMode.HOLD,
            setpoint=Decimal('28.0'),
            floor_temperature=Decimal('24.5'),
            heating=True,
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
    climate_unit_store = ClimateUnitStateStore(unavailable_after=timedelta(minutes=10))
    climate_unit_store.record_state(
        'heater-office',
        ClimateUnitState(
            mode=ClimateMode.HEAT,
            target=Decimal(22),
            room_temperature=Decimal(21),
            read_at=NOW,
            level='High',
        ),
    )
    return CaptureSamplesUseCase(
        devices=DeviceRepository(),
        thermostat_store=thermostat_store,
        plug_store=plug_store,
        temperature_store=temperature_store,
        phase_meter_store=f_phase_meter_store,
        climate_unit_store=climate_unit_store,
        buffer=f_buffer,
    )


@pytest.fixture
def f_sensor_use_case(
    f_buffer: SampleBuffer,
    clock: MonotonicClockStub,
    temperature_store: TemperatureStateStore,
    f_phase_meter_store: PhaseMeterStore,
) -> CaptureSamplesUseCase:
    for slug in ('outdoor', 'air-kitchen', 'air-hallway'):
        DeviceFactory(slug=slug, kind=DeviceKind.TEMPERATURE_SENSOR, driver=DeviceDriver.ESPHOME_TEMPERATURE)
    DeviceFactory(slug='phase-meter', kind=DeviceKind.PHASE_METER, driver=DeviceDriver.ESPHOME_PHASE_METER)
    DeviceFactory(slug='thermostat-kitchen')
    temperature_store.record_state(
        'air-hallway',
        TemperatureReading(temperature=Decimal('20.1'), humidity=Decimal('38.0'), read_at=NOW),
    )
    clock.advance(10 * 60 + 1)
    temperature_store.record_state(
        'air-kitchen',
        TemperatureReading(temperature=Decimal('21.5'), humidity=Decimal('41.2'), read_at=NOW),
    )
    temperature_store.record_state(
        'outdoor',
        TemperatureReading(temperature=Decimal('-3.5'), humidity=None, read_at=NOW),
    )
    for phase, amps in CURRENTS:
        f_phase_meter_store.record_current('phase-meter', phase, Decimal(amps), NOW)
    return CaptureSamplesUseCase(
        devices=DeviceRepository(),
        thermostat_store=ThermostatStateStore(unavailable_after=timedelta(minutes=10), clock=clock),
        plug_store=PlugStateStore(unavailable_after=timedelta(minutes=10), clock=clock),
        temperature_store=temperature_store,
        phase_meter_store=f_phase_meter_store,
        climate_unit_store=ClimateUnitStateStore(unavailable_after=timedelta(minutes=10), clock=clock),
        buffer=f_buffer,
    )


@pytest.fixture
def f_captured_use_case(f_sensor_use_case: CaptureSamplesUseCase, f_buffer: SampleBuffer) -> CaptureSamplesUseCase:
    f_sensor_use_case.execute(CaptureSamplesInputDTO(now=NOW))
    f_buffer.drain()
    return f_sensor_use_case


@pytest.fixture
def f_silent_meter_use_case(
    f_buffer: SampleBuffer,
    clock: MonotonicClockStub,
    temperature_store: TemperatureStateStore,
    f_phase_meter_store: PhaseMeterStore,
) -> CaptureSamplesUseCase:
    DeviceFactory(slug='phase-meter', kind=DeviceKind.PHASE_METER, driver=DeviceDriver.ESPHOME_PHASE_METER)
    for phase, amps in CURRENTS:
        f_phase_meter_store.record_current('phase-meter', phase, Decimal(amps), NOW)
    clock.advance(10 * 60 + 1)
    return CaptureSamplesUseCase(
        devices=DeviceRepository(),
        thermostat_store=ThermostatStateStore(unavailable_after=timedelta(minutes=10), clock=clock),
        plug_store=PlugStateStore(unavailable_after=timedelta(minutes=10), clock=clock),
        temperature_store=temperature_store,
        phase_meter_store=f_phase_meter_store,
        climate_unit_store=ClimateUnitStateStore(unavailable_after=timedelta(minutes=10), clock=clock),
        buffer=f_buffer,
    )


def test_capture_buffers_available_devices_only(f_use_case: CaptureSamplesUseCase, f_buffer: SampleBuffer) -> None:
    output = f_use_case.execute(CaptureSamplesInputDTO(now=NOW))

    assert (output, f_buffer.drain()) == (
        CaptureSamplesOutputDTO(captured=10),
        [
            SampleValue(metric='floor_temperature', subject='thermostat-kitchen', value=24.5, recorded_at=NOW),
            SampleValue(metric='floor_setpoint', subject='thermostat-kitchen', value=28.0, recorded_at=NOW),
            SampleValue(metric='thermostat_power', subject='thermostat-kitchen', value=1.0, recorded_at=NOW),
            SampleValue(metric='plug_on', subject='plug-boiler-1f', value=1.0, recorded_at=NOW),
            SampleValue(metric='plug_power', subject='plug-boiler-1f', value=1305.4, recorded_at=NOW),
            SampleValue(metric='plug_current', subject='plug-boiler-1f', value=6.02, recorded_at=NOW),
            SampleValue(metric='plug_voltage', subject='plug-boiler-1f', value=216.8, recorded_at=NOW),
            SampleValue(metric='climate_unit_power', subject='heater-office', value=1.0, recorded_at=NOW),
            SampleValue(metric='climate_unit_setpoint', subject='heater-office', value=22.0, recorded_at=NOW),
            SampleValue(metric='climate_unit_temperature', subject='heater-office', value=21.0, recorded_at=NOW),
        ],
    )


def test_capture_buffers_the_air_readings_and_the_phase_currents_since_the_last_capture(
    f_sensor_use_case: CaptureSamplesUseCase,
    f_buffer: SampleBuffer,
) -> None:
    output = f_sensor_use_case.execute(CaptureSamplesInputDTO(now=NOW))

    assert (output, f_buffer.drain()) == (
        CaptureSamplesOutputDTO(captured=9),
        [
            *AIR_SAMPLES,
            SampleValue(metric='phase_current_mean', subject='l1', value=11.0, recorded_at=NOW),
            SampleValue(metric='phase_current_max', subject='l1', value=12.0, recorded_at=NOW),
            SampleValue(metric='phase_current_mean', subject='l2', value=4.1, recorded_at=NOW),
            SampleValue(metric='phase_current_max', subject='l2', value=4.1, recorded_at=NOW),
            SampleValue(metric='phase_current_mean', subject='l3', value=0.51, recorded_at=NOW),
            SampleValue(metric='phase_current_max', subject='l3', value=0.52, recorded_at=NOW),
        ],
    )


def test_phase_currents_start_over_after_a_capture(
    f_captured_use_case: CaptureSamplesUseCase,
    f_buffer: SampleBuffer,
) -> None:
    f_captured_use_case.execute(CaptureSamplesInputDTO(now=NEXT_CAPTURE))

    assert f_buffer.drain() == [replace(sample, recorded_at=NEXT_CAPTURE) for sample in AIR_SAMPLES]


def test_unavailable_phase_meter_is_skipped_and_its_currents_dropped(
    f_silent_meter_use_case: CaptureSamplesUseCase,
    f_buffer: SampleBuffer,
    f_phase_meter_store: PhaseMeterStore,
) -> None:
    output = f_silent_meter_use_case.execute(CaptureSamplesInputDTO(now=NOW))

    assert (output, f_buffer.drain(), f_phase_meter_store.capture_statistics('phase-meter')) == (
        CaptureSamplesOutputDTO(captured=0),
        [],
        {},
    )
