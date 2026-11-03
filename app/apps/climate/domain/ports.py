from collections.abc import Collection, Mapping, Sequence
from contextlib import AbstractContextManager
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from apps.climate.domain.climate_config import ClimateConfig, ClimateConfigRecord
from apps.climate.domain.comfort_windows import ZoneWeek
from apps.climate.domain.drive import StartAnswer, ZoneStart
from apps.climate.domain.learning import HeatUpModel, ZoneLearningRecord
from apps.climate.domain.write_guard import WriteReservation
from apps.climate.domain.zone import ComfortWindow, ZoneSettings
from apps.climate.domain.zone_config import ZoneConfig, ZoneConfigRecord


class ZoneStore(Protocol):
    def all_settings(self) -> list[ZoneSettings]: ...

    def mark_commanded(self, slug: str, at: datetime) -> None: ...

    def mark_dry_run(self, slug: str, at: datetime) -> None: ...

    def mark_air_dry_run(self, slug: str, at: datetime) -> None: ...


class ZoneBoostStore(Protocol):
    def boost(self, slug: str, until: datetime, *, clear_stamps: bool) -> None: ...


class ZoneLearningStore(Protocol):
    def all_records(self) -> dict[str, ZoneLearningRecord]: ...

    def save_heat_up(self, slug: str, heat_up: HeatUpModel) -> None: ...

    def save_early_stop_offset(self, slug: str, offset: Decimal) -> None: ...


class LoadGate(Protocol):
    def held_zones(self) -> frozenset[str]: ...

    def gated_zones(self) -> frozenset[str]: ...

    def ask_starts(self, starts: Sequence[ZoneStart], now: datetime) -> dict[str, StartAnswer]: ...


class WriteGate(Protocol):
    def reserve(
        self,
        zone: str,
        at: datetime,
        *,
        stamped_at: datetime | None,
        read_at: float,
    ) -> WriteReservation | None: ...

    def override(self, zone: str, at: datetime) -> WriteReservation: ...

    def stamping(self, reservation: WriteReservation) -> AbstractContextManager[None]: ...

    def stored(self, reservation: WriteReservation) -> None: ...

    def holds(self, reservation: WriteReservation) -> bool: ...

    def clear(self, zone: str) -> bool: ...


class ZoneConfigStore(Protocol):
    def config_records(self) -> list[ZoneConfigRecord]: ...

    def config(self, slug: str) -> ZoneConfig: ...

    def save_config(self, slug: str, config: ZoneConfig, fields: Collection[str]) -> None: ...


class ClimateConfigStore(Protocol):
    def config_record(self) -> ClimateConfigRecord: ...

    def save_config(self, config: ClimateConfig, fields: Collection[str]) -> None: ...


class ComfortWindowStore(Protocol):
    def weeks(self) -> list[ZoneWeek]: ...

    def week(self, slug: str) -> ZoneWeek: ...

    def window_week(self, window_id: int) -> ZoneWeek: ...

    def replace_days(self, slug: str, days: Mapping[int, Sequence[ComfortWindow]]) -> None: ...
