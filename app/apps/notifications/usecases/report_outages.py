from dataclasses import dataclass
from datetime import datetime

import structlog
from django.db import OperationalError

from apps.core.domain.events import EventDispatcher, event_dispatcher
from apps.core.domain.usecases.base import BaseUseCase
from apps.notifications.domain.events import TeploResumed
from apps.notifications.domain.outage import Outage, StartupNote, detect_outages
from apps.notifications.domain.ports import PowerCutLedger
from apps.notifications.services.alert_book import AlertBook, alert_book
from apps.power.domain.outage import PowerCut
from apps.power.services.power_cut_recorder import PowerCutRecorder

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, kw_only=True)
class ReportOutagesInputDTO:
    now: datetime
    monotonic: float
    clock_trusted: bool


@dataclass(frozen=True, kw_only=True)
class ReportOutagesOutputDTO:
    reported: tuple[Outage, ...]


class ReportOutagesUseCase(BaseUseCase[ReportOutagesInputDTO, ReportOutagesOutputDTO]):
    def __init__(
        self,
        book: AlertBook,
        power_cuts: PowerCutLedger,
        dispatcher: EventDispatcher = event_dispatcher,
    ) -> None:
        super().__init__(dispatcher=dispatcher)
        self._book = book
        self._power_cuts = power_cuts

    @classmethod
    def init(cls) -> 'ReportOutagesUseCase':
        return cls(book=alert_book, power_cuts=PowerCutRecorder.init())

    def _execute(self, input_dto: ReportOutagesInputDTO) -> ReportOutagesOutputDTO:
        note = self._book.startup()
        if note is None or not input_dto.clock_trusted:
            return ReportOutagesOutputDTO(reported=())

        outages = detect_outages(note, input_dto.now, input_dto.monotonic)
        stored = False
        if outages.power_cut is not None:
            try:
                stored = self._store(outages.power_cut, note, input_dto.now)
            except OperationalError as error:
                logger.warning(
                    'outage_record_failed',
                    began_at=outages.power_cut.began_at.isoformat(),
                    ended_at=outages.power_cut.ended_at.isoformat(),
                    error=str(error),
                )
                return ReportOutagesOutputDTO(reported=())

        if not outages.held_back:
            self._book.forget_startup()
        news = (outages.power_cut if stored else None, outages.restart)
        reported = tuple(outage for outage in news if outage is not None)
        for outage in reported:
            self._event_store.add_event(TeploResumed(outage=outage, occurred_at=input_dto.now))
        return ReportOutagesOutputDTO(reported=reported)

    def _store(self, power_cut: Outage, note: StartupNote, noticed_at: datetime) -> bool:
        recorded = self._power_cuts.record(
            PowerCut(
                boot_id=note.boot.start.boot_id,
                began_at=power_cut.began_at,
                ended_at=power_cut.ended_at,
                noticed_at=noticed_at,
            ),
        )
        return recorded is not None
