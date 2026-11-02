from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from apps.core.domain.usecases.base import BaseUseCase
from apps.devices.domain.kinds import DeviceKind
from apps.devices.domain.readings import Phase
from apps.devices.repositories.device import DeviceRepository
from apps.devices.services.phase_meter_store import PhaseMeterSnapshot, PhaseMeterStore, phase_meter_store
from apps.power.domain.loads import METER_PHASES, LoadPhase
from apps.power.repositories.load_settings import LoadSettingsRepository
from apps.power.services.guard_book import GuardBook, guard_book

SCALE_AMPS = Decimal(25)
SCALE_PAST_SHED_AMPS = Decimal(1)


@dataclass(frozen=True, kw_only=True)
class GetPhaseCurrentsInputDTO:
    pass


@dataclass(frozen=True, kw_only=True)
class PhaseCurrent:
    phase: str
    amps: Decimal | None
    stale: bool
    age_seconds: int | None
    ev_amps: Decimal | None = None


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
    def __init__(
        self,
        devices: DeviceRepository,
        store: PhaseMeterStore,
        load_settings: LoadSettingsRepository,
        guard_book: GuardBook,
    ) -> None:
        super().__init__()
        self._devices = devices
        self._store = store
        self._load_settings = load_settings
        self._guard_book = guard_book

    @classmethod
    def init(cls) -> 'GetPhaseCurrentsUseCase':
        return cls(
            devices=DeviceRepository(),
            store=phase_meter_store,
            load_settings=LoadSettingsRepository(),
            guard_book=guard_book,
        )

    def _execute(self, input_dto: GetPhaseCurrentsInputDTO) -> GetPhaseCurrentsOutputDTO:  # noqa: ARG002
        slugs = self._devices.slugs_of_kind(DeviceKind.PHASE_METER)
        thresholds = self._load_settings.control().thresholds
        return GetPhaseCurrentsOutputDTO(
            meter=_meter(self._store.snapshot(slugs[0]), self._charging()) if slugs else None,
            scale_amps=max(SCALE_AMPS, thresholds.shed + SCALE_PAST_SHED_AMPS),
            start_ceiling_amps=thresholds.start_ceiling,
            shed_amps=thresholds.shed,
        )

    def _charging(self) -> dict[LoadPhase, Decimal]:
        state = self._guard_book.state()
        if state is None:
            return {}

        return {watch.phase: watch.step for watch in state.ev if watch.charging}


def _meter(snapshot: PhaseMeterSnapshot, charging: Mapping[LoadPhase, Decimal]) -> PhaseMeterOverview:
    amps = snapshot.currents.amps if snapshot.currents else {}
    return PhaseMeterOverview(
        stale=snapshot.stale,
        error_code=snapshot.error_code,
        error=snapshot.error,
        phases=tuple(
            PhaseCurrent(
                phase=METER_PHASES[phase].value,
                amps=amps.get(phase),
                stale=phase in snapshot.stale_phases,
                age_seconds=int(snapshot.ages[phase].total_seconds()) if phase in snapshot.ages else None,
                ev_amps=charging.get(METER_PHASES[phase]),
            )
            for phase in Phase
        ),
    )
