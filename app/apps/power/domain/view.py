from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal

from apps.power.domain.load_status import LoadStatus
from apps.power.domain.loads import LoadPhase


@dataclass(frozen=True, kw_only=True)
class GuardView:
    currents: Mapping[LoadPhase, Decimal]
    loads: tuple[LoadStatus, ...]
    overloaded: frozenset[LoadPhase] = frozenset()
    calming: frozenset[LoadPhase] = frozenset()
    silent_currents: Mapping[LoadPhase, Decimal] = field(default_factory=dict)
