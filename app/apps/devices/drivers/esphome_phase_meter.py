from apps.core.domain.errors import DomainError
from apps.devices.domain.errors import DeviceUnavailableError
from apps.devices.domain.readings import PhaseCurrents
from apps.devices.services.phase_meter_store import PhaseMeterStore, phase_meter_store


class EsphomePhaseMeterDriver:
    def __init__(self, slug: str, store: PhaseMeterStore) -> None:
        self._slug = slug
        self._store = store

    @classmethod
    def init(cls, slug: str) -> 'EsphomePhaseMeterDriver':
        return cls(slug=slug, store=phase_meter_store)

    def read_currents(self) -> PhaseCurrents:
        snapshot = self._store.snapshot(self._slug)
        if snapshot.currents is not None and not snapshot.stale:
            return snapshot.currents

        if snapshot.error_code is None or snapshot.error is None:
            raise DeviceUnavailableError('phase meter stale')

        raise DomainError(snapshot.error, error_code=snapshot.error_code)
