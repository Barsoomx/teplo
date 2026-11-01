from dataclasses import dataclass

import structlog

from apps.core.domain.usecases.base import BaseUseCase
from apps.notifications.domain.errors import AlertDeliveryError, AlertRejectedError
from apps.notifications.domain.ports import AlertSender
from apps.notifications.services.outbox import AlertOutbox, OutgoingMessage, alert_outbox
from apps.notifications.services.telegram import alert_sender

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, kw_only=True)
class DeliverAlertsInputDTO:
    pass


@dataclass(frozen=True, kw_only=True)
class DeliverAlertsOutputDTO:
    sent: int
    dropped: int
    pending: int


class DeliverAlertsUseCase(BaseUseCase[DeliverAlertsInputDTO, DeliverAlertsOutputDTO]):
    def __init__(self, outbox: AlertOutbox, sender: AlertSender) -> None:
        super().__init__()
        self._outbox = outbox
        self._sender = sender

    @classmethod
    def init(cls) -> 'DeliverAlertsUseCase':
        return cls(outbox=alert_outbox, sender=alert_sender)

    def _execute(self, input_dto: DeliverAlertsInputDTO) -> DeliverAlertsOutputDTO:  # noqa: ARG002
        expired = self._outbox.drop_expired()
        for message in expired:
            logger.warning('alert_message_expired', text=message.text)
        sent, rejected = self._send_pending()
        return DeliverAlertsOutputDTO(
            sent=sent,
            dropped=len(expired) + rejected,
            pending=len(self._outbox.pending()),
        )

    def _send_pending(self) -> tuple[int, int]:
        sent = rejected = 0
        for message in self._outbox.pending():
            try:
                self._sender.send(message.text)
            except AlertRejectedError as error:
                self._drop(message, error)
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

    def _drop(self, message: OutgoingMessage, error: AlertRejectedError) -> None:
        self._outbox.remove(message)
        logger.warning(
            'alert_message_rejected',
            error_code=error.error_code,
            detail=error.detail,
            text=message.text,
        )
