import time
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal

import structlog
from django.conf import settings

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
from apps.power.domain.guard import GuardLoads, GuardState, ShedState, load_statuses, relieved_currents
from apps.power.domain.load_status import LoadReading, LoadStatus
from apps.power.domain.loads import MEASURED_PHASES, LiveShed, LoadKind, LoadPhase, LoadRecord, ShedReason
from apps.power.domain.meter_watch import MeterCondition, limp_sheds, meter_condition
from apps.power.domain.moments import elapsed
from apps.power.domain.phase_watch import PhaseWatch, calm_phases, shed_due, with_shed
from apps.power.domain.ports import LoadStore
from apps.power.domain.shedding import (
    Shed,
    choose_restore,
    choose_shed,
    new_shed,
    relief,
    resumed_shed,
    switched_back_on,
)
from apps.power.domain.start_gate import decide_start
from apps.power.repositories.load import LoadRepository
from apps.power.services.guard_book import GuardBook, guard_book
from apps.power.services.load_switcher import LoadSwitcher

logger = structlog.get_logger(__name__)


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
        resumed = self._resumed(state, at)
        shedding = self._overridden(state, resumed, now)
        shedding = self._released(state, shedding, at, now)
        watches, shedding = self._shed_overloads(state, shedding, at, now)
        shedding = self._shed_limp(state, shedding, at, now)
        shedding = self._restored(state, shedding, at, now)
        self._book.remember(replace(state, phases=replace(state.phases, watches=watches), shedding=shedding))
        return ShedPhaseLoadsOutputDTO(
            shed=tuple(slug for slug in shedding.sheds if slug not in resumed.sheds),
            restored=tuple(slug for slug in resumed.sheds if slug not in shedding.sheds),
        )

    def _resumed(self, state: GuardState, at: float) -> ShedState:
        shedding = state.shedding
        if shedding.resumed:
            return shedding

        if self._dry_run:
            return replace(shedding, resumed=True)

        default_amps = state.loads.control.default_amps
        resumed = {
            record.slug: shed
            for record in state.loads.records
            if (shed := resumed_shed(record, at=at, default_amps=default_amps)) is not None
        }
        if resumed:
            logger.info('load_sheds_resumed', loads=sorted(resumed))
        return replace(shedding, sheds={**resumed, **shedding.sheds}, resumed=True)

    def _overridden(self, state: GuardState, shedding: ShedState, now: datetime) -> ShedState:
        taken = [shed for shed in shedding.sheds.values() if switched_back_on(shed, _reading(state, shed.load))]
        for shed in taken:
            self._loads.clear_shed(shed.load)
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
            if not elapsed(shedding.failed.get(record.slug), at, state.loads.control.thresholds.restore_interval):
                return shedding

            if not self._switched_on(record, shed, now):
                return replace(shedding, failed={**shedding.failed, record.slug: at})

            self._loads.clear_shed(record.slug)
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
        shedding: ShedState,
        at: float,
        now: datetime,
    ) -> tuple[dict[LoadPhase, PhaseWatch], ShedState]:
        thresholds = state.loads.control.thresholds
        watches = dict(state.phases.watches)
        for phase in MEASURED_PHASES:
            measured = state.phases.currents.get(phase)
            if not shed_due(watches[phase], measured, relief(shedding.sheds.values(), phase, at), at, thresholds):
                continue

            statuses = load_statuses(state.loads, state.readings, shedding)
            load = choose_shed(phase, [status for status in statuses if status.slug not in shedding.overridden])
            if load is None:
                continue

            watches[phase] = with_shed(watches[phase], at)
            shedding = self._shed(
                state,
                shedding,
                load,
                phase=phase,
                reason=ShedReason.OVERLOAD,
                amps=measured,
                at=at,
                now=now,
            )
        return watches, shedding

    def _shed_limp(self, state: GuardState, shedding: ShedState, at: float, now: datetime) -> ShedState:
        thresholds = state.loads.control.thresholds
        meter = state.phases.meter
        if meter_condition(meter, at, thresholds) is not MeterCondition.LOST:
            return shedding

        if not elapsed(shedding.last_limp_shed_at, at, thresholds.shed_interval):
            return shedding

        statuses = load_statuses(state.loads, state.readings, shedding)
        load = next(
            (load for load in limp_sheds(statuses) if meter.seen or self._live_load(state.loads, load.slug)),
            None,
        )
        if load is None:
            return shedding

        return self._shed(
            state,
            replace(shedding, last_limp_shed_at=at),
            load,
            phase=load.phase,
            reason=ShedReason.METER_LOST,
            amps=None,
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
        at: float,
        now: datetime,
    ) -> ShedState:
        record = state.loads.record(load.slug)
        if record is None:
            return shedding

        live = self._live(state.loads, record)
        was_on = True if record.kind is LoadKind.PLUG else None
        if live and not self._switched_off(record, now):
            return replace(shedding, failed={**shedding.failed, record.slug: at})

        if live:
            self._loads.mark_shed(record.slug, LiveShed(at=now, reason=reason, phase=phase, was_on=was_on))
        shed = new_shed(load, phase=phase, live=live, reason=reason, was_on=was_on, at=self._clock() if live else at)
        self._event_store.add_event(
            _shed_event(LoadShed if live else LoadShedDryRun, record, shed, amps=amps, now=now),
        )
        return replace(shedding, sheds={**shedding.sheds, record.slug: shed})

    def _restored(self, state: GuardState, shedding: ShedState, at: float, now: datetime) -> ShedState:
        thresholds = state.loads.control.thresholds
        if meter_condition(state.phases.meter, at, thresholds) is not MeterCondition.FRESH:
            return shedding

        statuses = load_statuses(state.loads, state.readings, shedding)
        currents = relieved_currents(state.phases.currents, shedding.sheds.values(), at)
        shed = choose_restore(
            tuple(shedding.sheds.values()),
            statuses,
            calm=calm_phases(state.phases.watches, at, thresholds),
            currents=currents,
            last_restore_at=shedding.last_restore_at,
            at=at,
            thresholds=thresholds,
        )
        record = None if shed is None else state.loads.record(shed.load)
        if shed is None or record is None:
            return shedding

        load = next(status for status in statuses if status.slug == record.slug)
        if record.kind is LoadKind.PLUG and not self._may_start(load, statuses, currents, shed.live, at, thresholds):
            return shedding

        if shed.live and not self._switched_on(record, shed, now):
            return replace(shedding, last_restore_at=at)

        if shed.live:
            self._loads.clear_shed(record.slug)
        self._event_store.add_event(
            _restore_event(
                LoadRestored if shed.live else LoadRestoredDryRun,
                record,
                shed,
                amps=state.phases.currents.get(shed.phase),
                cause=RestoreCause.CALM,
                now=now,
            ),
        )
        return replace(_without(shedding, {record.slug}), last_restore_at=at)

    def _may_start(
        self,
        load: LoadStatus,
        statuses: Sequence[LoadStatus],
        currents: Mapping[LoadPhase, Decimal],
        live: bool,
        at: float,
        thresholds: PhaseThresholds,
    ) -> bool:
        if live:
            decision = self._book.permit_start(
                load,
                meter=MeterCondition.FRESH,
                currents=currents,
                loads=statuses,
                at=at,
                thresholds=thresholds,
            )
            return decision.granted

        decision = decide_start(
            load,
            meter=MeterCondition.FRESH,
            currents=currents,
            loads=statuses,
            last_permit_at=self._book.last_permit_at(),
            at=at,
            thresholds=thresholds,
        )
        return decision.granted

    def _switched_off(self, record: LoadRecord, now: datetime) -> bool:
        try:
            self._switcher.shed(record, now)
        except DomainError as error:
            self._failed(record, LoadCommand.SHED, error, now)
            return False

        logger.info('load_shed', load=record.slug)
        return True

    def _switched_on(self, record: LoadRecord, shed: Shed, now: datetime) -> bool:
        try:
            self._switcher.restore(record, was_on=shed.was_on)
        except DomainError as error:
            self._failed(record, LoadCommand.RESTORE, error, now)
            return False

        logger.info('load_restored', load=record.slug)
        return True

    def _failed(self, record: LoadRecord, command: LoadCommand, error: DomainError, now: datetime) -> None:
        logger.warning(
            'load_command_failed',
            load=record.slug,
            device=record.device_slug,
            command=command,
            error_code=error.error_code,
            detail=error.detail,
        )
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


def _reading(state: GuardState, slug: str) -> LoadReading | None:
    seen = state.readings.get(slug)
    return None if seen is None else seen.reading


def _overloaded(state: GuardState, phase: LoadPhase) -> bool:
    watch = state.phases.watches.get(phase)
    return watch is not None and watch.overloaded


def _name(loads: GuardLoads, slug: str) -> str:
    record = loads.record(slug)
    return slug if record is None else record.name


def _without(shedding: ShedState, slugs: Collection[str]) -> ShedState:
    return replace(shedding, sheds={slug: shed for slug, shed in shedding.sheds.items() if slug not in slugs})


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
