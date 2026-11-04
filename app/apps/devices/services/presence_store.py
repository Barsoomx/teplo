import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from apps.devices.services.state_store import UNAVAILABLE_AFTER


@dataclass(frozen=True, kw_only=True)
class PresenceSnapshot:
    slug: str
    present: bool | None
    read_at: datetime | None
    age: timedelta | None
    seen_age: timedelta | None
    watched_for: timedelta | None
    available: bool
    error_code: str | None
    error: str | None


@dataclass(frozen=True, kw_only=True)
class _Report:
    present: bool
    read_at: datetime
    recorded_at: float
    order: int


class PresenceStore:
    def __init__(self, unavailable_after: timedelta, clock: Callable[[], float] = time.monotonic) -> None:
        self._unavailable_after = unavailable_after
        self._clock = clock
        self._reports: dict[str, _Report] = {}
        self._seen_at: dict[str, float] = {}
        self._watched_since: dict[str, float] = {}
        self._failures: dict[str, tuple[str, str]] = {}
        self._failed_at: dict[str, int] = {}
        self._reported: dict[str, bool] = {}
        self._order = 0
        self._lock = threading.Lock()

    def record(self, slug: str, present: bool, read_at: datetime) -> None:
        with self._lock:
            now = self._clock()
            watched = self._available(slug, now)
            self._order += 1
            self._reports[slug] = _Report(present=present, read_at=read_at, recorded_at=now, order=self._order)
            if present:
                self._seen_at[slug] = now
            if not watched:
                self._watched_since[slug] = now
            self._failures.pop(slug, None)

    def record_failure(self, slug: str, error_code: str, error: str) -> None:
        with self._lock:
            self._order += 1
            self._failures[slug] = (error_code, error)
            self._failed_at[slug] = self._order

    def clear_failure(self, slug: str, error_code: str) -> None:
        with self._lock:
            if slug in self._failures and self._failures[slug][0] == error_code:
                del self._failures[slug]

    def snapshot(self, slug: str) -> PresenceSnapshot:
        with self._lock:
            now = self._clock()
            report = self._reports.get(slug)
            available = self._available(slug, now)
            seen_at = self._seen_at.get(slug)
            watched_since = self._watched_since.get(slug)
            error_code, error = self._failures.get(slug, (None, None))
        return PresenceSnapshot(
            slug=slug,
            present=None if report is None else report.present,
            read_at=None if report is None else report.read_at,
            age=None if report is None else _since(report.recorded_at, now),
            seen_age=None if seen_at is None else _since(seen_at, now),
            watched_for=_since(watched_since, now) if available and watched_since is not None else None,
            available=available,
            error_code=error_code,
            error=error,
        )

    def last_reported(self, slug: str) -> bool | None:
        with self._lock:
            return self._reported.get(slug)

    def mark_reported(self, slug: str, available: bool) -> None:
        with self._lock:
            self._reported[slug] = available

    def _available(self, slug: str, now: float) -> bool:
        report = self._reports.get(slug)
        if report is None or _since(report.recorded_at, now) > self._unavailable_after:
            return False

        return report.order > self._failed_at.get(slug, 0)


def _since(moment: float, now: float) -> timedelta:
    return timedelta(seconds=now - moment)


presence_store = PresenceStore(unavailable_after=UNAVAILABLE_AFTER)
