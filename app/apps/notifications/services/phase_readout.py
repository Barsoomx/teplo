from decimal import Decimal

from apps.devices.domain.kinds import DeviceKind
from apps.devices.domain.readings import Phase
from apps.devices.repositories.device import DeviceRepository
from apps.devices.services.phase_meter_store import PhaseMeterStore, phase_meter_store
from apps.notifications.domain.alerts import PhaseReading
from apps.power.domain.estimate import PhaseEstimate
from apps.power.domain.loads import METER_PHASES
from apps.power.services.guard_book import GuardBook, guard_book


class PhaseMeterReadout:
    def __init__(self, devices: DeviceRepository, store: PhaseMeterStore, book: GuardBook) -> None:
        self._devices = devices
        self._store = store
        self._book = book

    @classmethod
    def init(cls) -> 'PhaseMeterReadout':
        return cls(devices=DeviceRepository(), store=phase_meter_store, book=guard_book)

    def phases(self) -> tuple[PhaseReading, ...] | None:
        slugs = self._devices.slugs_of_kind(DeviceKind.PHASE_METER)
        if not slugs:
            return None

        snapshot = self._store.snapshot(slugs[0])
        amps = {} if snapshot.currents is None else snapshot.currents.amps
        state = self._book.state()
        estimate = None if state is None else state.phases.estimate
        return tuple(
            _reading(METER_PHASES[phase].value, None if phase in snapshot.stale_phases else amps.get(phase), estimate)
            for phase in Phase
        )


def _reading(phase: str, amps: Decimal | None, estimate: PhaseEstimate | None) -> PhaseReading:
    if amps is None or estimate is None or estimate.phase != phase:
        return PhaseReading(phase=phase, amps=amps, estimated=False)

    return PhaseReading(phase=phase, amps=estimate.amps, estimated=True)
