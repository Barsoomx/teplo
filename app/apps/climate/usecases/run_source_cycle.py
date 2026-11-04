from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from functools import partial

import structlog
from django.conf import settings
from django.db import OperationalError
from django.utils import timezone

from apps.climate.domain.drive import StartPermits
from apps.climate.domain.errors import HeatSourceNotFoundError
from apps.climate.domain.events import OccupancyDryRun, SourceCommandFailed, SourceCommandIssued
from apps.climate.domain.fast_source import (
    SourceChange,
    SourceDrive,
    SourceStart,
    SourceTarget,
    plan_source_drive,
    room_held_by_hand,
    room_targets,
)
from apps.climate.domain.heat_sources import SourceSettings
from apps.climate.domain.modes import COMMAND_SENT, ZoneCycleOutcome
from apps.climate.domain.ports import ClimateSensorsStore, HeatSourceStore, SourceLoadGate, SourceWrites
from apps.climate.domain.room_climate import RoomClimateSettings
from apps.climate.domain.room_intent import RoomIntents
from apps.climate.repositories.climate_settings import ClimateSettingsRepository
from apps.climate.repositories.heat_source import HeatSourceRepository
from apps.climate.services.control_book import ControlBook, control_book
from apps.climate.services.room_intents import RoomIntentReader
from apps.climate.services.source_write_gate import source_write_gate
from apps.core.domain.errors import DomainError
from apps.core.domain.events import EventDispatcher, event_dispatcher
from apps.core.domain.ports import Transaction
from apps.core.domain.usecases.base import BaseUseCase
from apps.core.services.database_transaction import DatabaseTransaction
from apps.devices.domain.capabilities import ControllableClimateUnit, ControllableClimateUnitProvider
from apps.devices.domain.readings import ClimateUnitState
from apps.devices.services.driver_registry import driver_registry
from apps.devices.services.state_store import (
    ClimateUnitStateStore,
    TemperatureStateStore,
    climate_unit_state_store,
    temperature_state_store,
)
from apps.power.services.guard_load_gate import GuardLoadGate

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, kw_only=True)
class RunSourceCycleInputDTO:
    now: datetime
    monotonic: float
    clock_trusted: bool


@dataclass(frozen=True, kw_only=True)
class RunSourceCycleOutputDTO:
    outcomes: dict[str, ZoneCycleOutcome]


@dataclass(frozen=True, kw_only=True)
class SourceRun:
    source: SourceSettings
    target: SourceTarget | None
    drive: SourceDrive
    writes_live: bool


class RunSourceCycleUseCase(BaseUseCase[RunSourceCycleInputDTO, RunSourceCycleOutputDTO]):
    def __init__(
        self,
        sources: HeatSourceStore,
        intents: RoomIntentReader,
        climate_settings: ClimateSensorsStore,
        units: ClimateUnitStateStore,
        temperatures: TemperatureStateStore,
        provider: ControllableClimateUnitProvider,
        book: ControlBook,
        gate: SourceLoadGate,
        writes: SourceWrites,
        transaction: Transaction,
        dry_run: bool,
        dispatcher: EventDispatcher = event_dispatcher,
    ) -> None:
        super().__init__(dispatcher=dispatcher)
        self._sources = sources
        self._intents = intents
        self._climate_settings = climate_settings
        self._units = units
        self._temperatures = temperatures
        self._provider = provider
        self._book = book
        self._gate = gate
        self._writes = writes
        self._transaction = transaction
        self._dry_run = dry_run

    @classmethod
    def init(cls) -> 'RunSourceCycleUseCase':
        return cls(
            sources=HeatSourceRepository(),
            intents=RoomIntentReader.init(),
            climate_settings=ClimateSettingsRepository(),
            units=climate_unit_state_store,
            temperatures=temperature_state_store,
            provider=driver_registry,
            book=control_book,
            gate=GuardLoadGate.init(),
            writes=source_write_gate,
            transaction=DatabaseTransaction(),
            dry_run=settings.TEPLO_DRY_RUN,
        )

    def _execute(self, input_dto: RunSourceCycleInputDTO) -> RunSourceCycleOutputDTO:
        now = timezone.localtime(input_dto.now)
        with self._transaction():
            sources = self._sources.commanded_settings()
            intents = self._intents.read(now, clock_trusted=input_dto.clock_trusted)
            outdoor_slug = self._climate_settings.sensors().outdoor_slug
        if not sources:
            return RunSourceCycleOutputDTO(outcomes={})

        held = self._gate.held_sources()
        gated = self._gate.gated_sources()
        outdoor = self._temperature(outdoor_slug)
        runs: list[SourceRun] = []
        for room, room_sources in _by_room(sources).items():
            runs.extend(
                self._plan_room(
                    room,
                    room_sources,
                    intents,
                    held,
                    outdoor,
                    now,
                    gated=gated,
                    read_at=input_dto.monotonic,
                ),
            )
        permits = StartPermits(answers=self._gate.ask_source_starts(_starts(runs), now), gated=gated)
        return RunSourceCycleOutputDTO(
            outcomes={run.source.slug: self._run(run, permits, now, read_at=input_dto.monotonic) for run in runs},
        )

    def _plan_room(
        self,
        room: str,
        sources: Sequence[SourceSettings],
        intents: RoomIntents,
        held: frozenset[str],
        outdoor: Decimal | None,
        now: datetime,
        *,
        gated: frozenset[str],
        read_at: float,
    ) -> list[SourceRun]:
        intent = intents.of(room)
        climate = next((climate for climate in intents.rooms if climate.slug == room), None)
        if intent is None or climate is None:
            if self._book.first_notice_without_climate(room):
                logger.warning('source_room_without_climate', room=room, sources=[source.slug for source in sources])
            return []

        states = {source.slug: self._state(source.device_slug) for source in sources}
        air = self._temperature(climate.air_sensor_slug)
        targets = room_targets(
            sources,
            states,
            shed=held,
            air=air,
            target=intent.target,
            outdoor=outdoor,
            now=now,
        )
        self._watch_occupancy(
            climate,
            sources,
            states,
            targets,
            intents,
            held=held,
            air=air,
            outdoor=outdoor,
            now=now,
        )
        hands_off = room_held_by_hand(sources, now)
        return [
            self._plan(
                source,
                targets[source.slug],
                states[source.slug],
                held,
                now,
                hands_off=hands_off,
                gated=gated,
                read_at=read_at,
            )
            for source in sources
        ]

    def _watch_occupancy(
        self,
        climate: RoomClimateSettings,
        sources: Sequence[SourceSettings],
        states: Mapping[str, ClimateUnitState | None],
        targets: Mapping[str, SourceTarget | None],
        intents: RoomIntents,
        *,
        held: frozenset[str],
        air: Decimal | None,
        outdoor: Decimal | None,
        now: datetime,
    ) -> None:
        would_be = intents.would_be_of(climate.slug)
        occupancy = intents.occupancy_of(climate.slug)
        if would_be is None or occupancy is None:
            return

        would = room_targets(sources, states, shed=held, air=air, target=would_be.target, outdoor=outdoor, now=now)
        for source in sources:
            target = would[source.slug]
            if target is None or target == targets[source.slug]:
                self._book.forget_occupancy_journaled(source.slug)
                continue

            if self._book.occupancy_journaled(source.slug) == target:
                continue

            self._book.remember_occupancy_journaled(source.slug, target)
            self._event_store.add_event(
                OccupancyDryRun(
                    source=source.slug,
                    name=source.name,
                    room=climate.slug,
                    room_name=climate.name,
                    occupancy=occupancy,
                    power=target.power,
                    setpoint=target.setpoint,
                    occurred_at=now,
                ),
            )

    def _plan(
        self,
        source: SourceSettings,
        target: SourceTarget | None,
        state: ClimateUnitState | None,
        held: frozenset[str],
        now: datetime,
        *,
        hands_off: bool,
        gated: frozenset[str],
        read_at: float,
    ) -> SourceRun:
        writes_live = source.writes_live(dry_run=self._dry_run)
        drive = plan_source_drive(
            target,
            state,
            last_write_at=source.last_write_at(dry_run=self._dry_run),
            interval=source.write_interval,
            now=now,
            shed=source.slug in held,
            hands_off=hands_off,
            slot_free=self._slot_free(source, now, writes_live=writes_live, read_at=read_at),
        )
        if writes_live and drive.outcome is None and source.slug not in gated:
            logger.info('source_waits_for_load_management', source=source.slug)
            drive = SourceDrive(change=SourceChange(), outcome=ZoneCycleOutcome.WAITING, starts=False)
        if not writes_live and self._journaled(source, target, drive):
            drive = SourceDrive(change=SourceChange(), outcome=ZoneCycleOutcome.UNCHANGED, starts=False)
        return SourceRun(source=source, target=target, drive=drive, writes_live=writes_live)

    def _journaled(self, source: SourceSettings, target: SourceTarget | None, drive: SourceDrive) -> bool:
        if drive.outcome not in (None, ZoneCycleOutcome.THROTTLED):
            return False

        return target is not None and self._book.source_journaled(source.slug) == target

    def _slot_free(self, source: SourceSettings, now: datetime, *, writes_live: bool, read_at: float) -> bool:
        if not writes_live:
            return True

        return self._writes.allows(
            source.slug,
            now,
            stamped_at=source.last_command_at,
            read_at=read_at,
            interval=source.write_interval,
        )

    def _run(self, run: SourceRun, permits: StartPermits, now: datetime, *, read_at: float) -> ZoneCycleOutcome:
        try:
            return self._drive(run, permits, now, read_at=read_at)
        except (OperationalError, HeatSourceNotFoundError) as error:
            logger.warning(
                'source_cycle_failed',
                source=run.source.slug,
                error=str(error),
            )
            return ZoneCycleOutcome.FAILED

    def _drive(self, run: SourceRun, permits: StartPermits, now: datetime, *, read_at: float) -> ZoneCycleOutcome:
        if run.drive.outcome is not None:
            return run.drive.outcome

        if run.source.slug in self._gate.held_sources():
            return ZoneCycleOutcome.SHED

        if run.drive.starts:
            note = permits.note(run.source.slug)
            if note is not None:
                self._event_store.add_event(note)
            if not permits.granted(run.source.slug, writes_live=run.writes_live):
                return ZoneCycleOutcome.WAITING

        if run.target is None:
            return ZoneCycleOutcome.UNCHANGED

        if not run.writes_live:
            return self._journal_dry_run(run.source, run.target, run.drive.change, now)

        outcome = self._issue(run.source, run.target, run.drive.change, now, read_at=read_at)
        if run.drive.starts and outcome in COMMAND_SENT:
            self._gate.note_source_started(run.source.slug)
        return outcome

    def _journal_dry_run(
        self,
        source: SourceSettings,
        target: SourceTarget,
        change: SourceChange,
        now: datetime,
    ) -> ZoneCycleOutcome:
        self._sources.mark_dry_run(source.slug, now)
        self._book.remember_source_journaled(source.slug, target)
        self._event_store.add_event(
            SourceCommandIssued(
                source=source.slug,
                name=source.name,
                power=change.power,
                setpoint=change.setpoint,
                dry_run=True,
                occurred_at=now,
            ),
        )
        return ZoneCycleOutcome.DRY_RUN

    def _issue(
        self,
        source: SourceSettings,
        target: SourceTarget,
        change: SourceChange,
        now: datetime,
        *,
        read_at: float,
    ) -> ZoneCycleOutcome:
        reservation = self._writes.reserve(
            source.slug,
            now,
            stamped_at=source.last_command_at,
            read_at=read_at,
            interval=source.write_interval,
        )
        if reservation is None:
            logger.info('source_write_held', source=source.slug)
            return ZoneCycleOutcome.THROTTLED

        with self._writes.stamping(reservation):
            self._sources.mark_commanded(source.slug, now, power=target.power, setpoint=target.setpoint)
        try:
            unit = self._provider.controllable_climate_unit(source.device_slug)
            written = self._writes.write(reservation, partial(_apply, unit, change))
        except DomainError as error:
            logger.warning(
                'source_command_failed',
                source=source.slug,
                device=source.device_slug,
                error_code=error.error_code,
                detail=error.detail,
            )
            self._event_store.add_event(
                SourceCommandFailed(
                    source=source.slug,
                    name=source.name,
                    power=change.power,
                    setpoint=change.setpoint,
                    error=error.detail,
                    error_code=error.error_code,
                    occurred_at=now,
                ),
            )
            return ZoneCycleOutcome.FAILED

        if not written:
            logger.info('source_write_held', source=source.slug)
            return ZoneCycleOutcome.THROTTLED

        self._event_store.add_event(
            SourceCommandIssued(
                source=source.slug,
                name=source.name,
                power=change.power,
                setpoint=change.setpoint,
                dry_run=False,
                occurred_at=now,
            ),
        )
        return ZoneCycleOutcome.APPLIED

    def _state(self, slug: str) -> ClimateUnitState | None:
        snapshot = self._units.snapshot(slug)
        return snapshot.state if snapshot.available else None

    def _temperature(self, slug: str | None) -> Decimal | None:
        if slug is None:
            return None

        snapshot = self._temperatures.snapshot(slug)
        if not snapshot.available or snapshot.state is None:
            return None

        return snapshot.state.temperature


def _apply(unit: ControllableClimateUnit, change: SourceChange) -> None:
    if change.power is not None:
        unit.set_power(change.power)
    if change.setpoint is None:
        return

    try:
        unit.set_setpoint(change.setpoint)
    except DomainError:
        if change.power is True:
            unit.set_power(False)
        raise


def _by_room(sources: Sequence[SourceSettings]) -> dict[str, list[SourceSettings]]:
    rooms: dict[str, list[SourceSettings]] = {}
    for source in sources:
        rooms.setdefault(source.room_slug, []).append(source)
    return rooms


def _starts(runs: Sequence[SourceRun]) -> list[SourceStart]:
    return [SourceStart(source=run.source.slug, writes_live=run.writes_live) for run in runs if run.drive.starts]
