from decimal import Decimal

from apps.devices.domain.kinds import DeviceKind
from apps.devices.domain.readings import Phase
from apps.devices.repositories.device import DeviceRepository
from apps.devices.services.phase_meter_store import PhaseMeterStore, phase_meter_store
from apps.power.domain.loads import METER_PHASES


class PhaseMeterReadout:
    def __init__(self, devices: DeviceRepository, store: PhaseMeterStore) -> None:
        self._devices = devices
        self._store = store

    @classmethod
    def init(cls) -> 'PhaseMeterReadout':
        return cls(devices=DeviceRepository(), store=phase_meter_store)

    def phases(self) -> tuple[tuple[str, Decimal | None], ...] | None:
        slugs = self._devices.slugs_of_kind(DeviceKind.PHASE_METER)
        if not slugs:
            return None

        snapshot = self._store.snapshot(slugs[0])
        amps = {} if snapshot.currents is None else snapshot.currents.amps
        return tuple(
            (METER_PHASES[phase].value, None if phase in snapshot.stale_phases else amps.get(phase)) for phase in Phase
        )
