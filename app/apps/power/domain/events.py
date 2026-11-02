from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from apps.core.domain.events import DomainEvent
from apps.power.domain.loads import LoadKind, LoadPhase, ShedReason
from apps.power.domain.start_gate import StartRefusal


class RestoreCause(StrEnum):
    CALM = 'calm'
    LIMP = 'limp'
    RELEASED = 'released'


class LoadCommand(StrEnum):
    SHED = 'shed'
    RESTORE = 'restore'


@dataclass(frozen=True, kw_only=True)
class PhaseOverloaded(DomainEvent):
    phase: LoadPhase
    amps: Decimal


@dataclass(frozen=True, kw_only=True)
class PhaseCalmed(DomainEvent):
    phase: LoadPhase
    amps: Decimal


@dataclass(frozen=True, kw_only=True)
class LoadShedEvent(DomainEvent):
    load: str
    name: str
    kind: LoadKind
    phase: LoadPhase
    amps: Decimal | None
    load_amps: Decimal
    reason: ShedReason


@dataclass(frozen=True, kw_only=True)
class LoadShed(LoadShedEvent):
    pass


@dataclass(frozen=True, kw_only=True)
class LoadShedDryRun(LoadShedEvent):
    pass


@dataclass(frozen=True, kw_only=True)
class LoadRestoreEvent(DomainEvent):
    load: str
    name: str
    kind: LoadKind
    phase: LoadPhase
    amps: Decimal | None
    load_amps: Decimal
    reason: ShedReason
    cause: RestoreCause
    switched_on: bool


@dataclass(frozen=True, kw_only=True)
class LoadRestored(LoadRestoreEvent):
    pass


@dataclass(frozen=True, kw_only=True)
class LoadRestoredDryRun(LoadRestoreEvent):
    pass


@dataclass(frozen=True, kw_only=True)
class LoadShedOverridden(DomainEvent):
    load: str
    name: str
    phase: LoadPhase


@dataclass(frozen=True, kw_only=True)
class LoadCommandFailed(DomainEvent):
    load: str
    name: str
    command: LoadCommand
    error: str
    error_code: str


@dataclass(frozen=True, kw_only=True)
class StartWouldWaitDryRun(DomainEvent):
    load: str
    name: str
    phase: LoadPhase
    amps: Decimal | None
    load_amps: Decimal
    reason: StartRefusal


@dataclass(frozen=True, kw_only=True)
class LoadPhaseLearned(DomainEvent):
    load: str
    name: str
    phase: LoadPhase
    amps: Decimal
    samples: int


@dataclass(frozen=True, kw_only=True)
class EvChargingEvent(DomainEvent):
    phase: LoadPhase
    amps: Decimal
    before: Decimal


@dataclass(frozen=True, kw_only=True)
class EvChargingStarted(EvChargingEvent):
    pass


@dataclass(frozen=True, kw_only=True)
class EvChargingStopped(EvChargingEvent):
    pass
