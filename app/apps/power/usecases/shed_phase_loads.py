import time
from collections.abc import Callable, Collection
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal

import structlog
from django.conf import settings
from django.db import OperationalError

from apps.climate.domain.write_guard import MIN_WRITE_INTERVAL
from apps.core.domain.errors import DomainError
from apps.core.domain.events import EventDispatcher, event_dispatcher
from apps.core.domain.usecases.base import BaseUseCase
from apps.power.domain.control import PhaseThresholds, commands_live
from apps.power.domain.events import (
    LoadCommand,
    LoadCommandFailed,
    LoadRestored,
    LoadRestoredDryRun,
    LoadRestoreEvent,
    LoadShed,
    LoadShedDryRun,
    LoadShedEvent,
    LoadShedOverridden,
    RestoreCause,
)
from apps.power.domain.guard import GuardLoads, GuardState, ShedState, awaits_reading, guard_view
from apps.power.domain.load_status import LoadReading, LoadStatus
from apps.power.domain.loads import MEASURED_PHASES, LiveShed, LoadKind, LoadPhase, LoadRecord, ShedReason
from apps.power.domain.meter_watch import MeterCondition, limp_sheds, meter_condition
from apps.power.domain.moments import elapsed
from apps.power.domain.phase_watch import PhaseWatch, calm_phases, shed_due, with_shed
from apps.power.domain.ports import LoadStore
from apps.power.domain.shedding import (
    Shed,
    can_hand_back,
    choose_restore,
    choose_shed,
    new_shed,
    resumed_shed,
    shed_confirmed,
    switched_back_on,
    unconfirmed_shed,
)
from apps.power.domain.start_gate import decide_start
from apps.power.domain.view import GuardView
from apps.power.repositories.load import LoadRepository
from apps.power.services.guard_book import GuardBook, guard_book
from apps.power.services.load_switcher import LoadSwitcher

logger = structlog.get_logger(__name__)

LIVE_FIRST = (True, False)
SAVE_RETRY = timedelta(minutes=1)


@dataclass(frozen=True, kw_only=True)
class ShedPhaseLoadsInputDTO:
    now: datetime


@dataclass(frozen=True, kw_only=True)
class ShedPhaseLoadsOutputDTO:
    shed: tuple[str, ...]
    restored: tuple[str, ...]


class ShedPhaseLoadsUseCase(BaseUseCase[ShedPhaseLoadsInputDTO, ShedPhaseLoadsOutputDTO]):
    def __init__(
        self,
        loads: LoadStore,
        switcher: LoadSwitcher,
        book: GuardBook,
        clock: Callable[[], float],
        dry_run: bool,
        dispatcher: EventDispatcher = event_dispatcher,
    ) -> None:
        super().__init__(dispatcher=dispatcher)
        self._loads = loads
        self._switcher = switcher
        self._book = book
        self._clock = clock
        self._dry_run = dry_run

    @classmethod
    def init(cls) -> 'ShedPhaseLoadsUseCase':
        return cls(
            loads=LoadRepository(),
            switcher=LoadSwitcher.init(),
            book=guard_book,
            clock=time.monotonic,
            dry_run=settings.TEPLO_DRY_RUN,
        )

    def _execute(self, input_dto: ShedPhaseLoadsInputDTO) -> ShedPhaseLoadsOutputDTO:
        state = self._book.state()
        if state is None:
            return ShedPhaseLoadsOutputDTO(shed=(), restored=())

        at = self._clock()
        now = input_dto.now
        resumed = self._resumed(state, at, now)
        shedding = self._confirmed(state, resumed, now)
        shedding = self._overridden(state, shedding, now)
        shedding = self._released(state, shedding, at, now)
        watches = dict(state.phases.watches)
        for live in LIVE_FIRST:
            watches, shedding = self._shed_overloads(state, watches, shedding, live=live, at=at, now=now)
            shedding = self._shed_limp(state, shedding, live=live, at=at, now=now)
            shedding = self._restored(state, shedding, live=live, at=at, now=now)
        shedding = self._saved(shedding, at)
        self._book.remember(replace(state, phases=replace(state.phases, watches=watches), shedding=shedding))
        return ShedPhaseLoadsOutputDTO(
            shed=tuple(slug for slug in shedding.sheds if slug not in resumed.sheds),
            restored=tuple(slug for slug in resumed.sheds if slug not in shedding.sheds),
        )

    def _resumed(self, state: GuardState, at: float, now: datetime) -> ShedState:
        shedding = state.shedding
        if shedding.resumed:
            return shedding

        if self._dry_run:
            return replace(shedding, resumed=True)

        default_amps = state.loads.control.default_amps
        resumed = {
            record.slug: shed
            for record in state.loads.records
            if (shed := resumed_shed(record, at=at, now=now, default_amps=default_amps)) is not None
        }
        if resumed:
            logger.info('load_sheds_resumed', loads=sorted(resumed))
        return replace(shedding, sheds={**resumed, **shedding.sheds}, resumed=True)

    def _confirmed(self, state: GuardState, shedding: ShedState, now: datetime) -> ShedState:
        for slug, unconfirmed in tuple(shedding.unconfirmed.items()):
            record = state.loads.record(slug)
            confirmed = shed_confirmed(unconfirmed, _reading(state, slug))
            if record is not None and confirmed is None:
                continue

            shedding = replace(
                shedding,
                unconfirmed={key: kept for key, kept in shedding.unconfirmed.items() if key != slug},
            )
            if record is None or not confirmed:
                continue

            shed = unconfirmed.shed
            logger.info('load_shed_confirmed', load=slug)
            self._event_store.add_event(_shed_event(LoadShed, record, shed, amps=unconfirmed.amps, now=now))
            shedding = _stored(
                replace(shedding, sheds={**shedding.sheds, slug: shed}),
                slug,
                LiveShed(at=unconfirmed.at, reason=shed.reason, phase=shed.phase, was_on=shed.was_on),
            )
        return shedding

    def _overridden(self, state: GuardState, shedding: ShedState, now: datetime) -> ShedState:
        taken = [shed for shed in shedding.sheds.values() if switched_back_on(shed, _reading(state, shed.load))]
        for shed in taken:
            logger.info('load_shed_overridden', load=shed.load)
            self._event_store.add_event(
                LoadShedOverridden(
                    load=shed.load,
                    name=_name(state.loads, shed.load),
                    phase=shed.phase,
                    occurred_at=now,
                ),
            )
        kept = {slug: phase for slug, phase in shedding.overridden.items() if _overloaded(state, phase)}
        return replace(
            _without(shedding, {shed.load for shed in taken}),
            overridden={**kept, **{shed.load: shed.phase for shed in taken}},
            unsaved={**shedding.unsaved, **{shed.load: None for shed in taken}},
        )

    def _released(self, state: GuardState, shedding: ShedState, at: float, now: datetime) -> ShedState:
        for shed in tuple(shedding.sheds.values()):
            record = state.loads.record(shed.load)
            if record is None:
                logger.warning('load_shed_forgotten', load=shed.load, live=shed.live)
                shedding = _without(shedding, {shed.load})
                continue

            if shed.live != self._live(state.loads, record):
                shedding = self._release(state, shedding, record, shed, at, now)
        return shedding

    def _release(
        self,
        state: GuardState,
        shedding: ShedState,
        record: LoadRecord,
        shed: Shed,
        at: float,
        now: datetime,
    ) -> ShedState:
        if shed.live:
            if not self._release_due(state, shedding, record, shed, at):
                return shedding

            try:
                self._switcher.release(
                    record,
                    was_on=shed.was_on,
                    zone_writes_live=self._zone_writes_live(state.loads, record),
                    at=now,
                )
            except DomainError as error:
                return self._restore_failed(shedding, record, shed, error, now)

            logger.info('load_released', load=record.slug)
            shedding = _stored(shedding, record.slug, None)
        self._event_store.add_event(
            _restore_event(
                LoadRestored if shed.live else LoadRestoredDryRun,
                record,
                shed,
                amps=state.phases.currents.get(shed.phase),
                cause=RestoreCause.RELEASED,
                now=now,
            ),
        )
        return _without(shedding, {record.slug})

    def _shed_overloads(
        self,
        state: GuardState,
        watches: dict[LoadPhase, PhaseWatch],
        shedding: ShedState,
        *,
        live: bool,
        at: float,
        now: datetime,
    ) -> tuple[dict[LoadPhase, PhaseWatch], ShedState]:
        thresholds = state.loads.control.thresholds
        watched = dict(watches)
        for phase in MEASURED_PHASES:
            view = guard_view(state, shedding, live=live)
            if not shed_due(watched[phase].overload(live=live), view.currents.get(phase), at, thresholds):
                continue

            load = choose_shed(phase, self._candidates(state, shedding, view, live=live))
            if load is None:
                continue

            watched[phase] = with_shed(watched[phase], at, live=live)
            shedding = self._shed(
                state,
                shedding,
                load,
                phase=phase,
                reason=ShedReason.OVERLOAD,
                amps=state.phases.currents.get(phase),
                live=live,
                at=at,
                now=now,
            )
        return watched, shedding

    def _shed_limp(self, state: GuardState, shedding: ShedState, *, live: bool, at: float, now: datetime) -> ShedState:
        thresholds = state.loads.control.thresholds
        meter = state.phases.meter
        if meter_condition(meter, at, thresholds) is not MeterCondition.LOST or not (live or meter.seen):
            return shedding

        times = shedding.times(live=live)
        if not elapsed(times.last_limp_shed_at, at, thresholds.shed_interval):
            return shedding

        view = guard_view(state, shedding, live=live)
        sheddable = {status.slug for status in self._candidates(state, shedding, view, live=live)}
        load = next((load for load in limp_sheds(view.loads) if load.slug in sheddable), None)
        if load is None:
            return shedding

        return self._shed(
            state,
            shedding.with_times(replace(times, last_limp_shed_at=at), live=live),
            load,
            phase=load.phase,
            reason=ShedReason.METER_LOST,
            amps=None,
            live=live,
            at=at,
            now=now,
        )

    def _shed(
        self,
        state: GuardState,
        shedding: ShedState,
        load: LoadStatus,
        *,
        phase: LoadPhase,
        reason: ShedReason,
        amps: Decimal | None,
        live: bool,
        at: float,
        now: datetime,
    ) -> ShedState:
        record = state.loads.record(load.slug)
        if record is None:
            return shedding

        was_on = True if record.kind is LoadKind.PLUG else None
        stamp_lost = False
        if live:
            try:
                stamp_lost = not self._switcher.shed(record, now)
            except DomainError as error:
                return self._shed_failed(shedding, record, load, error, phase=phase, reason=reason, amps=amps, now=now)

            logger.info('load_shed', load=record.slug, stamp_lost=stamp_lost)

        shed = replace(
            new_shed(load, phase=phase, live=live, reason=reason, was_on=was_on, at=self._clock() if live else at),
            stamp_lost=stamp_lost,
        )
        self._event_store.add_event(
            _shed_event(LoadShed if live else LoadShedDryRun, record, shed, amps=amps, now=now),
        )
        shedding = replace(shedding, sheds={**shedding.sheds, record.slug: shed})
        if not live:
            return shedding

        return _stored(shedding, record.slug, LiveShed(at=now, reason=reason, phase=phase, was_on=was_on))

    def _restored(self, state: GuardState, shedding: ShedState, *, live: bool, at: float, now: datetime) -> ShedState:
        thresholds = state.loads.control.thresholds
        if meter_condition(state.phases.meter, at, thresholds) is not MeterCondition.FRESH:
            return shedding

        view = guard_view(state, shedding, live=live)
        times = shedding.times(live=live)
        shed = choose_restore(
            tuple(
                shed
                for shed in shedding.sheds.values()
                if shed.live == live
                and self._live_load(state.loads, shed.load) == live
                and can_hand_back(shed, at, MIN_WRITE_INTERVAL)
            ),
            view.loads,
            calm=calm_phases(state.phases.watches, at, thresholds, live=live),
            currents=view.currents,
            last_restore_at=times.last_restore_at,
            at=at,
            thresholds=thresholds,
        )
        record = None if shed is None else state.loads.record(shed.load)
        if shed is None or record is None:
            return shedding

        load = next(status for status in view.loads if status.slug == record.slug)
        if record.kind is LoadKind.PLUG and not self._would_start(load, view, at, thresholds):
            return shedding

        restored = shedding.with_times(replace(times, last_restore_at=at), live=live)
        if live:
            try:
                if not self._switcher.restore(
                    record,
                    was_on=shed.was_on,
                    permit=lambda: self._permitted(load, view, at, thresholds),
                ):
                    return shedding
            except DomainError as error:
                return self._restore_failed(restored, record, shed, error, now)

            logger.info('load_restored', load=record.slug)
            restored = _stored(restored, record.slug, None)

        self._event_store.add_event(
            _restore_event(
                LoadRestored if live else LoadRestoredDryRun,
                record,
                shed,
                amps=view.currents.get(shed.phase),
                cause=RestoreCause.CALM,
                now=now,
            ),
        )
        return _without(restored, {record.slug})

    def _candidates(
        self,
        state: GuardState,
        shedding: ShedState,
        view: GuardView,
        *,
        live: bool,
    ) -> list[LoadStatus]:
        return [
            status
            for status in view.loads
            if status.slug not in shedding.overridden and self._live_load(state.loads, status.slug) == live
        ]

    def _permitted(self, load: LoadStatus, view: GuardView, at: float, thresholds: PhaseThresholds) -> bool:
        decision = self._book.permit_start(
            load,
            meter=MeterCondition.FRESH,
            view=view,
            at=at,
            thresholds=thresholds,
        )
        return decision.granted

    def _would_start(self, load: LoadStatus, view: GuardView, at: float, thresholds: PhaseThresholds) -> bool:
        decision = decide_start(
            load,
            meter=MeterCondition.FRESH,
            view=view,
            last_permit_at=self._book.last_permit_at(),
            at=at,
            thresholds=thresholds,
        )
        return decision.granted

    def _shed_failed(
        self,
        shedding: ShedState,
        record: LoadRecord,
        load: LoadStatus,
        error: DomainError,
        *,
        phase: LoadPhase,
        reason: ShedReason,
        amps: Decimal | None,
        now: datetime,
    ) -> ShedState:
        self._failed(record, LoadCommand.SHED, error, now, journaled=True)
        failed_at = self._clock()
        failed = replace(shedding, failed={**shedding.failed, record.slug: failed_at})
        if record.kind is not LoadKind.PLUG:
            return failed

        unconfirmed = unconfirmed_shed(load, phase=phase, reason=reason, amps=amps, at=failed_at, now=now)
        return replace(failed, unconfirmed={**failed.unconfirmed, record.slug: unconfirmed})

    def _restore_failed(
        self,
        shedding: ShedState,
        record: LoadRecord,
        shed: Shed,
        error: DomainError,
        now: datetime,
    ) -> ShedState:
        self._failed(record, LoadCommand.RESTORE, error, now, journaled=not shed.restore_failed)
        return replace(
            shedding,
            sheds={**shedding.sheds, record.slug: replace(shed, restore_failed=True)},
            failed={**shedding.failed, record.slug: self._clock()},
        )

    def _failed(
        self,
        record: LoadRecord,
        command: LoadCommand,
        error: DomainError,
        now: datetime,
        *,
        journaled: bool,
    ) -> None:
        logger.warning(
            'load_command_failed',
            load=record.slug,
            device=record.device_slug,
            command=command,
            error_code=error.error_code,
            detail=error.detail,
        )
        if not journaled:
            return

        self._event_store.add_event(
            LoadCommandFailed(
                load=record.slug,
                name=record.name,
                command=command,
                error=error.detail,
                error_code=error.error_code,
                occurred_at=now,
            ),
        )

    def _saved(self, shedding: ShedState, at: float) -> ShedState:
        if not shedding.unsaved or not elapsed(shedding.save_failed_at, at, SAVE_RETRY):
            return shedding

        unsaved = dict(shedding.unsaved)
        for slug, stored in shedding.unsaved.items():
            try:
                self._store(slug, stored)
            except OperationalError as error:
                logger.warning('load_shed_save_failed', load=slug, error=str(error))
                return replace(shedding, unsaved=unsaved, save_failed_at=at)

            del unsaved[slug]
        return replace(shedding, unsaved={}, save_failed_at=None)

    def _store(self, slug: str, stored: LiveShed | None) -> None:
        if stored is None:
            self._loads.clear_shed(slug)
            return

        self._loads.mark_shed(slug, stored)

    def _live(self, loads: GuardLoads, record: LoadRecord) -> bool:
        return commands_live(
            loads.control,
            record,
            dry_run=self._dry_run,
            zone_live_control=record.zone_slug in loads.live_zones,
        )

    def _live_load(self, loads: GuardLoads, slug: str) -> bool:
        record = loads.record(slug)
        return record is not None and self._live(loads, record)

    def _zone_writes_live(self, loads: GuardLoads, record: LoadRecord) -> bool:
        return not self._dry_run and record.zone_slug in loads.live_zones

    def _release_due(self, state: GuardState, shedding: ShedState, record: LoadRecord, shed: Shed, at: float) -> bool:
        if awaits_reading(state, shedding, record.slug):
            return False

        if record.kind is LoadKind.PLUG:
            return True

        if self._zone_writes_live(state.loads, record):
            return can_hand_back(shed, at, MIN_WRITE_INTERVAL)

        return elapsed(shed.at, at, MIN_WRITE_INTERVAL)


def _reading(state: GuardState, slug: str) -> LoadReading | None:
    seen = state.readings.get(slug)
    return None if seen is None else seen.reading


def _overloaded(state: GuardState, phase: LoadPhase) -> bool:
    watch = state.phases.watches.get(phase)
    return watch is not None and watch.live.overloaded


def _name(loads: GuardLoads, slug: str) -> str:
    record = loads.record(slug)
    return slug if record is None else record.name


def _without(shedding: ShedState, slugs: Collection[str]) -> ShedState:
    return replace(shedding, sheds={slug: shed for slug, shed in shedding.sheds.items() if slug not in slugs})


def _stored(shedding: ShedState, slug: str, stored: LiveShed | None) -> ShedState:
    return replace(shedding, unsaved={**shedding.unsaved, slug: stored})


def _shed_event(
    event_type: type[LoadShedEvent],
    record: LoadRecord,
    shed: Shed,
    *,
    amps: Decimal | None,
    now: datetime,
) -> LoadShedEvent:
    return event_type(
        load=record.slug,
        name=record.name,
        kind=record.kind,
        phase=shed.phase,
        amps=amps,
        load_amps=shed.amps,
        reason=shed.reason,
        occurred_at=now,
    )


def _restore_event(
    event_type: type[LoadRestoreEvent],
    record: LoadRecord,
    shed: Shed,
    *,
    amps: Decimal | None,
    cause: RestoreCause,
    now: datetime,
) -> LoadRestoreEvent:
    return event_type(
        load=record.slug,
        name=record.name,
        kind=record.kind,
        phase=shed.phase,
        amps=amps,
        load_amps=shed.amps,
        reason=shed.reason,
        cause=cause,
        occurred_at=now,
    )
