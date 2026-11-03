import email.message
import http.client
import json
import urllib.error
from datetime import UTC, datetime
from typing import cast

import pytest
from structlog.testing import capture_logs

from apps.core.domain.errors import DomainError
from apps.core.services.secrets import TelegramSecret
from apps.notifications.domain.bot import BotUpdate
from apps.notifications.domain.errors import AlertDeliveryError, AlertRejectedError, CommandPollError
from apps.notifications.services.telegram import (
    TelegramClient,
    UnconfiguredSender,
    UrlOpener,
    alert_sender_for,
    build_opener,
    proxies_for,
)
from apps.notifications.stubs import UrlOpenerStub

TOKEN = '123456:telegram-token'
URL = f'https://api.telegram.org/bot{TOKEN}/sendMessage'
UPDATES_URL = f'https://api.telegram.org/bot{TOKEN}/getUpdates'
TEXT = 'Кухня: термостат снова на связи'
PROXY_USER = 'teplo-proxy-user'
PROXY_PASSWORD = 'proxy-password'
SENT = b'{"ok": true}'
SENT_AT = datetime(2026, 11, 4, 13, 40, tzinfo=UTC)
UPDATES = json.dumps(
    {
        'ok': True,
        'result': [
            {
                'update_id': 41,
                'message': {
                    'message_id': 7,
                    'date': 1793799600,
                    'chat': {'id': -1001234, 'type': 'group'},
                    'text': 'холодно в гостиной',
                },
            },
            {
                'update_id': 42,
                'edited_message': {
                    'message_id': 7,
                    'date': 1793799600,
                    'edit_date': 1793799660,
                    'chat': {'id': -1001234, 'type': 'group'},
                    'text': 'жарко в гостиной',
                },
            },
            {'update_id': 43, 'message': {'message_id': 8, 'date': 1793799720, 'chat': {'id': 42}, 'sticker': {}}},
            {'update_id': 44, 'my_chat_member': {'chat': {'id': 42}}},
            {'message': {'message_id': 9, 'date': 1793799720, 'chat': {'id': 42}, 'text': 'без номера'}},
        ],
    },
).encode()


def _http_error(code: int, reason: str) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(URL, code, reason, email.message.Message(), None)


@pytest.fixture
def f_opener() -> UrlOpenerStub:
    return UrlOpenerStub(error=None, bodies=(SENT,))


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
        opener=UrlOpenerStub(error=_http_error(401, 'Unauthorized'), bodies=()),
        timeout=10.0,
    )


@pytest.fixture
def f_unconfigured_sender() -> UnconfiguredSender:
    return UnconfiguredSender()


@pytest.fixture
def f_poll_opener(request: pytest.FixtureRequest) -> UrlOpenerStub:
    return UrlOpenerStub(error=None, bodies=(request.param,))


@pytest.fixture
def f_poll_client(f_poll_opener: UrlOpenerStub) -> TelegramClient:
    return TelegramClient(token=TOKEN, chat_id='-1001234', opener=f_poll_opener, timeout=10.0)


@pytest.fixture
def f_failing_poll_client(request: pytest.FixtureRequest) -> TelegramClient:
    return TelegramClient(
        token=TOKEN,
        chat_id='-1001234',
        opener=UrlOpenerStub(error=request.param, bodies=()),
        timeout=10.0,
    )


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
    client = TelegramClient(token=TOKEN, chat_id='42', opener=UrlOpenerStub(error=error, bodies=()), timeout=10.0)

    with pytest.raises(expected):
        client.send(TEXT)


def test_refusal_names_the_status_and_keeps_the_token_out(f_unauthorized_client: TelegramClient) -> None:
    with pytest.raises(AlertRejectedError) as raised:
        f_unauthorized_client.send(TEXT)

    assert (raised.value.detail, raised.value.error_code) == (
        'telegram answered: HTTP Error 401: Unauthorized',
        'alert_rejected',
    )


@pytest.mark.parametrize(
    'opener',
    [
        build_opener(f'http:/{PROXY_USER}:{PROXY_PASSWORD}@proxy.invalid:3128'),
        UrlOpenerStub(error=http.client.InvalidURL(f"nonnumeric port: '{PROXY_PASSWORD}'"), bodies=()),
    ],
    ids=['url_without_authority', 'credentials_without_host'],
)
def test_malformed_proxy_fails_without_its_credentials(opener: UrlOpener) -> None:
    client = TelegramClient(token=TOKEN, chat_id='42', opener=opener, timeout=10.0)

    with pytest.raises(AlertDeliveryError) as raised:
        client.send(TEXT)

    detail = raised.value.detail
    assert (raised.value.error_code, detail, PROXY_USER in detail, PROXY_PASSWORD in detail) == (
        'alert_delivery_failed',
        'telegram unreachable: malformed proxy or API address',
        False,
        False,
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


@pytest.mark.parametrize(
    ('f_poll_opener', 'offset', 'body'),
    [
        (UPDATES, 41, {'timeout': 10, 'allowed_updates': ['message'], 'offset': 41}),
        (UPDATES, None, {'timeout': 10, 'allowed_updates': ['message']}),
    ],
    ids=['after_an_update', 'from_where_telegram_kept'],
    indirect=['f_poll_opener'],
)
def test_updates_long_poll_the_bot_with_ten_seconds_of_slack_over_the_wait(
    f_poll_client: TelegramClient,
    f_poll_opener: UrlOpenerStub,
    offset: int | None,
    body: dict[str, object],
) -> None:
    f_poll_client.updates(offset, wait_seconds=10)

    request, timeout = f_poll_opener.requests[0]
    assert (
        request.full_url,
        request.get_method(),
        request.headers,
        json.loads(cast(bytes, request.data)),
        timeout,
    ) == (UPDATES_URL, 'POST', {'Content-type': 'application/json'}, body, 20.0)


@pytest.mark.parametrize('f_poll_opener', [UPDATES], indirect=True)
def test_updates_read_messages_and_edits_and_keep_other_updates_for_the_offset(f_poll_client: TelegramClient) -> None:
    updates = f_poll_client.updates(None, wait_seconds=10)

    assert updates == [
        BotUpdate(update_id=41, chat_id='-1001234', text='холодно в гостиной', sent_at=SENT_AT, edited=False),
        BotUpdate(update_id=42, chat_id='-1001234', text='жарко в гостиной', sent_at=SENT_AT, edited=True),
        BotUpdate(
            update_id=43,
            chat_id='42',
            text=None,
            sent_at=datetime(2026, 11, 4, 13, 42, tzinfo=UTC),
            edited=False,
        ),
        BotUpdate(update_id=44, chat_id='', text=None, sent_at=None, edited=False),
    ]


@pytest.mark.parametrize(
    ('f_poll_opener', 'detail'),
    [
        (b'<html>Bad Gateway</html>', 'telegram answered with malformed JSON'),
        (b'\xff\xfe\xfd', 'telegram answered with malformed JSON'),
        (b'{"ok": false, "error_code": 409, "description": "Conflict"}', 'telegram answered without updates'),
        (b'{"ok": true, "result": {}}', 'telegram answered without updates'),
    ],
    ids=['not_json', 'not_text', 'refused', 'no_list'],
    indirect=['f_poll_opener'],
)
def test_an_unreadable_answer_fails_the_poll(f_poll_client: TelegramClient, detail: str) -> None:
    with pytest.raises(CommandPollError) as raised:
        f_poll_client.updates(None, wait_seconds=10)

    assert (raised.value.error_code, raised.value.detail) == ('owner_commands_unreachable', detail)


@pytest.mark.parametrize(
    ('f_failing_poll_client', 'detail'),
    [
        (_http_error(409, 'Conflict'), 'telegram answered: HTTP Error 409: Conflict'),
        (
            urllib.error.URLError('Name or service not known'),
            'telegram unreachable: <urlopen error Name or service not known>',
        ),
        (TimeoutError('timed out'), 'telegram unreachable: timed out'),
        (
            http.client.InvalidURL(f"nonnumeric port: '{PROXY_PASSWORD}'"),
            'telegram unreachable: malformed proxy or API address',
        ),
    ],
    ids=['second_poller', 'no_network', 'timeout', 'malformed_proxy'],
    indirect=['f_failing_poll_client'],
)
def test_a_failed_poll_names_the_cause_without_the_token_or_the_proxy_password(
    f_failing_poll_client: TelegramClient,
    detail: str,
) -> None:
    with pytest.raises(CommandPollError) as raised:
        f_failing_poll_client.updates(41, wait_seconds=10)

    assert (raised.value.detail, TOKEN in raised.value.detail, PROXY_PASSWORD in raised.value.detail) == (
        detail,
        False,
        False,
    )
