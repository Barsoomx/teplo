from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from apps.climate.domain.drive import StartAnswer, ZoneStart


class LoadGate(Protocol):
    def held_zones(self) -> frozenset[str]: ...

    def ask_starts(self, starts: Sequence[ZoneStart], now: datetime) -> dict[str, StartAnswer]: ...
