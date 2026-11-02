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
        if snapshot.currents is None or snapshot.stale:
            raise DeviceUnavailableError(snapshot.error or 'phase meter stale')

        return snapshot.currents
