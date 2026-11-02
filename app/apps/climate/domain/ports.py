from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from apps.climate.domain.drive import StartAnswer, ZoneStart
from apps.climate.domain.learning import HeatUpModel, ZoneLearningRecord
from apps.climate.domain.zone import ZoneSettings


class ZoneStore(Protocol):
    def all_settings(self) -> list[ZoneSettings]: ...

    def mark_commanded(self, slug: str, at: datetime) -> None: ...

    def mark_dry_run(self, slug: str, at: datetime) -> None: ...

    def mark_air_dry_run(self, slug: str, at: datetime) -> None: ...


class ZoneLearningStore(Protocol):
    def all_records(self) -> dict[str, ZoneLearningRecord]: ...

    def save_heat_up(self, slug: str, heat_up: HeatUpModel) -> None: ...

    def save_early_stop_offset(self, slug: str, offset: Decimal) -> None: ...


class LoadGate(Protocol):
    def held_zones(self) -> frozenset[str]: ...

    def ask_starts(self, starts: Sequence[ZoneStart], now: datetime) -> dict[str, StartAnswer]: ...
