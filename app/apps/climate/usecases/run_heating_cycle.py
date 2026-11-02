from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

import structlog
from django.conf import settings
from django.utils import timezone

from apps.climate.domain.change import ThermostatChange
from apps.climate.domain.control import ClimateControl
from apps.climate.domain.drive import StartAnswer, ZoneDrive, ZoneStart, plan_drive
from apps.climate.domain.events import SecondFloorGuardDryRun, ZoneCommandFailed, ZoneCommandIssued
from apps.climate.domain.learning import (
    Morning,
    ZoneLearningRecord,
    estimate_morning,
    learn_early_stop_offset,
    learn_morning,
)
from apps.climate.domain.memory import ZoneMemory
from apps.climate.domain.modes import ZoneCycleOutcome
from apps.climate.domain.observation import StopWatch
from apps.climate.domain.ports import LoadGate
from apps.climate.domain.second_floor_guard import decide_second_floor_guard
from apps.climate.domain.zone import ZoneSettings
from apps.climate.domain.zone_cycle import (
    MINUTE,
    CycleConditions,
    ZoneConditions,
    ZoneDecision,
    ZoneFollowUp,
    decide_zone,
    follow_zone,
)
from apps.climate.repositories.climate_settings import ClimateSettingsRepository
from apps.climate.repositories.zone import ZoneRepository
from apps.climate.repositories.zone_learning import ZoneLearningRepository
from apps.climate.services.control_book import ControlBook, control_book
from apps.core.domain.errors import DomainError
from apps.core.domain.events import EventDispatcher, event_dispatcher
from apps.core.domain.usecases.base import BaseUseCase
from apps.devices.domain.capabilities import ThermostatProvider
from apps.devices.services.driver_registry import driver_registry
from apps.devices.services.state_store import (
    TemperatureStateStore,
    ThermostatStateStore,
    temperature_state_store,
    thermostat_state_store,
)
from apps.power.services.guard_load_gate import GuardLoadGate

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, kw_only=True)
class RunHeatingCycleInputDTO:
    now: datetime
    clock_trusted: bool


@dataclass(frozen=True, kw_only=True)
class RunHeatingCycleOutputDTO:
    outcomes: dict[str, ZoneCycleOutcome]


@dataclass(frozen=True, kw_only=True)
class ZoneRun:
    zone: ZoneSettings
    memory: ZoneMemory
    conditions: ZoneConditions
    decision: ZoneDecision
    drive: ZoneDrive


class RunHeatingCycleUseCase(BaseUseCase[RunHeatingCycleInputDTO, RunHeatingCycleOutputDTO]):
    def __init__(
        self,
        zones: ZoneRepository,
        climate_settings: ClimateSettingsRepository,
        learning: ZoneLearningRepository,
        store: ThermostatStateStore,
        temperatures: TemperatureStateStore,
        provider: ThermostatProvider,
        book: ControlBook,
        gate: LoadGate,
        dry_run: bool,
        dispatcher: EventDispatcher = event_dispatcher,
    ) -> None:
        super().__init__(dispatcher=dispatcher)
        self._zones = zones
        self._climate_settings = climate_settings
        self._learning = learning
        self._store = store
        self._temperatures = temperatures
        self._provider = provider
        self._book = book
        self._gate = gate
        self._dry_run = dry_run

    @classmethod
    def init(cls) -> 'RunHeatingCycleUseCase':
        return cls(
            zones=ZoneRepository(),
            climate_settings=ClimateSettingsRepository(),
            learning=ZoneLearningRepository(),
            store=thermostat_state_store,
            temperatures=temperature_state_store,
            provider=driver_registry,
            book=control_book,
            gate=GuardLoadGate.init(),
            dry_run=settings.TEPLO_DRY_RUN,
        )

    def _execute(self, input_dto: RunHeatingCycleInputDTO) -> RunHeatingCycleOutputDTO:
        now = timezone.localtime(input_dto.now)
        control = self._climate_settings.control()
        zones = self._zones.all_settings()
        guarded_zone = self._run_guard(zones, control, now)
        learning = self._learning.all_records()
        cycle = CycleConditions(
            now=now,
            clock_trusted=input_dto.clock_trusted,
            control=control,
            outdoor=self._temperature(control.sensors.outdoor_slug),
        )
        held = self._gate.held_zones()
        runs = [
            self._plan(zone, self._conditions(zone, learning, guarded_zone), cycle, held=zone.slug in held)
            for zone in zones
        ]
        answers = self._gate.ask_starts(
            [ZoneStart(zone=run.zone.slug, writes_live=run.conditions.writes_live) for run in runs if run.drive.starts],
            now,
        )
        return RunHeatingCycleOutputDTO(
            outcomes={run.zone.slug: self._run_zone(run, answers.get(run.zone.slug), cycle) for run in runs},
        )

    def _conditions(
        self,
        zone: ZoneSettings,
        learning: dict[str, ZoneLearningRecord],
        guarded_zone: str | None,
    ) -> ZoneConditions:
        return ZoneConditions(
            air=self._temperature(zone.air_sensor_slug),
            learning=learning.get(zone.slug, ZoneLearningRecord.unlearned()),
            guarded=zone.slug == guarded_zone,
            writes_live=zone.live_control and not self._dry_run,
        )

    def _run_guard(self, zones: list[ZoneSettings], control: ClimateControl, now: datetime) -> str | None:
        guard = control.second_floor_guard
        air = self._temperature(control.sensors.second_floor_slug)
        previous = self._book.guard_active()
        active = decide_second_floor_guard(previous, air, guard)
        self._book.remember_guard(active)
        zone = next((zone for zone in zones if zone.slug == guard.zone_slug), None)
        if zone is None:
            return None

        if active != previous:
            self._note_guard(zone, active, air, live=guard.live, now=now)
        return zone.slug if active and guard.live else None

    def _note_guard(self, zone: ZoneSettings, active: bool, air: Decimal | None, *, live: bool, now: datetime) -> None:
        logger.info(
            'second_floor_guard_changed',
            zone=zone.slug,
            active=active,
            air=None if air is None else str(air),
            live=live,
        )
        if live:
            return

        self._event_store.add_event(
            SecondFloorGuardDryRun(
                zone=zone.slug,
                active=active,
                air=air,
                setpoint=zone.setpoints.comfort,
                occurred_at=now,
            ),
        )

    def _plan(self, zone: ZoneSettings, conditions: ZoneConditions, cycle: CycleConditions, *, held: bool) -> ZoneRun:
        memory = self._book.zone(zone.slug)
        decision = decide_zone(zone, memory, conditions, cycle)
        snapshot = self._store.snapshot(zone.thermostat_slug)
        drive = plan_drive(
            decision.target,
            snapshot.state if snapshot.available else None,
            last_write_at=zone.last_command_at if conditions.writes_live else zone.last_dry_run_at,
            now=cycle.now,
            held=held,
        )
        return ZoneRun(zone=zone, memory=memory, conditions=conditions, decision=decision, drive=drive)

    def _run_zone(self, run: ZoneRun, answer: StartAnswer | None, cycle: CycleConditions) -> ZoneCycleOutcome:
        outcome = self._drive(run, answer, cycle.now)
        follow_up = follow_zone(run.zone, run.memory, run.decision, outcome, run.conditions, cycle)
        self._journal_air_dry_run(run.zone, follow_up, cycle.now)
        self._learn(run.zone, follow_up, run.conditions.learning, cycle.control)
        self._book.remember_zone(run.zone.slug, follow_up.memory)
        return outcome

    def _drive(self, run: ZoneRun, answer: StartAnswer | None, now: datetime) -> ZoneCycleOutcome:
        if run.drive.outcome is not None:
            return run.drive.outcome

        if answer is not None and answer.note is not None:
            self._event_store.add_event(answer.note)
        if answer is not None and not answer.granted:
            return ZoneCycleOutcome.WAITING

        if not run.conditions.writes_live:
            return self._journal_dry_run(run.zone, run.drive.change, now)

        return self._issue(run.zone, run.drive.change, now)

    def _journal_dry_run(self, zone: ZoneSettings, change: ThermostatChange, now: datetime) -> ZoneCycleOutcome:
        self._zones.mark_dry_run(zone.slug, now)
        self._event_store.add_event(ZoneCommandIssued(zone=zone.slug, change=change, dry_run=True, occurred_at=now))
        return ZoneCycleOutcome.DRY_RUN

    def _issue(self, zone: ZoneSettings, change: ThermostatChange, now: datetime) -> ZoneCycleOutcome:
        self._zones.mark_commanded(zone.slug, now)
        try:
            self._apply(zone.thermostat_slug, change)
        except DomainError as error:
            logger.warning(
                'zone_command_failed',
                zone=zone.slug,
                device=zone.thermostat_slug,
                error_code=error.error_code,
                detail=error.detail,
            )
            self._event_store.add_event(
                ZoneCommandFailed(
                    zone=zone.slug,
                    change=change,
                    error=error.detail,
                    error_code=error.error_code,
                    occurred_at=now,
                ),
            )
            return ZoneCycleOutcome.FAILED

        self._event_store.add_event(ZoneCommandIssued(zone=zone.slug, change=change, dry_run=False, occurred_at=now))
        return ZoneCycleOutcome.APPLIED

    def _apply(self, slug: str, change: ThermostatChange) -> None:
        thermostat = self._provider.thermostat(slug)
        if change.power is not None:
            thermostat.set_power(change.power)
        if change.mode is not None:
            thermostat.set_mode(change.mode)
        if change.setpoint is not None:
            thermostat.set_setpoint(change.setpoint)

    def _journal_air_dry_run(self, zone: ZoneSettings, follow_up: ZoneFollowUp, now: datetime) -> None:
        if follow_up.optimal_start is not None:
            self._event_store.add_event(follow_up.optimal_start)
        if follow_up.air_dry_run is not None:
            self._zones.mark_air_dry_run(zone.slug, now)
            self._event_store.add_event(follow_up.air_dry_run)

    def _learn(
        self,
        zone: ZoneSettings,
        follow_up: ZoneFollowUp,
        learning: ZoneLearningRecord,
        control: ClimateControl,
    ) -> None:
        if follow_up.morning is not None:
            self._learn_morning(zone, follow_up.morning, learning)
        if follow_up.stop is not None:
            offset = learning.early_stop_offset_or(control.regulation.initial_early_stop_offset)
            self._learn_stop(zone, follow_up.stop, offset)

    def _learn_morning(self, zone: ZoneSettings, morning: Morning, learning: ZoneLearningRecord) -> None:
        estimate = estimate_morning(morning)
        if estimate is None:
            logger.info('zone_morning_taught_nothing', zone=zone.slug, samples=len(morning.samples))
            return

        heat_up = learn_morning(learning.heat_up, estimate)
        self._learning.save_heat_up(zone.slug, heat_up)
        logger.info(
            'zone_morning_learned',
            zone=zone.slug,
            dead_time_minutes=estimate.dead_time // MINUTE,
            rate=str(estimate.rate),
            outdoor_bucket=estimate.outdoor_bucket,
            mornings=heat_up.mornings,
        )

    def _learn_stop(self, zone: ZoneSettings, watch: StopWatch, offset: Decimal) -> None:
        learned = learn_early_stop_offset(offset, peak=watch.peak, target=watch.target)
        self._learning.save_early_stop_offset(zone.slug, learned)
        logger.info(
            'zone_early_stop_offset_learned',
            zone=zone.slug,
            target=str(watch.target),
            peak=str(watch.peak),
            offset=str(learned),
        )

    def _temperature(self, slug: str | None) -> Decimal | None:
        if slug is None:
            return None

        snapshot = self._temperatures.snapshot(slug)
        if not snapshot.available or snapshot.state is None:
            return None

        return snapshot.state.temperature
