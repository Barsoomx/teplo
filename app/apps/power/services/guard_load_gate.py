import time
from collections.abc import Callable, Sequence
from datetime import datetime

import structlog

from apps.climate.domain.drive import StartAnswer, ZoneStart
from apps.power.domain.control import starts_live
from apps.power.domain.events import StartWouldWaitDryRun
from apps.power.domain.guard import GuardState, load_statuses, relieved_currents
from apps.power.domain.load_status import LoadStatus
from apps.power.domain.loads import LoadRecord
from apps.power.domain.meter_watch import meter_condition
from apps.power.domain.start_gate import StartDecision, StartRequest
from apps.power.services.guard_book import GuardBook, guard_book

logger = structlog.get_logger(__name__)


class GuardLoadGate:
    def __init__(self, book: GuardBook, clock: Callable[[], float]) -> None:
        self._book = book
        self._clock = clock

    @classmethod
    def init(cls) -> 'GuardLoadGate':
        return cls(book=guard_book, clock=time.monotonic)

    def held_zones(self) -> frozenset[str]:
        return self._book.held_zones()

    def ask_starts(self, starts: Sequence[ZoneStart], now: datetime) -> dict[str, StartAnswer]:
        state = self._book.state()
        if state is None:
            return {}

        at = self._clock()
        thresholds = state.loads.control.thresholds
        statuses = load_statuses(state.loads, state.readings, state.shedding)
        requests = _requests(state, starts, statuses)
        decisions = self._book.serve_starts(
            tuple(requests.values()),
            meter=meter_condition(state.phases.meter, at, thresholds),
            currents=relieved_currents(state.phases.currents, state.shedding.sheds.values(), at),
            loads=statuses,
            at=at,
            thresholds=thresholds,
        )
        began = self._book.note_waiting(
            frozenset(slug for slug, decision in decisions.items() if not decision.granted),
        )
        records = {record.slug: record for record in state.loads.records}
        return {
            zone: _answer(
                records[request.load.slug],
                request,
                decisions[request.load.slug],
                began=request.load.slug in began,
                now=now,
            )
            for zone, request in requests.items()
        }


def _requests(
    state: GuardState,
    starts: Sequence[ZoneStart],
    statuses: Sequence[LoadStatus],
) -> dict[str, StartRequest]:
    records = {record.zone_slug: record for record in state.loads.records if record.zone_slug is not None}
    by_slug = {status.slug: status for status in statuses}
    requests: dict[str, StartRequest] = {}
    for start in starts:
        record = records.get(start.zone)
        if record is None:
            continue

        live = starts_live(state.loads.control, record, writes_live=start.writes_live)
        if live or state.phases.meter.seen:
            requests[start.zone] = StartRequest(load=by_slug[record.slug], live=live)
    return requests


def _answer(
    record: LoadRecord,
    request: StartRequest,
    decision: StartDecision,
    *,
    began: bool,
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
    return StartAnswer(
        granted=True,
        note=StartWouldWaitDryRun(
            load=record.slug,
            name=record.name,
            phase=request.load.phase if overrun is None else overrun.phase,
            amps=None if overrun is None else overrun.amps,
            load_amps=request.load.amps,
            reason=refusal,
            occurred_at=now,
        ),
    )
