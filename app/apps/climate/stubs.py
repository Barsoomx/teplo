import threading
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from decimal import Decimal

from apps.climate.domain.climate_config import ClimateConfig, ClimateConfigRecord
from apps.climate.domain.comfort_windows import StoredWindow, ZoneWeek
from apps.climate.domain.drive import StartAnswer, ZoneStart
from apps.climate.domain.errors import ComfortWindowNotFoundError, ZoneNotFoundError
from apps.climate.domain.learning import HeatUpModel, ZoneLearningRecord
from apps.climate.domain.zone import ComfortWindow, ZoneSettings
from apps.climate.domain.zone_config import ZoneConfig, ZoneConfigRecord

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


class ZoneConfigStoreStub:
    def __init__(self, records: list[ZoneConfigRecord]) -> None:
        self._records = records
        self._saved: list[tuple[str, ZoneConfig, tuple[str, ...]]] = []

    @property
    def saved(self) -> list[tuple[str, ZoneConfig, tuple[str, ...]]]:
        return self._saved

    def config_records(self) -> list[ZoneConfigRecord]:
        return list(self._records)

    def config_for_update(self, slug: str) -> ZoneConfig:
        record = next((record for record in self._records if record.slug == slug), None)
        if record is None:
            raise ZoneNotFoundError(slug)

        return record.config

    def save_config(self, slug: str, config: ZoneConfig, fields: Collection[str]) -> None:
        self._saved.append((slug, config, tuple(fields)))
        written = {field: getattr(config, field) for field in fields}
        self._records = [
            replace(record, config=replace(record.config, **written)) if record.slug == slug else record
            for record in self._records
        ]


class ClimateConfigStoreStub:
    def __init__(self, record: ClimateConfigRecord) -> None:
        self._record = record
        self._saved: list[tuple[ClimateConfig, tuple[str, ...]]] = []

    @property
    def saved(self) -> list[tuple[ClimateConfig, tuple[str, ...]]]:
        return self._saved

    def config_record(self) -> ClimateConfigRecord:
        return self._record

    def config_record_for_update(self) -> ClimateConfigRecord:
        return self._record

    def save_config(self, config: ClimateConfig, fields: Collection[str]) -> None:
        self._saved.append((config, tuple(fields)))
        written = {field: getattr(config, field) for field in fields}
        self._record = replace(self._record, config=replace(self._record.config, **written))


class ComfortWindowStoreStub:
    def __init__(self, weeks: list[ZoneWeek]) -> None:
        self._weeks = weeks
        self._replaced: list[tuple[str, dict[int, tuple[ComfortWindow, ...]]]] = []

    @property
    def replaced(self) -> list[tuple[str, dict[int, tuple[ComfortWindow, ...]]]]:
        return self._replaced

    def weeks(self) -> list[ZoneWeek]:
        return list(self._weeks)

    def week_for_update(self, slug: str) -> ZoneWeek:
        week = next((week for week in self._weeks if week.slug == slug), None)
        if week is None:
            raise ZoneNotFoundError(slug)

        return week

    def window_week_for_update(self, window_id: int) -> ZoneWeek:
        week = next((week for week in self._weeks if week.stored(window_id) is not None), None)
        if week is None:
            raise ComfortWindowNotFoundError(window_id)

        return week

    def replace_days(self, slug: str, days: Mapping[int, Sequence[ComfortWindow]]) -> None:
        self._replaced.append((slug, {weekday: tuple(windows) for weekday, windows in days.items()}))
        week = self.week_for_update(slug)
        next_id = max((stored.id for each in self._weeks for stored in each.windows), default=0) + 1
        kept = tuple(stored for stored in week.windows if stored.weekday not in days)
        placed = [(weekday, window) for weekday in sorted(days) for window in days[weekday]]
        added = tuple(
            StoredWindow(id=next_id + offset, weekday=weekday, start=window.start, end=window.end)
            for offset, (weekday, window) in enumerate(placed)
        )
        self._weeks = [replace(each, windows=kept + added) if each.slug == slug else each for each in self._weeks]


class ContendedLockStub:
    def __init__(self, contended: Callable[[], None]) -> None:
        self._lock = threading.Lock()
        self._contended = contended

    def __enter__(self) -> None:
        if self._lock.acquire(blocking=False):
            return

        self._contended()
        self._lock.acquire()

    def __exit__(self, *_exc: object) -> None:
        self._lock.release()
