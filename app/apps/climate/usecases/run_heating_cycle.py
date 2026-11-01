from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

import structlog
from django.conf import settings
from django.utils import timezone

from apps.climate.domain.change import ThermostatChange, plan_change
from apps.climate.domain.events import ZoneCommandFailed, ZoneCommandIssued
from apps.climate.domain.floor_policy import decide_floor_target
from apps.climate.domain.schedule import resolve_mode
from apps.climate.domain.write_guard import write_allowed
from apps.climate.domain.zone import ZoneSettings
from apps.climate.repositories.zone import ZoneRepository
from apps.core.domain.errors import DomainError
from apps.core.domain.events import EventDispatcher, event_dispatcher
from apps.core.domain.usecases.base import BaseUseCase
from apps.devices.domain.capabilities import ThermostatProvider
from apps.devices.services.driver_registry import driver_registry
from apps.devices.services.state_store import ThermostatStateStore, thermostat_state_store
from apps.system.domain.clock import ClockTrustSource
from apps.system.services.clock_trust import clock_trust_source

logger = structlog.get_logger(__name__)


class ZoneCycleOutcome(StrEnum):
    UNCHANGED = 'unchanged'
    APPLIED = 'applied'
    DRY_RUN = 'dry_run'
    THROTTLED = 'throttled'
    DEVICE_UNAVAILABLE = 'device_unavailable'
    FAILED = 'failed'


@dataclass(frozen=True, kw_only=True)
class RunHeatingCycleInputDTO:
    now: datetime


@dataclass(frozen=True, kw_only=True)
class RunHeatingCycleOutputDTO:
    outcomes: dict[str, ZoneCycleOutcome]


class RunHeatingCycleUseCase(BaseUseCase[RunHeatingCycleInputDTO, RunHeatingCycleOutputDTO]):
    def __init__(
        self,
        zones: ZoneRepository,
        store: ThermostatStateStore,
        provider: ThermostatProvider,
        clock: ClockTrustSource,
        dry_run: bool,
        dispatcher: EventDispatcher = event_dispatcher,
    ) -> None:
        super().__init__(dispatcher=dispatcher)
        self._zones = zones
        self._store = store
        self._provider = provider
        self._clock = clock
        self._dry_run = dry_run

    @classmethod
    def init(cls) -> 'RunHeatingCycleUseCase':
        return cls(
            zones=ZoneRepository(),
            store=thermostat_state_store,
            provider=driver_registry,
            clock=clock_trust_source(),
            dry_run=settings.TEPLO_DRY_RUN,
        )

    def _execute(self, input_dto: RunHeatingCycleInputDTO) -> RunHeatingCycleOutputDTO:
        now = timezone.localtime(input_dto.now)
        clock_trusted = self._clock.status().trusted
        return RunHeatingCycleOutputDTO(
            outcomes={zone.slug: self._run_zone(zone, now, clock_trusted) for zone in self._zones.all_settings()},
        )

    def _run_zone(self, zone: ZoneSettings, now: datetime, clock_trusted: bool) -> ZoneCycleOutcome:
        snapshot = self._store.snapshot(zone.thermostat_slug)
        if not snapshot.available or snapshot.state is None:
            return ZoneCycleOutcome.DEVICE_UNAVAILABLE

        mode = resolve_mode(zone.windows, now, zone.preheat_lead)
        target = decide_floor_target(zone, mode, now, clock_trusted=clock_trusted)
        change = plan_change(target, snapshot.state)
        if change.is_empty:
            return ZoneCycleOutcome.UNCHANGED

        live = zone.live_control and not self._dry_run
        if not write_allowed(zone.last_command_at if live else zone.last_dry_run_at, now):
            return ZoneCycleOutcome.THROTTLED

        if not live:
            return self._journal_dry_run(zone, change, now)

        return self._issue(zone, change, now)

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
