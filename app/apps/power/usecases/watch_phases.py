import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal

import structlog

from apps.core.domain.events import EventDispatcher, event_dispatcher
from apps.core.domain.usecases.base import BaseUseCase
from apps.devices.domain.kinds import DeviceKind
from apps.devices.repositories.device import DeviceRepository
from apps.devices.services.phase_meter_store import PhaseMeterStore, phase_meter_store
from apps.devices.services.state_store import (
    PlugStateStore,
    ThermostatStateStore,
    plug_state_store,
    thermostat_state_store,
)
from apps.power.domain.ev_detection import EvWatch, detect_ev
from apps.power.domain.events import (
    EvChargingEvent,
    EvChargingStarted,
    EvChargingStopped,
    LoadPhaseLearned,
    PhaseCalmed,
    PhaseOverloaded,
)
from apps.power.domain.guard import GuardLoads, GuardState, PhaseState, SeenReading, follow_samples, phase_amps
from apps.power.domain.load_status import plug_reading, thermostat_reading
from apps.power.domain.loads import (
    MEASURED_PHASES,
    METER_PHASES,
    LearnedPhase,
    LoadKind,
    LoadPhase,
    LoadRecord,
    measured_currents,
)
from apps.power.domain.meter_watch import watch_meter
from apps.power.domain.moments import elapsed
from apps.power.domain.phase_learning import LoadChange, detect_change, follow_learning, learned_changed
from apps.power.domain.phase_watch import PhaseWatchStep, watch_phase
from apps.power.domain.ports import DeviceDirectory, LoadSettingsStore, LoadStore
from apps.power.repositories.load import LoadRepository
from apps.power.repositories.load_settings import LoadSettingsRepository
from apps.power.services.guard_book import GuardBook, guard_book

logger = structlog.get_logger(__name__)

LOADS_REFRESH = timedelta(minutes=1)


@dataclass(frozen=True, kw_only=True)
class WatchPhasesInputDTO:
    now: datetime


@dataclass(frozen=True, kw_only=True)
class WatchPhasesOutputDTO:
    overloaded: tuple[LoadPhase, ...]
    calmed: tuple[LoadPhase, ...]
    learned: tuple[str, ...]


class WatchPhasesUseCase(BaseUseCase[WatchPhasesInputDTO, WatchPhasesOutputDTO]):
    def __init__(
        self,
        loads: LoadStore,
        load_settings: LoadSettingsStore,
        devices: DeviceDirectory,
        meter: PhaseMeterStore,
        thermostats: ThermostatStateStore,
        plugs: PlugStateStore,
        book: GuardBook,
        clock: Callable[[], float],
        dispatcher: EventDispatcher = event_dispatcher,
    ) -> None:
        super().__init__(dispatcher=dispatcher)
        self._loads = loads
        self._load_settings = load_settings
        self._devices = devices
        self._meter = meter
        self._thermostats = thermostats
        self._plugs = plugs
        self._book = book
        self._clock = clock

    @classmethod
    def init(cls) -> 'WatchPhasesUseCase':
        return cls(
            loads=LoadRepository(),
            load_settings=LoadSettingsRepository(),
            devices=DeviceRepository(),
            meter=phase_meter_store,
            thermostats=thermostat_state_store,
            plugs=plug_state_store,
            book=guard_book,
            clock=time.monotonic,
        )

    def _execute(self, input_dto: WatchPhasesInputDTO) -> WatchPhasesOutputDTO:
        at = self._clock()
        previous = self._book.state()
        loads = self._loads_at(previous, at)
        state = GuardState.started(loads, at) if previous is None else replace(previous, loads=loads)
        readings, changes = self._readings(state, at)
        currents, read_at = self._currents(loads.meter, at)
        phases, steps = self._watch(state, currents, read_at, at, input_dto.now)
        learning = follow_learning(state.learning, changes, phases.samples, at)
        learned = self._learned(loads, learning.learned, input_dto.now)
        ev = self._follow_ev(state.ev, phases, learning.state.changes, at, input_dto.now)
        self._book.remember(
            replace(state, loads=learned, phases=phases, readings=readings, learning=learning.state, ev=ev),
        )
        return WatchPhasesOutputDTO(
            overloaded=tuple(phase for phase, step in steps.items() if step.overloaded),
            calmed=tuple(phase for phase, step in steps.items() if step.calmed),
            learned=tuple(record.slug for record in learned.records if record not in loads.records),
        )

    def _loads_at(self, state: GuardState | None, at: float) -> GuardLoads:
        if state is not None and not elapsed(state.loads.read_at, at, LOADS_REFRESH):
            return state.loads

        return GuardLoads(
            records=tuple(self._loads.all_records()),
            control=self._load_settings.control(),
            live_zones=self._loads.live_zone_slugs(),
            meter=next(iter(self._devices.slugs_of_kind(DeviceKind.PHASE_METER)), None),
            read_at=at,
        )

    def _currents(self, meter: str | None, at: float) -> tuple[dict[LoadPhase, Decimal], dict[LoadPhase, float]]:
        if meter is None:
            return {}, {}

        snapshot = self._meter.snapshot(meter)
        if snapshot.currents is None:
            return {}, {}

        fresh = {phase: amps for phase, amps in snapshot.currents.amps.items() if phase not in snapshot.stale_phases}
        return (
            measured_currents(fresh),
            {METER_PHASES[phase]: at - snapshot.ages[phase].total_seconds() for phase in fresh},
        )

    def _readings(self, state: GuardState, at: float) -> tuple[dict[str, SeenReading], list[LoadChange]]:
        readings: dict[str, SeenReading] = {}
        changes: list[LoadChange] = []
        for record in state.loads.records:
            before = state.readings.get(record.slug)
            seen = self._seen(record, before, at)
            if seen is None:
                continue

            readings[record.slug] = seen
            if before is None or seen is before:
                continue

            change = detect_change(record.slug, before.reading, seen.reading)
            if change is not None:
                changes.append(change)
        return readings, changes

    def _seen(self, record: LoadRecord, before: SeenReading | None, at: float) -> SeenReading | None:
        if record.kind is LoadKind.PLUG:
            plug = self._plugs.snapshot(record.device_slug)
            if not plug.available or plug.state is None or plug.age is None:
                return None

            if before is not None and before.polled_at == plug.state.read_at:
                return before

            return SeenReading(
                polled_at=plug.state.read_at,
                reading=plug_reading(
                    on=plug.state.on,
                    current=plug.state.current_amps,
                    read_at=at - plug.age.total_seconds(),
                ),
            )

        thermostat = self._thermostats.snapshot(record.device_slug)
        if not thermostat.available or thermostat.state is None or thermostat.age is None:
            return None

        if before is not None and before.polled_at == thermostat.state.read_at:
            return before

        return SeenReading(
            polled_at=thermostat.state.read_at,
            reading=thermostat_reading(
                power=thermostat.state.power,
                heating=thermostat.state.heating,
                setpoint=thermostat.state.setpoint,
                floor_temperature=thermostat.state.floor_temperature,
                read_at=at - thermostat.age.total_seconds(),
            ),
        )

    def _watch(
        self,
        state: GuardState,
        currents: dict[LoadPhase, Decimal],
        read_at: dict[LoadPhase, float],
        at: float,
        now: datetime,
    ) -> tuple[PhaseState, dict[LoadPhase, PhaseWatchStep]]:
        thresholds = state.loads.control.thresholds
        amps = phase_amps(currents, read_at, state.shedding.sheds.values())
        steps = {
            phase: watch_phase(state.phases.watches[phase], amps.get(phase), at, thresholds)
            for phase in MEASURED_PHASES
        }
        for phase, step in steps.items():
            self._note(phase, step, currents.get(phase), now)
        phases = PhaseState(
            meter=watch_meter(state.phases.meter, fresh=_fresh(currents), at=at),
            currents=currents,
            read_at=read_at,
            watches={phase: step.watch for phase, step in steps.items()},
            samples=follow_samples(state.phases.samples, currents, at),
        )
        return phases, steps

    def _note(self, phase: LoadPhase, step: PhaseWatchStep, measured: Decimal | None, now: datetime) -> None:
        if measured is None:
            return

        if step.overloaded:
            logger.warning('phase_overloaded', phase=phase, amps=str(measured))
            self._event_store.add_event(PhaseOverloaded(phase=phase, amps=measured, occurred_at=now))
        if step.calmed:
            logger.info('phase_calmed', phase=phase, amps=str(measured))
            self._event_store.add_event(PhaseCalmed(phase=phase, amps=measured, occurred_at=now))

    def _learned(
        self,
        loads: GuardLoads,
        learned: Sequence[tuple[str, LearnedPhase]],
        now: datetime,
    ) -> GuardLoads:
        for slug, phase in learned:
            record = loads.record(slug)
            if record is None or not learned_changed(record.learned, phase):
                continue

            self._loads.save_learned(slug, phase)
            loads = loads.with_learned(slug, phase)
            logger.info('load_phase_learned', load=slug, phase=phase.phase, amps=str(phase.amps))
            self._event_store.add_event(
                LoadPhaseLearned(
                    load=slug,
                    name=record.name,
                    phase=phase.phase,
                    amps=phase.amps,
                    samples=phase.samples,
                    occurred_at=now,
                ),
            )
        return loads

    def _follow_ev(
        self,
        watches: tuple[EvWatch, ...],
        phases: PhaseState,
        changes: Sequence[LoadChange],
        at: float,
        now: datetime,
    ) -> tuple[EvWatch, ...]:
        if not _fresh(phases.currents):
            return watches

        detection = detect_ev(watches, phases.samples, changes, at)
        for watch in detection.started:
            logger.info('ev_charging_started', phase=watch.phase, amps=str(watch.step))
            self._event_store.add_event(_ev_event(EvChargingStarted, watch, now))
        for watch in detection.stopped:
            logger.info('ev_charging_stopped', phase=watch.phase, amps=str(watch.step))
            self._event_store.add_event(_ev_event(EvChargingStopped, watch, now))
        return detection.watches


def _fresh(currents: Mapping[LoadPhase, Decimal]) -> bool:
    return all(phase in currents for phase in MEASURED_PHASES)


def _ev_event(event_type: type[EvChargingEvent], watch: EvWatch, now: datetime) -> EvChargingEvent:
    return event_type(phase=watch.phase, amps=watch.step, before=watch.before, occurred_at=now)
