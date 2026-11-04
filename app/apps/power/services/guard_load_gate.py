import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

import structlog

from apps.climate.domain.drive import StartAnswer, ZoneStart
from apps.climate.domain.fast_source import SourceStart
from apps.power.domain.control import starts_live
from apps.power.domain.events import StartWouldWaitDryRun
from apps.power.domain.guard import GuardState, start_view
from apps.power.domain.load_status import LoadStatus
from apps.power.domain.loads import LoadPhase, LoadRecord
from apps.power.domain.meter_watch import meter_condition
from apps.power.domain.start_gate import StartDecision, StartRequest
from apps.power.services.guard_book import GuardBook, guard_book

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, kw_only=True)
class KeyedStart:
    key: str
    writes_live: bool


class GuardLoadGate:
    def __init__(self, book: GuardBook, clock: Callable[[], float]) -> None:
        self._book = book
        self._clock = clock

    @classmethod
    def init(cls) -> 'GuardLoadGate':
        return cls(book=guard_book, clock=time.monotonic)

    def held_zones(self) -> frozenset[str]:
        return self._book.held_zones()

    def held_sources(self) -> frozenset[str]:
        return self._book.held_sources()

    def gated_zones(self) -> frozenset[str]:
        state = self._book.state()
        if state is None:
            return frozenset()

        return frozenset(
            record.zone_slug
            for record in state.loads.records
            if record.zone_slug is not None and starts_live(state.loads.control, record, writes_live=True)
        )

    def gated_sources(self) -> frozenset[str]:
        state = self._book.state()
        if state is None:
            return frozenset()

        return frozenset(
            record.source_slug
            for record in state.loads.records
            if record.source_slug is not None
            and record.source_slug in state.loads.live_sources
            and starts_live(state.loads.control, record, writes_live=True)
        )

    def ask_starts(self, starts: Sequence[ZoneStart], now: datetime) -> dict[str, StartAnswer]:
        state = self._book.state()
        if state is None:
            return {}

        records = {record.zone_slug: record for record in state.loads.records if record.zone_slug is not None}
        keyed = [KeyedStart(key=start.zone, writes_live=start.writes_live) for start in starts]
        return self._ask(state, keyed, records, now)

    def ask_source_starts(self, starts: Sequence[SourceStart], now: datetime) -> dict[str, StartAnswer]:
        state = self._book.state()
        if state is None:
            return {}

        records = {record.source_slug: record for record in state.loads.records if record.source_slug is not None}
        keyed = [KeyedStart(key=start.source, writes_live=start.writes_live) for start in starts]
        return self._ask(state, keyed, records, now)

    def note_started(self, zone: str) -> None:
        state = self._book.state()
        if state is None:
            return

        self._note(next((record for record in state.loads.records if record.zone_slug == zone), None))

    def note_source_started(self, source: str) -> None:
        state = self._book.state()
        if state is None:
            return

        self._note(next((record for record in state.loads.records if record.source_slug == source), None))

    def _note(self, record: LoadRecord | None) -> None:
        if record is not None:
            self._book.note_started(record.slug, self._clock())

    def _ask(
        self,
        state: GuardState,
        starts: Sequence[KeyedStart],
        records: Mapping[str, LoadRecord],
        now: datetime,
    ) -> dict[str, StartAnswer]:
        at = self._clock()
        thresholds = state.loads.control.thresholds
        live_view = start_view(state, state.shedding, live=True)
        requests = _requests(state, starts, records, live_view.loads)
        decisions = self._book.serve_starts(
            tuple(requests.values()),
            meter=meter_condition(state.phases.meter, at, thresholds),
            live_view=live_view,
            dry_run_view=start_view(state, state.shedding, live=False),
            at=at,
            thresholds=thresholds,
        )
        began = self._book.note_waiting(
            frozenset(record.slug for record in records.values()),
            frozenset(slug for slug, decision in decisions.items() if not decision.granted),
        )
        by_slug = {record.slug: record for record in state.loads.records}
        return {
            key: _answer(
                by_slug[request.load.slug],
                request,
                decisions[request.load.slug],
                began=request.load.slug in began,
                estimated=state.loads.control.estimate.phase,
                now=now,
            )
            for key, request in requests.items()
        }


def _requests(
    state: GuardState,
    starts: Sequence[KeyedStart],
    records: Mapping[str, LoadRecord],
    statuses: Sequence[LoadStatus],
) -> dict[str, StartRequest]:
    by_slug = {status.slug: status for status in statuses}
    requests: dict[str, StartRequest] = {}
    for start in starts:
        record = records.get(start.key)
        if record is None:
            continue

        live = starts_live(state.loads.control, record, writes_live=start.writes_live)
        if live or state.phases.meter.seen:
            requests[start.key] = StartRequest(
                load=by_slug[record.slug],
                live=live,
                handed_back=state.shedding.handed_back.get(record.slug),
            )
    return requests


def _answer(
    record: LoadRecord,
    request: StartRequest,
    decision: StartDecision,
    *,
    began: bool,
    estimated: LoadPhase | None,
    now: datetime,
) -> StartAnswer:
    refusal = decision.refusal
    if refusal is None:
        return StartAnswer(granted=True, note=None)

    if began:
        logger.info('load_start_waits', load=record.slug, live=request.live, reason=refusal)
    if request.live:
        return StartAnswer(granted=False, note=None)

    if not began:
        return StartAnswer(granted=True, note=None)

    overrun = decision.overrun
    phase = request.load.phase if overrun is None else overrun.phase
    return StartAnswer(
        granted=True,
        note=StartWouldWaitDryRun(
            load=record.slug,
            name=record.name,
            phase=phase,
            amps=None if overrun is None else overrun.amps,
            load_amps=request.load.amps,
            reason=refusal,
            estimated=phase is estimated,
            occurred_at=now,
        ),
    )
