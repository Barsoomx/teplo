from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from apps.core.domain.events import EventDispatcher, event_dispatcher
from apps.core.domain.usecases.base import BaseUseCase
from apps.notifications.domain.alerts import AlertKey, AlertTransition
from apps.notifications.domain.events import AlertCleared, AlertEvent, AlertRaised
from apps.notifications.domain.policy import (
    AlertChange,
    Condition,
    device_condition,
    evaluate_alerts,
    phase_condition,
    zone_condition,
)
from apps.notifications.domain.ports import DeviceSource, PhaseSource, ZoneSource
from apps.notifications.services.alert_book import AlertBook, alert_book
from apps.notifications.services.phase_meter_source import PhaseMeterSource
from apps.notifications.services.phase_overload_source import PhaseOverloadSource
from apps.notifications.services.plug_source import PlugSource
from apps.notifications.services.temperature_sensor_source import TemperatureSensorSource
from apps.notifications.services.thermostat_source import ThermostatSource
from apps.notifications.services.zone_source import ZoneHeatingSource


@dataclass(frozen=True, kw_only=True)
class EvaluateAlertsInputDTO:
    now: datetime
    monotonic: float
    clock_trusted: bool


@dataclass(frozen=True, kw_only=True)
class EvaluateAlertsOutputDTO:
    raised: tuple[AlertKey, ...]
    cleared: tuple[AlertKey, ...]


class EvaluateAlertsUseCase(BaseUseCase[EvaluateAlertsInputDTO, EvaluateAlertsOutputDTO]):
    def __init__(
        self,
        devices: Sequence[DeviceSource],
        zones: ZoneSource,
        phases: PhaseSource,
        book: AlertBook,
        dispatcher: EventDispatcher = event_dispatcher,
    ) -> None:
        super().__init__(dispatcher=dispatcher)
        self._devices = devices
        self._zones = zones
        self._phases = phases
        self._book = book

    @classmethod
    def init(cls) -> 'EvaluateAlertsUseCase':
        return cls(
            devices=(
                ThermostatSource.init(),
                PlugSource.init(),
                TemperatureSensorSource.init(),
                PhaseMeterSource.init(),
            ),
            zones=ZoneHeatingSource.init(),
            phases=PhaseOverloadSource.init(),
            book=alert_book,
        )

    def _execute(self, input_dto: EvaluateAlertsInputDTO) -> EvaluateAlertsOutputDTO:
        evaluation = evaluate_alerts(
            self._conditions(input_dto.now, input_dto.clock_trusted),
            self._book.tracks(),
            input_dto.now,
            input_dto.monotonic,
            clock_trusted=input_dto.clock_trusted,
        )
        self._book.replace_tracks(evaluation.tracks)
        for change in evaluation.changes:
            self._event_store.add_event(_event(change, input_dto.now))
        return EvaluateAlertsOutputDTO(
            raised=_keys(evaluation.changes, AlertTransition.RAISED),
            cleared=_keys(evaluation.changes, AlertTransition.CLEARED),
        )

    def _conditions(self, now: datetime, clock_trusted: bool) -> list[Condition]:
        conditions = [device_condition(device) for source in self._devices for device in source.devices()]
        conditions.extend(zone_condition(zone) for zone in self._zones.zones(now, clock_trusted=clock_trusted))
        conditions.extend(phase_condition(alarm) for alarm in self._phases.phases())
        return conditions


def _event(change: AlertChange, now: datetime) -> AlertEvent:
    event_type: type[AlertEvent] = AlertRaised if change.transition is AlertTransition.RAISED else AlertCleared
    return event_type(
        kind=change.condition.key.kind,
        subject=change.condition.key.subject,
        name=change.condition.name,
        device_kind=change.condition.device_kind,
        since=change.since,
        amps=change.condition.amps,
        occurred_at=now,
    )


def _keys(changes: Sequence[AlertChange], transition: AlertTransition) -> tuple[AlertKey, ...]:
    return tuple(change.condition.key for change in changes if change.transition is transition)
