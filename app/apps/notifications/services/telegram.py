import http.client
import json
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from urllib.parse import quote

import structlog
from django.conf import settings

from apps.core.domain.errors import DomainError
from apps.core.services.secrets import SecretsFile, TelegramSecret
from apps.notifications.domain.bot import BotUpdate
from apps.notifications.domain.errors import AlertDeliveryError, AlertRejectedError, CommandPollError
from apps.notifications.domain.ports import AlertSender

logger = structlog.get_logger(__name__)

SEND_TIMEOUT = 10.0
POLL_SLACK_SECONDS = 10
TOO_MANY_REQUESTS = 429
MALFORMED_ADDRESS = 'telegram unreachable: malformed proxy or API address'
MALFORMED_ANSWER = 'telegram answered with malformed JSON'
NO_UPDATES = 'telegram answered without updates'


class HttpResponse(Protocol):
    def read(self) -> bytes: ...

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
        request = self._request('sendMessage', {'chat_id': self._chat_id, 'text': text})
        try:
            response = self._opener.open(request, timeout=self._timeout)
        except urllib.error.HTTPError as error:
            raise _refusal(error) from None
        except (ValueError, http.client.InvalidURL):
            raise AlertDeliveryError(MALFORMED_ADDRESS) from None
        except (OSError, http.client.HTTPException) as error:
            raise AlertDeliveryError(f'telegram unreachable: {error}') from None

        response.close()

    def updates(self, offset: int | None, wait_seconds: int) -> list[BotUpdate]:
        poll: dict[str, object] = {'timeout': wait_seconds, 'allowed_updates': ['message']}
        if offset is not None:
            poll['offset'] = offset
        request = self._request('getUpdates', poll)
        try:
            response = self._opener.open(request, timeout=wait_seconds + POLL_SLACK_SECONDS)
        except urllib.error.HTTPError as error:
            raise CommandPollError(f'telegram answered: {error}') from None
        except (ValueError, http.client.InvalidURL):
            raise CommandPollError(MALFORMED_ADDRESS) from None
        except (OSError, http.client.HTTPException) as error:
            raise CommandPollError(f'telegram unreachable: {error}') from None

        try:
            answer = response.read()
        except (OSError, http.client.HTTPException) as error:
            raise CommandPollError(f'telegram unreachable: {error}') from None
        finally:
            response.close()
        return bot_updates(answer)

    def _request(self, method: str, body: dict[str, object]) -> urllib.request.Request:
        return urllib.request.Request(
            f'https://api.telegram.org/bot{quote(self._token, safe=":")}/{method}',
            data=json.dumps(body).encode(),
            headers={'Content-Type': 'application/json'},
            method='POST',
        )


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


def bot_updates(answer: bytes) -> list[BotUpdate]:
    try:
        payload = json.loads(answer)
    except ValueError:
        raise CommandPollError(MALFORMED_ANSWER) from None

    result = payload.get('result') if isinstance(payload, dict) and payload.get('ok') is True else None
    if not isinstance(result, list):
        raise CommandPollError(NO_UPDATES)

    return [update for update in (_bot_update(entry) for entry in result) if update is not None]


def _bot_update(entry: object) -> BotUpdate | None:
    if not isinstance(entry, dict):
        return None

    update_id = entry.get('update_id')
    if not isinstance(update_id, int) or isinstance(update_id, bool):
        return None

    edited = 'message' not in entry and 'edited_message' in entry
    message = entry.get('edited_message' if edited else 'message')
    if not isinstance(message, dict):
        message = {}
    chat = message.get('chat')
    text = message.get('text')
    return BotUpdate(
        update_id=update_id,
        chat_id=_chat_id(chat.get('id') if isinstance(chat, dict) else None),
        text=text if isinstance(text, str) else None,
        sent_at=_sent_at(message.get('date')),
        edited=edited,
    )


def _chat_id(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, int | str):
        return ''

    return str(value)


def _sent_at(value: object) -> datetime | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None

    try:
        return datetime.fromtimestamp(value, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _refusal(error: urllib.error.HTTPError) -> DomainError:
    detail = f'telegram answered: {error}'
    if 400 <= error.code < 500 and error.code != TOO_MANY_REQUESTS:
        return AlertRejectedError(detail)

    return AlertDeliveryError(detail)


alert_sender = alert_sender_for(SecretsFile.init(Path(settings.TEPLO_SECRETS_FILE)).telegram)
