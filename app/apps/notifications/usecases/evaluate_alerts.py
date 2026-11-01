from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from apps.core.domain.events import EventDispatcher, event_dispatcher
from apps.core.domain.usecases.base import BaseUseCase
from apps.notifications.domain.alerts import AlertKey, AlertTransition
from apps.notifications.domain.events import AlertCleared, AlertEvent, AlertRaised, TeploResumed
from apps.notifications.domain.outage import Outage, detect_outage
from apps.notifications.domain.policy import AlertChange, Condition, device_condition, evaluate_alerts, zone_condition
from apps.notifications.domain.ports import DeviceSource, ZoneSource
from apps.notifications.services.alert_book import AlertBook, alert_book
from apps.notifications.services.thermostat_source import ThermostatSource
from apps.notifications.services.zone_source import ZoneHeatingSource
from apps.system.domain.clock import ClockTrustSource
from apps.system.services.clock_trust import clock_trust_source


@dataclass(frozen=True, kw_only=True)
class EvaluateAlertsInputDTO:
    now: datetime
    monotonic: float


@dataclass(frozen=True, kw_only=True)
class EvaluateAlertsOutputDTO:
    raised: tuple[AlertKey, ...]
    cleared: tuple[AlertKey, ...]
    outage: Outage | None


class EvaluateAlertsUseCase(BaseUseCase[EvaluateAlertsInputDTO, EvaluateAlertsOutputDTO]):
    def __init__(
        self,
        devices: Sequence[DeviceSource],
        zones: ZoneSource,
        clock: ClockTrustSource,
        book: AlertBook,
        dispatcher: EventDispatcher = event_dispatcher,
    ) -> None:
        super().__init__(dispatcher=dispatcher)
        self._devices = devices
        self._zones = zones
        self._clock = clock
        self._book = book

    @classmethod
    def init(cls) -> 'EvaluateAlertsUseCase':
        return cls(
            devices=(ThermostatSource.init(),),
            zones=ZoneHeatingSource.init(),
            clock=clock_trust_source(),
            book=alert_book,
        )

    def _execute(self, input_dto: EvaluateAlertsInputDTO) -> EvaluateAlertsOutputDTO:
        clock_trusted = self._clock.status().trusted
        evaluation = evaluate_alerts(
            self._conditions(input_dto.now, clock_trusted),
            self._book.tracks(),
            input_dto.now,
            input_dto.monotonic,
            clock_trusted=clock_trusted,
        )
        self._book.replace_tracks(evaluation.tracks)
        for change in evaluation.changes:
            self._event_store.add_event(_event(change, input_dto.now))
        return EvaluateAlertsOutputDTO(
            raised=_keys(evaluation.changes, AlertTransition.RAISED),
            cleared=_keys(evaluation.changes, AlertTransition.CLEARED),
            outage=self._report_outage(input_dto, clock_trusted),
        )

    def _conditions(self, now: datetime, clock_trusted: bool) -> list[Condition]:
        conditions = [device_condition(device) for source in self._devices for device in source.devices(now)]
        conditions.extend(zone_condition(zone) for zone in self._zones.zones(now, clock_trusted=clock_trusted))
        return conditions

    def _report_outage(self, input_dto: EvaluateAlertsInputDTO, clock_trusted: bool) -> Outage | None:
        note = self._book.startup()
        if note is None or not clock_trusted:
            return None

        self._book.forget_startup()
        outage = detect_outage(note, input_dto.now, input_dto.monotonic)
        if outage is not None:
            self._event_store.add_event(TeploResumed(outage=outage, occurred_at=input_dto.now))
        return outage


def _event(change: AlertChange, now: datetime) -> AlertEvent:
    event_type: type[AlertEvent] = AlertRaised if change.transition is AlertTransition.RAISED else AlertCleared
    return event_type(
        kind=change.condition.key.kind,
        subject=change.condition.key.subject,
        name=change.condition.name,
        device_kind=change.condition.device_kind,
        since=change.since,
        occurred_at=now,
    )


def _keys(changes: Sequence[AlertChange], transition: AlertTransition) -> tuple[AlertKey, ...]:
    return tuple(change.condition.key for change in changes if change.transition is transition)
