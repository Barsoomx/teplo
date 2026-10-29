from dataclasses import dataclass

from apps.core.domain.usecases.base import BaseUseCase
from apps.telemetry.repositories.sample import SampleRepository
from apps.telemetry.services.sample_buffer import SampleBuffer, sample_buffer


@dataclass(frozen=True, kw_only=True)
class FlushSamplesInputDTO:
    pass


@dataclass(frozen=True, kw_only=True)
class FlushSamplesOutputDTO:
    flushed: int


class FlushSamplesUseCase(BaseUseCase[FlushSamplesInputDTO, FlushSamplesOutputDTO]):
    def __init__(self, buffer: SampleBuffer, samples: SampleRepository) -> None:
        super().__init__()
        self._buffer = buffer
        self._samples = samples

    @classmethod
    def init(cls) -> 'FlushSamplesUseCase':
        return cls(buffer=sample_buffer, samples=SampleRepository())

    def _execute(self, input_dto: FlushSamplesInputDTO) -> FlushSamplesOutputDTO:  # noqa: ARG002
        samples = self._buffer.drain()
        if not samples:
            return FlushSamplesOutputDTO(flushed=0)

        return FlushSamplesOutputDTO(flushed=self._samples.add_many(samples))
