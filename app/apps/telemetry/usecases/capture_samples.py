from dataclasses import dataclass
from datetime import datetime

from apps.core.domain.usecases.base import BaseUseCase
from apps.devices.domain.readings import PlugState, ThermostatState
from apps.devices.repositories.device import DeviceRepository
from apps.devices.services.state_store import (
    PlugStateStore,
    ThermostatStateStore,
    plug_state_store,
    thermostat_state_store,
)
from apps.telemetry.domain.samples import SampleValue
from apps.telemetry.services.sample_buffer import SampleBuffer, sample_buffer


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
        buffer: SampleBuffer,
    ) -> None:
        super().__init__()
        self._devices = devices
        self._thermostat_store = thermostat_store
        self._plug_store = plug_store
        self._buffer = buffer

    @classmethod
    def init(cls) -> 'CaptureSamplesUseCase':
        return cls(
            devices=DeviceRepository(),
            thermostat_store=thermostat_state_store,
            plug_store=plug_state_store,
            buffer=sample_buffer,
        )

    def _execute(self, input_dto: CaptureSamplesInputDTO) -> CaptureSamplesOutputDTO:
        samples = [*self._thermostat_samples(input_dto.now), *self._plug_samples(input_dto.now)]
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
