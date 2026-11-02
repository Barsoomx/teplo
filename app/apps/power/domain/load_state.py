from enum import StrEnum

from apps.power.domain.shedding import Shed


class LoadState(StrEnum):
    DRAWING = 'drawing'
    IDLE = 'idle'
    SHED = 'shed'
    SHED_DRY_RUN = 'shed_dry_run'
    WAITING = 'waiting'
    WAITING_DRY_RUN = 'waiting_dry_run'
    UNAVAILABLE = 'unavailable'


def load_state(*, drawing: bool | None, shed: Shed | None, waiting: bool, live: bool) -> LoadState:
    if shed is not None:
        return LoadState.SHED if shed.live else LoadState.SHED_DRY_RUN

    if waiting:
        return LoadState.WAITING if live else LoadState.WAITING_DRY_RUN

    if drawing is None:
        return LoadState.UNAVAILABLE

    return LoadState.DRAWING if drawing else LoadState.IDLE
