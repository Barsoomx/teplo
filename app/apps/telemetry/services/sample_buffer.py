import threading

from apps.telemetry.domain.samples import SampleValue


class SampleBuffer:
    def __init__(self) -> None:
        self._samples: list[SampleValue] = []
        self._lock = threading.Lock()

    def add(self, sample: SampleValue) -> None:
        with self._lock:
            self._samples.append(sample)

    def drain(self) -> list[SampleValue]:
        with self._lock:
            samples = self._samples
            self._samples = []
        return samples


sample_buffer = SampleBuffer()
