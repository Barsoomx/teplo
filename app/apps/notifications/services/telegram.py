import http.client
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Protocol
from urllib.parse import quote

import structlog
from django.conf import settings

from apps.core.domain.errors import DomainError
from apps.core.services.secrets import SecretsFile, TelegramSecret
from apps.notifications.domain.errors import AlertDeliveryError, AlertRejectedError
from apps.notifications.domain.ports import AlertSender

logger = structlog.get_logger(__name__)

SEND_TIMEOUT = 10.0
TOO_MANY_REQUESTS = 429


class HttpResponse(Protocol):
    def close(self) -> None: ...


class UrlOpener(Protocol):
    def open(self, fullurl: urllib.request.Request, /, *, timeout: float) -> HttpResponse: ...


class TelegramClient:
    def __init__(self, token: str, chat_id: str, opener: UrlOpener, timeout: float) -> None:
        self._token = token
        self._chat_id = chat_id
        self._opener = opener
        self._timeout = timeout

    @classmethod
    def init(cls, secret: TelegramSecret) -> 'TelegramClient':
        return cls(
            token=secret.token,
            chat_id=secret.chat_id,
            opener=build_opener(secret.proxy),
            timeout=SEND_TIMEOUT,
        )

    def send(self, text: str) -> None:
        request = urllib.request.Request(
            f'https://api.telegram.org/bot{quote(self._token, safe=":")}/sendMessage',
            data=json.dumps({'chat_id': self._chat_id, 'text': text}).encode(),
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        try:
            response = self._opener.open(request, timeout=self._timeout)
        except urllib.error.HTTPError as error:
            raise _refusal(error) from None
        except (OSError, http.client.HTTPException) as error:
            raise AlertDeliveryError(f'telegram unreachable: {error}') from None

        response.close()


class UnconfiguredSender:
    def __init__(self) -> None:
        self._warned = False

    def send(self, text: str) -> None:  # noqa: ARG002
        if self._warned:
            return

        self._warned = True
        logger.warning('telegram_not_configured')


def build_opener(proxy: str | None) -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.ProxyHandler(proxies_for(proxy)))


def proxies_for(proxy: str | None) -> dict[str, str]:
    if proxy is None:
        return {}

    return {'http': proxy, 'https': proxy}


def alert_sender_for(secret: TelegramSecret | None) -> AlertSender:
    if secret is None:
        return UnconfiguredSender()

    return TelegramClient.init(secret)


def _refusal(error: urllib.error.HTTPError) -> DomainError:
    detail = f'telegram answered: {error}'
    if 400 <= error.code < 500 and error.code != TOO_MANY_REQUESTS:
        return AlertRejectedError(detail)

    return AlertDeliveryError(detail)


alert_sender = alert_sender_for(SecretsFile.init(Path(settings.TEPLO_SECRETS_FILE)).telegram)
