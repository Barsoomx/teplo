import email.message
import http.client
import json
import urllib.error
from typing import cast

import pytest
from structlog.testing import capture_logs

from apps.core.domain.errors import DomainError
from apps.core.services.secrets import TelegramSecret
from apps.notifications.domain.errors import AlertDeliveryError, AlertRejectedError
from apps.notifications.services.telegram import (
    TelegramClient,
    UnconfiguredSender,
    alert_sender_for,
    build_opener,
    proxies_for,
)
from apps.notifications.stubs import UrlOpenerStub

TOKEN = '123456:telegram-token'
URL = f'https://api.telegram.org/bot{TOKEN}/sendMessage'
TEXT = 'Кухня: термостат снова на связи'


def _http_error(code: int, reason: str) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(URL, code, reason, email.message.Message(), None)


@pytest.fixture
def f_opener() -> UrlOpenerStub:
    return UrlOpenerStub(error=None)


@pytest.fixture
def f_client(f_opener: UrlOpenerStub) -> TelegramClient:
    return TelegramClient(token=TOKEN, chat_id='42', opener=f_opener, timeout=10.0)


@pytest.fixture
def f_odd_token_client(f_opener: UrlOpenerStub) -> TelegramClient:
    return TelegramClient(token='123456:odd token/', chat_id='42', opener=f_opener, timeout=10.0)


@pytest.fixture
def f_unauthorized_client() -> TelegramClient:
    return TelegramClient(
        token=TOKEN,
        chat_id='42',
        opener=UrlOpenerStub(error=_http_error(401, 'Unauthorized')),
        timeout=10.0,
    )


@pytest.fixture
def f_malformed_proxy_client() -> TelegramClient:
    return TelegramClient(token=TOKEN, chat_id='42', opener=build_opener('http:/proxy.invalid'), timeout=10.0)


@pytest.fixture
def f_unconfigured_sender() -> UnconfiguredSender:
    return UnconfiguredSender()


def test_send_posts_the_text_to_the_chat(f_client: TelegramClient, f_opener: UrlOpenerStub) -> None:
    f_client.send(TEXT)

    request, timeout = f_opener.requests[0]
    assert (
        request.full_url,
        request.get_method(),
        request.headers,
        json.loads(cast(bytes, request.data)),
        timeout,
    ) == (
        URL,
        'POST',
        {'Content-type': 'application/json'},
        {'chat_id': '42', 'text': TEXT},
        10.0,
    )


def test_token_is_quoted_into_the_path(f_odd_token_client: TelegramClient, f_opener: UrlOpenerStub) -> None:
    f_odd_token_client.send(TEXT)

    assert f_opener.requests[0][0].full_url == 'https://api.telegram.org/bot123456:odd%20token%2F/sendMessage'


@pytest.mark.parametrize(
    ('error', 'expected'),
    [
        (_http_error(400, 'Bad Request'), AlertRejectedError),
        (_http_error(403, 'Forbidden'), AlertRejectedError),
        (_http_error(429, 'Too Many Requests'), AlertDeliveryError),
        (_http_error(502, 'Bad Gateway'), AlertDeliveryError),
        (urllib.error.URLError('Name or service not known'), AlertDeliveryError),
        (TimeoutError('timed out'), AlertDeliveryError),
        (http.client.IncompleteRead(b''), AlertDeliveryError),
        (UnicodeError("encoding with 'idna' codec failed (UnicodeError: label empty or too long)"), AlertDeliveryError),
    ],
)
def test_failed_send_says_whether_a_retry_can_help(error: Exception, expected: type[DomainError]) -> None:
    client = TelegramClient(token=TOKEN, chat_id='42', opener=UrlOpenerStub(error=error), timeout=10.0)

    with pytest.raises(expected):
        client.send(TEXT)


def test_refusal_names_the_status_and_keeps_the_token_out(f_unauthorized_client: TelegramClient) -> None:
    with pytest.raises(AlertRejectedError) as raised:
        f_unauthorized_client.send(TEXT)

    assert (raised.value.detail, raised.value.error_code) == (
        'telegram answered: HTTP Error 401: Unauthorized',
        'alert_rejected',
    )


def test_malformed_proxy_is_a_delivery_failure(f_malformed_proxy_client: TelegramClient) -> None:
    with pytest.raises(AlertDeliveryError) as raised:
        f_malformed_proxy_client.send(TEXT)

    assert (raised.value.error_code, raised.value.detail) == (
        'alert_delivery_failed',
        "telegram unreachable: proxy URL with no authority: 'http:/proxy.invalid'",
    )


@pytest.mark.parametrize(
    ('proxy', 'expected'),
    [
        ('http://proxy.invalid:3128', {'http': 'http://proxy.invalid:3128', 'https': 'http://proxy.invalid:3128'}),
        (None, {}),
    ],
)
def test_proxy_carries_both_schemes_and_its_absence_ignores_the_environment(
    proxy: str | None,
    expected: dict[str, str],
) -> None:
    assert proxies_for(proxy) == expected


def test_unconfigured_sender_skips_sending_and_says_so_once(f_unconfigured_sender: UnconfiguredSender) -> None:
    with capture_logs() as logs:
        f_unconfigured_sender.send(TEXT)
        f_unconfigured_sender.send(TEXT)

    assert logs == [{'event': 'telegram_not_configured', 'log_level': 'warning'}]


@pytest.mark.parametrize(
    ('secret', 'expected'),
    [
        (None, UnconfiguredSender),
        (TelegramSecret(token=TOKEN, chat_id='42', proxy=None), TelegramClient),
    ],
)
def test_sender_follows_the_telegram_secret(secret: TelegramSecret | None, expected: type) -> None:
    assert type(alert_sender_for(secret)) is expected
