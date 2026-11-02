from dataclasses import replace
from datetime import datetime
from decimal import Decimal

from apps.climate.domain.learning import HeatUpModel, ZoneLearningRecord
from apps.climate.domain.zone import ZoneSettings

ZoneStamp = tuple[str, str, datetime]


class ZoneStoreStub:
    def __init__(self, zones: list[ZoneSettings], errors: dict[tuple[str, str], Exception]) -> None:
        self._zones = zones
        self._errors = errors
        self._stamps: list[ZoneStamp] = []

    @property
    def stamps(self) -> list[ZoneStamp]:
        return self._stamps

    def all_settings(self) -> list[ZoneSettings]:
        return self._zones

    def mark_commanded(self, slug: str, at: datetime) -> None:
        self._stamp('last_command_at', slug, at)

    def mark_dry_run(self, slug: str, at: datetime) -> None:
        self._stamp('last_dry_run_at', slug, at)

    def mark_air_dry_run(self, slug: str, at: datetime) -> None:
        self._stamp('last_air_dry_run_at', slug, at)

    def _stamp(self, stamp: str, slug: str, at: datetime) -> None:
        error = self._errors.get((stamp, slug))
        if error is not None:
            raise error

        self._stamps.append((stamp, slug, at))


class ZoneLearningStoreStub:
    def __init__(self, records: dict[str, ZoneLearningRecord], errors: dict[str, Exception]) -> None:
        self._records = records
        self._errors = errors

    @property
    def records(self) -> dict[str, ZoneLearningRecord]:
        return self._records

    def all_records(self) -> dict[str, ZoneLearningRecord]:
        return dict(self._records)

    def save_heat_up(self, slug: str, heat_up: HeatUpModel) -> None:
        self._save(slug, replace(self._record(slug), heat_up=heat_up))

    def save_early_stop_offset(self, slug: str, offset: Decimal) -> None:
        self._save(slug, replace(self._record(slug), early_stop_offset=offset))

    def _record(self, slug: str) -> ZoneLearningRecord:
        return self._records.get(slug, ZoneLearningRecord.unlearned())

    def _save(self, slug: str, record: ZoneLearningRecord) -> None:
        error = self._errors.get(slug)
        if error is not None:
            raise error

        self._records[slug] = record
