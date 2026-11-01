from dataclasses import dataclass
from datetime import datetime

import structlog
from django.conf import settings
from django.utils import translation

from apps.core.domain.usecases.base import BaseUseCase
from apps.notifications.domain.errors import AlertDeliveryError, AlertRejectedError
from apps.notifications.domain.ports import AlertSender
from apps.notifications.services.outbox import AlertOutbox, OutgoingMessage, alert_outbox
from apps.notifications.services.telegram import alert_sender

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, kw_only=True)
class DeliverAlertsInputDTO:
    now: datetime


@dataclass(frozen=True, kw_only=True)
class DeliverAlertsOutputDTO:
    sent: int
    dropped: int
    pending: int


class DeliverAlertsUseCase(BaseUseCase[DeliverAlertsInputDTO, DeliverAlertsOutputDTO]):
    def __init__(self, outbox: AlertOutbox, sender: AlertSender, language: str) -> None:
        super().__init__()
        self._outbox = outbox
        self._sender = sender
        self._language = language

    @classmethod
    def init(cls) -> 'DeliverAlertsUseCase':
        return cls(outbox=alert_outbox, sender=alert_sender, language=settings.TEPLO_ALERT_LANGUAGE)

    def _execute(self, input_dto: DeliverAlertsInputDTO) -> DeliverAlertsOutputDTO:
        expired = self._outbox.drop_expired()
        for message in expired:
            logger.warning('alert_message_expired', text=self._text(message, input_dto.now))
        sent, rejected = self._send_pending(input_dto.now)
        return DeliverAlertsOutputDTO(
            sent=sent,
            dropped=len(expired) + rejected,
            pending=len(self._outbox.pending()),
        )

    def _send_pending(self, now: datetime) -> tuple[int, int]:
        sent = rejected = 0
        for message in self._outbox.pending():
            text = self._text(message, now)
            try:
                self._sender.send(text)
            except AlertRejectedError as error:
                self._drop(message, text, error)
                rejected += 1
            except AlertDeliveryError as error:
                logger.warning(
                    'alert_delivery_failed',
                    error_code=error.error_code,
                    detail=error.detail,
                    pending=len(self._outbox.pending()),
                )
                break
            else:
                self._outbox.remove(message)
                sent += 1
        return sent, rejected

    def _text(self, message: OutgoingMessage, at: datetime) -> str:
        with translation.override(self._language):
            return message.content.text(at)

    def _drop(self, message: OutgoingMessage, text: str, error: AlertRejectedError) -> None:
        self._outbox.remove(message)
        logger.warning(
            'alert_message_rejected',
            error_code=error.error_code,
            detail=error.detail,
            text=text,
        )
