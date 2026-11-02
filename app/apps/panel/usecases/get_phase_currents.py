from dataclasses import dataclass
from decimal import Decimal

from apps.core.domain.usecases.base import BaseUseCase
from apps.devices.domain.kinds import DeviceKind
from apps.devices.domain.readings import Phase
from apps.devices.repositories.device import DeviceRepository
from apps.devices.services.phase_meter_store import PhaseMeterSnapshot, PhaseMeterStore, phase_meter_store

PHASE_NAMES = {Phase.A: 'l1', Phase.B: 'l2', Phase.C: 'l3'}
SCALE_AMPS = Decimal(25)
START_CEILING_AMPS = Decimal(20)
SHED_AMPS = Decimal(24)


@dataclass(frozen=True, kw_only=True)
class GetPhaseCurrentsInputDTO:
    pass


@dataclass(frozen=True, kw_only=True)
class PhaseCurrent:
    phase: str
    amps: Decimal | None
    stale: bool
    age_seconds: int | None


@dataclass(frozen=True, kw_only=True)
class PhaseMeterOverview:
    stale: bool
    error_code: str | None
    error: str | None
    phases: tuple[PhaseCurrent, ...]


@dataclass(frozen=True, kw_only=True)
class GetPhaseCurrentsOutputDTO:
    meter: PhaseMeterOverview | None
    scale_amps: Decimal
    start_ceiling_amps: Decimal
    shed_amps: Decimal


class GetPhaseCurrentsUseCase(BaseUseCase[GetPhaseCurrentsInputDTO, GetPhaseCurrentsOutputDTO]):
    def __init__(self, devices: DeviceRepository, store: PhaseMeterStore) -> None:
        super().__init__()
        self._devices = devices
        self._store = store

    @classmethod
    def init(cls) -> 'GetPhaseCurrentsUseCase':
        return cls(devices=DeviceRepository(), store=phase_meter_store)

    def _execute(self, input_dto: GetPhaseCurrentsInputDTO) -> GetPhaseCurrentsOutputDTO:  # noqa: ARG002
        slugs = self._devices.slugs_of_kind(DeviceKind.PHASE_METER)
        return GetPhaseCurrentsOutputDTO(
            meter=_meter(self._store.snapshot(slugs[0])) if slugs else None,
            scale_amps=SCALE_AMPS,
            start_ceiling_amps=START_CEILING_AMPS,
            shed_amps=SHED_AMPS,
        )


def _meter(snapshot: PhaseMeterSnapshot) -> PhaseMeterOverview:
    amps = snapshot.currents.amps if snapshot.currents else {}
    return PhaseMeterOverview(
        stale=snapshot.stale,
        error_code=snapshot.error_code,
        error=snapshot.error,
        phases=tuple(
            PhaseCurrent(
                phase=PHASE_NAMES[phase],
                amps=amps.get(phase),
                stale=phase in snapshot.stale_phases,
                age_seconds=int(snapshot.ages[phase].total_seconds()) if phase in snapshot.ages else None,
            )
            for phase in Phase
        ),
    )
