from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.utils import translation

from apps.core.domain.usecases.base import BaseUseCase
from apps.core.services.secrets import SecretsFile
from apps.notifications.domain.errors import TelegramNotConfiguredError
from apps.notifications.domain.ports import AlertSender
from apps.notifications.services.messages import delivery_check_text
from apps.notifications.services.telegram import TelegramClient


@dataclass(frozen=True, kw_only=True)
class SendTestAlertInputDTO:
    pass


@dataclass(frozen=True, kw_only=True)
class SendTestAlertOutputDTO:
    text: str


class SendTestAlertUseCase(BaseUseCase[SendTestAlertInputDTO, SendTestAlertOutputDTO]):
    def __init__(self, sender: AlertSender | None, language: str) -> None:
        super().__init__()
        self._sender = sender
        self._language = language

    @classmethod
    def init(cls) -> 'SendTestAlertUseCase':
        secret = SecretsFile.init(Path(settings.TEPLO_SECRETS_FILE)).telegram
        return cls(
            sender=None if secret is None else TelegramClient.init(secret),
            language=settings.TEPLO_ALERT_LANGUAGE,
        )

    def _execute(self, input_dto: SendTestAlertInputDTO) -> SendTestAlertOutputDTO:  # noqa: ARG002
        if self._sender is None:
            raise TelegramNotConfiguredError

        with translation.override(self._language):
            text = delivery_check_text()
        self._sender.send(text)
        return SendTestAlertOutputDTO(text=text)
