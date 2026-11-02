from apps.core.domain.errors import DomainError
from apps.devices.domain.errors import DeviceUnavailableError
from apps.devices.domain.readings import TemperatureReading
from apps.devices.services.state_store import TemperatureStateStore, temperature_state_store


class EsphomeTemperatureDriver:
    def __init__(self, slug: str, store: TemperatureStateStore) -> None:
        self._slug = slug
        self._store = store

    @classmethod
    def init(cls, slug: str) -> 'EsphomeTemperatureDriver':
        return cls(slug=slug, store=temperature_state_store)

    def read_temperature(self) -> TemperatureReading:
        snapshot = self._store.snapshot(self._slug)
        if snapshot.state is not None and snapshot.available:
            return snapshot.state

        if snapshot.error_code is None or snapshot.error is None:
            raise DeviceUnavailableError('no recent reading')

        raise DomainError(snapshot.error, error_code=snapshot.error_code)
