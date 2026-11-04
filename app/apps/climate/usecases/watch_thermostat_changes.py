from dataclasses import dataclass
from datetime import datetime

import structlog
from django.db import OperationalError

from apps.climate.domain.errors import ZoneNotFoundError
from apps.climate.domain.events import ZoneManualChange
from apps.climate.domain.manual_change import ManualChange, teplo_window, thermostat_change
from apps.climate.domain.ports import ClimateGatesStore, WriteGate, ZoneHoldStore, ZoneStore
from apps.climate.domain.zone import MANUAL_HOLD, ZoneSettings
from apps.climate.repositories.climate_settings import ClimateSettingsRepository
from apps.climate.repositories.zone import ZoneRepository
from apps.climate.services.write_gate import zone_write_gate
from apps.core.domain.events import EventDispatcher, event_dispatcher
from apps.core.domain.usecases.base import BaseUseCase
from apps.devices.domain.readings import ReadingPair, ThermostatState

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, kw_only=True)
class WatchThermostatChangesInputDTO:
    pairs: tuple[ReadingPair[ThermostatState], ...]
    now: datetime


@dataclass(frozen=True, kw_only=True)
class WatchThermostatChangesOutputDTO:
    held: tuple[str, ...]
    journaled: tuple[str, ...]


class WatchThermostatChangesUseCase(BaseUseCase[WatchThermostatChangesInputDTO, WatchThermostatChangesOutputDTO]):
    def __init__(
        self,
        zones: ZoneStore,
        holds: ZoneHoldStore,
        gates: ClimateGatesStore,
        writes: WriteGate,
        dispatcher: EventDispatcher = event_dispatcher,
    ) -> None:
        super().__init__(dispatcher=dispatcher)
        self._zones = zones
        self._holds = holds
        self._gates = gates
        self._writes = writes

    @classmethod
    def init(cls) -> 'WatchThermostatChangesUseCase':
        return cls(
            zones=ZoneRepository(),
            holds=ZoneRepository(),
            gates=ClimateSettingsRepository(),
            writes=zone_write_gate,
        )

    def _execute(self, input_dto: WatchThermostatChangesInputDTO) -> WatchThermostatChangesOutputDTO:
        changes = [(pair, change) for pair in input_dto.pairs if (change := thermostat_change(pair)) is not None]
        if not changes:
            return WatchThermostatChangesOutputDTO(held=(), journaled=())

        zones = {zone.thermostat_slug: zone for zone in self._zones.all_settings()}
        live = self._gates.gates().manual_hold_live
        held: list[str] = []
        journaled: list[str] = []
        for pair, change in changes:
            zone = zones.get(pair.slug)
            if zone is None or self._writes.written_between(zone.slug, *teplo_window(pair)):
                continue

            if self._note(zone, change, live=live, now=input_dto.now):
                journaled.append(zone.slug)
                if live:
                    held.append(zone.slug)
        return WatchThermostatChangesOutputDTO(held=tuple(held), journaled=tuple(journaled))

    def _note(self, zone: ZoneSettings, change: ManualChange, *, live: bool, now: datetime) -> bool:
        until = now + MANUAL_HOLD
        if live:
            try:
                self._holds.hold_by_hand(zone.slug, until)
            except (OperationalError, ZoneNotFoundError) as error:
                logger.warning(
                    'zone_manual_hold_failed',
                    zone=zone.slug,
                    error=str(error),
                )
                return False

        logger.info(
            'zone_manual_change',
            zone=zone.slug,
            power=None if change.power is None else list(change.power),
            mode=None if change.mode is None else list(change.mode),
            setpoint=None if change.setpoint is None else [str(setpoint) for setpoint in change.setpoint],
            until=until.isoformat(),
            live=live,
        )
        self._event_store.add_event(
            ZoneManualChange(
                zone=zone.slug,
                name=zone.name,
                power=change.power,
                mode=change.mode,
                setpoint=change.setpoint,
                until=until,
                dry_run=not live,
                occurred_at=now,
            ),
        )
        return True
