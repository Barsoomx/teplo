from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from apps.power.domain.load_status import LoadStatus
from apps.power.domain.loads import LoadPhase


@dataclass(frozen=True, kw_only=True)
class GuardView:
    currents: Mapping[LoadPhase, Decimal]
    loads: tuple[LoadStatus, ...]
