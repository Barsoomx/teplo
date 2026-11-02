from dataclasses import dataclass
from datetime import datetime

from apps.core.domain.usecases.base import BaseUseCase
from apps.devices.domain.kinds import DeviceKind
from apps.devices.domain.readings import Phase, PlugState, TemperatureReading, ThermostatState
from apps.devices.repositories.device import DeviceRepository
from apps.devices.services.phase_meter_store import CurrentStatistics, PhaseMeterStore, phase_meter_store
from apps.devices.services.state_store import (
    PlugStateStore,
    TemperatureStateStore,
    ThermostatStateStore,
    plug_state_store,
    temperature_state_store,
    thermostat_state_store,
)
from apps.telemetry.domain.samples import SampleValue
from apps.telemetry.services.sample_buffer import SampleBuffer, sample_buffer

PHASE_SUBJECTS = {Phase.A: 'l1', Phase.B: 'l2', Phase.C: 'l3'}


@dataclass(frozen=True, kw_only=True)
class CaptureSamplesInputDTO:
    now: datetime


@dataclass(frozen=True, kw_only=True)
class CaptureSamplesOutputDTO:
    captured: int


class CaptureSamplesUseCase(BaseUseCase[CaptureSamplesInputDTO, CaptureSamplesOutputDTO]):
    def __init__(
        self,
        devices: DeviceRepository,
        thermostat_store: ThermostatStateStore,
        plug_store: PlugStateStore,
        temperature_store: TemperatureStateStore,
        phase_meter_store: PhaseMeterStore,
        buffer: SampleBuffer,
    ) -> None:
        super().__init__()
        self._devices = devices
        self._thermostat_store = thermostat_store
        self._plug_store = plug_store
        self._temperature_store = temperature_store
        self._phase_meter_store = phase_meter_store
        self._buffer = buffer

    @classmethod
    def init(cls) -> 'CaptureSamplesUseCase':
        return cls(
            devices=DeviceRepository(),
            thermostat_store=thermostat_state_store,
            plug_store=plug_state_store,
            temperature_store=temperature_state_store,
            phase_meter_store=phase_meter_store,
            buffer=sample_buffer,
        )

    def _execute(self, input_dto: CaptureSamplesInputDTO) -> CaptureSamplesOutputDTO:
        samples = [
            *self._thermostat_samples(input_dto.now),
            *self._plug_samples(input_dto.now),
            *self._temperature_samples(input_dto.now),
            *self._phase_meter_samples(input_dto.now),
        ]
        for sample in samples:
            self._buffer.add(sample)
        return CaptureSamplesOutputDTO(captured=len(samples))

    def _thermostat_samples(self, now: datetime) -> list[SampleValue]:
        samples: list[SampleValue] = []
        for slug in self._devices.thermostat_slugs():
            snapshot = self._thermostat_store.snapshot(slug)
            if snapshot.available and snapshot.state is not None:
                samples.extend(_thermostat_values(slug, snapshot.state, now))
        return samples

    def _plug_samples(self, now: datetime) -> list[SampleValue]:
        samples: list[SampleValue] = []
        for slug in self._devices.plug_slugs():
            snapshot = self._plug_store.snapshot(slug)
            if snapshot.available and snapshot.state is not None:
                samples.extend(_plug_values(slug, snapshot.state, now))
        return samples

    def _temperature_samples(self, now: datetime) -> list[SampleValue]:
        samples: list[SampleValue] = []
        for slug in self._devices.slugs_of_kind(DeviceKind.TEMPERATURE_SENSOR):
            snapshot = self._temperature_store.snapshot(slug)
            if snapshot.available and snapshot.state is not None:
                samples.extend(_temperature_values(slug, snapshot.state, now))
        return samples

    def _phase_meter_samples(self, now: datetime) -> list[SampleValue]:
        samples: list[SampleValue] = []
        for slug in self._devices.slugs_of_kind(DeviceKind.PHASE_METER):
            available = self._phase_meter_store.snapshot(slug).available
            statistics = self._phase_meter_store.capture_statistics(slug)
            if available:
                samples.extend(_phase_values(statistics, now))
        return samples


def _thermostat_values(slug: str, state: ThermostatState, now: datetime) -> tuple[SampleValue, ...]:
    return (
        SampleValue(metric='floor_temperature', subject=slug, value=float(state.floor_temperature), recorded_at=now),
        SampleValue(metric='floor_setpoint', subject=slug, value=float(state.setpoint), recorded_at=now),
        SampleValue(metric='thermostat_power', subject=slug, value=1.0 if state.power else 0.0, recorded_at=now),
    )


def _plug_values(slug: str, state: PlugState, now: datetime) -> tuple[SampleValue, ...]:
    return (
        SampleValue(metric='plug_on', subject=slug, value=1.0 if state.on else 0.0, recorded_at=now),
        SampleValue(metric='plug_power', subject=slug, value=float(state.power_watts), recorded_at=now),
        SampleValue(metric='plug_current', subject=slug, value=float(state.current_amps), recorded_at=now),
        SampleValue(metric='plug_voltage', subject=slug, value=float(state.voltage_volts), recorded_at=now),
    )


def _temperature_values(slug: str, reading: TemperatureReading, now: datetime) -> tuple[SampleValue, ...]:
    temperature = SampleValue(metric='air_temperature', subject=slug, value=float(reading.temperature), recorded_at=now)
    if reading.humidity is None:
        return (temperature,)

    return (
        temperature,
        SampleValue(metric='air_humidity', subject=slug, value=float(reading.humidity), recorded_at=now),
    )


def _phase_values(statistics: dict[Phase, CurrentStatistics], now: datetime) -> list[SampleValue]:
    samples: list[SampleValue] = []
    for phase, current in statistics.items():
        subject = PHASE_SUBJECTS[phase]
        samples.extend(
            (
                SampleValue(metric='phase_current_mean', subject=subject, value=float(current.mean), recorded_at=now),
                SampleValue(metric='phase_current_max', subject=subject, value=float(current.peak), recorded_at=now),
            ),
        )
    return samples
