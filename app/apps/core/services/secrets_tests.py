from pathlib import Path

import pytest

from apps.core.domain.errors import SecretMissingError
from apps.core.services.secrets import DeviceSecret, SecretsFile, TelegramSecret

SECRETS_TOML = """
[devices.thermostat-kitchen]
address = '192.0.2.10'
device_id = 'kitchen-device-id'
local_key = 'kitchen-local-key'

[devices.thermostat-hallway]
address = '192.0.2.11'
device_id = 'hallway-device-id'
local_key = 'hallway-local-key'
protocol = '3.4'

[panel]
password = 'panel-secret'

[telegram]
token = ' 123456:telegram-token '
chat_id = -1001234
proxy = 'http://proxy.invalid:3128'
"""
DIRECT_TELEGRAM_TOML = """
[telegram]
token = '123456:telegram-token'
chat_id = '42'
proxy = ''
"""
TOKENLESS_TELEGRAM_TOML = """
[telegram]
chat_id = '42'
"""


@pytest.fixture
def f_secrets(tmp_path: Path) -> SecretsFile:
    path = tmp_path / 'secrets.toml'
    path.write_text(SECRETS_TOML, encoding='utf-8')
    return SecretsFile.init(path)


@pytest.fixture
def f_empty_secrets(tmp_path: Path) -> SecretsFile:
    return SecretsFile.init(tmp_path / 'absent.toml')


@pytest.fixture
def f_direct_telegram_secrets(tmp_path: Path) -> SecretsFile:
    path = tmp_path / 'secrets.toml'
    path.write_text(DIRECT_TELEGRAM_TOML, encoding='utf-8')
    return SecretsFile.init(path)


@pytest.fixture
def f_tokenless_telegram_secrets(tmp_path: Path) -> SecretsFile:
    path = tmp_path / 'secrets.toml'
    path.write_text(TOKENLESS_TELEGRAM_TOML, encoding='utf-8')
    return SecretsFile.init(path)


def test_device_secret_defaults_protocol(f_secrets: SecretsFile) -> None:
    assert f_secrets.device('thermostat-kitchen') == DeviceSecret(
        address='192.0.2.10',
        device_id='kitchen-device-id',
        local_key='kitchen-local-key',
        protocol='3.3',
    )


def test_device_secret_keeps_explicit_protocol(f_secrets: SecretsFile) -> None:
    assert f_secrets.device('thermostat-hallway').protocol == '3.4'


def test_panel_password_is_read(f_secrets: SecretsFile) -> None:
    assert f_secrets.panel_password == 'panel-secret'


def test_unknown_device_raises(f_secrets: SecretsFile) -> None:
    with pytest.raises(SecretMissingError):
        f_secrets.device('thermostat-attic')


def test_missing_file_has_no_secrets(f_empty_secrets: SecretsFile) -> None:
    with pytest.raises(SecretMissingError):
        f_empty_secrets.device('thermostat-kitchen')


def test_telegram_secret_is_read_with_proxy(f_secrets: SecretsFile) -> None:
    assert f_secrets.telegram == TelegramSecret(
        token='123456:telegram-token',
        chat_id='-1001234',
        proxy='http://proxy.invalid:3128',
    )


def test_telegram_secret_without_proxy_goes_direct(f_direct_telegram_secrets: SecretsFile) -> None:
    assert f_direct_telegram_secrets.telegram == TelegramSecret(token='123456:telegram-token', chat_id='42', proxy=None)


def test_telegram_table_without_token_configures_nothing(f_tokenless_telegram_secrets: SecretsFile) -> None:
    assert f_tokenless_telegram_secrets.telegram is None


def test_missing_telegram_table_configures_nothing(f_empty_secrets: SecretsFile) -> None:
    assert f_empty_secrets.telegram is None
