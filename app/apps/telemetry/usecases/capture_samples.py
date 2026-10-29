from dataclasses import dataclass
from datetime import datetime

from apps.core.domain.usecases.base import BaseUseCase
from apps.devices.domain.readings import ThermostatState
from apps.devices.repositories.device import DeviceRepository
from apps.devices.services.state_store import ThermostatStateStore, thermostat_state_store
from apps.telemetry.domain.samples import SampleValue
from apps.telemetry.services.sample_buffer import SampleBuffer, sample_buffer


@dataclass(frozen=True, kw_only=True)
class CaptureSamplesInputDTO:
    now: datetime


@dataclass(frozen=True, kw_only=True)
class CaptureSamplesOutputDTO:
    captured: int


class CaptureSamplesUseCase(BaseUseCase[CaptureSamplesInputDTO, CaptureSamplesOutputDTO]):
    def __init__(self, devices: DeviceRepository, store: ThermostatStateStore, buffer: SampleBuffer) -> None:
        super().__init__()
        self._devices = devices
        self._store = store
        self._buffer = buffer

    @classmethod
    def init(cls) -> 'CaptureSamplesUseCase':
        return cls(devices=DeviceRepository(), store=thermostat_state_store, buffer=sample_buffer)

    def _execute(self, input_dto: CaptureSamplesInputDTO) -> CaptureSamplesOutputDTO:
        captured = 0
        for slug in self._devices.thermostat_slugs():
            snapshot = self._store.snapshot(slug, input_dto.now)
            if not snapshot.available or snapshot.state is None:
                continue

            for sample in _thermostat_samples(slug, snapshot.state, input_dto.now):
                self._buffer.add(sample)
                captured += 1
        return CaptureSamplesOutputDTO(captured=captured)


def _thermostat_samples(slug: str, state: ThermostatState, now: datetime) -> tuple[SampleValue, ...]:
    return (
        SampleValue(metric='floor_temperature', subject=slug, value=float(state.floor_temperature), recorded_at=now),
        SampleValue(metric='floor_setpoint', subject=slug, value=float(state.setpoint), recorded_at=now),
        SampleValue(metric='thermostat_power', subject=slug, value=1.0 if state.power else 0.0, recorded_at=now),
    )
