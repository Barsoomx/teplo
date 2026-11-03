from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from decimal import Decimal

from apps.climate.domain.drive import StartAnswer, ZoneStart
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
    def __init__(
        self,
        records: dict[str, ZoneLearningRecord],
        errors: dict[str, Exception],
        read_errors: tuple[Exception, ...],
    ) -> None:
        self._records = records
        self._errors = errors
        self._read_errors = read_errors
        self._reads = 0

    @property
    def records(self) -> dict[str, ZoneLearningRecord]:
        return self._records

    def all_records(self) -> dict[str, ZoneLearningRecord]:
        self._reads += 1
        if self._reads <= len(self._read_errors):
            raise self._read_errors[self._reads - 1]

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


class LoadGateStub:
    def __init__(self, held: frozenset[str], gated: frozenset[str], answers: dict[str, StartAnswer]) -> None:
        self._held = held
        self._gated = gated
        self._answers = answers
        self._asked: list[tuple[ZoneStart, ...]] = []

    @property
    def asked(self) -> list[tuple[ZoneStart, ...]]:
        return self._asked

    def hold(self, zone: str) -> None:
        self._held = self._held | {zone}

    def release(self, zone: str) -> None:
        self._held = self._held - {zone}

    def held_zones(self) -> frozenset[str]:
        return self._held

    def gated_zones(self) -> frozenset[str]:
        return self._gated

    def ask_starts(self, starts: Sequence[ZoneStart], now: datetime) -> dict[str, StartAnswer]:  # noqa: ARG002
        self._asked.append(tuple(starts))
        return {start.zone: self._answers[start.zone] for start in starts if start.zone in self._answers}
