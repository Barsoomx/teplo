from pathlib import Path

import pytest

from apps.core.domain.errors import SecretMissingError
from apps.core.services.secrets import DeviceSecret, MqttSecret, SecretsFile, TelegramSecret

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

[mqtt]
host = ' 192.0.2.1 '
port = 1884
username = ' teplo '
password = ' mqtt password '
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
COMMANDS_TELEGRAM_TOML = """
[telegram]
token = '123456:telegram-token'
chat_id = '42'
"""
DEFAULT_PORT_MQTT_TOML = """
[mqtt]
host = 'router.invalid'
username = 'teplo'
password = 'mqtt-password'
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


@pytest.fixture
def f_commands_secrets(tmp_path: Path, request: pytest.FixtureRequest) -> SecretsFile:
    path = tmp_path / 'secrets.toml'
    path.write_text(f'{COMMANDS_TELEGRAM_TOML}{request.param}', encoding='utf-8')
    return SecretsFile.init(path)


@pytest.fixture
def f_mqtt_secrets(tmp_path: Path, request: pytest.FixtureRequest) -> SecretsFile:
    path = tmp_path / 'secrets.toml'
    path.write_text(request.param, encoding='utf-8')
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


@pytest.mark.parametrize(
    ('f_commands_secrets', 'commands'),
    [
        ('commands = true', True),
        ('commands = false', False),
        ('', False),
        ("commands = 'true'", False),
    ],
    ids=['switched_on', 'switched_off', 'missing', 'text'],
    indirect=['f_commands_secrets'],
)
def test_telegram_commands_are_taken_only_when_switched_on(f_commands_secrets: SecretsFile, commands: bool) -> None:
    assert f_commands_secrets.telegram == TelegramSecret(
        token='123456:telegram-token',
        chat_id='42',
        proxy=None,
        commands=commands,
    )


def test_mqtt_secret_is_read_with_its_port(f_secrets: SecretsFile) -> None:
    assert f_secrets.mqtt == MqttSecret(host='192.0.2.1', port=1884, username='teplo', password=' mqtt password ')


@pytest.mark.parametrize('f_mqtt_secrets', [DEFAULT_PORT_MQTT_TOML], indirect=True)
def test_mqtt_port_defaults_to_1883(f_mqtt_secrets: SecretsFile) -> None:
    assert f_mqtt_secrets.mqtt == MqttSecret(
        host='router.invalid',
        port=1883,
        username='teplo',
        password='mqtt-password',
    )


@pytest.mark.parametrize(
    'f_mqtt_secrets',
    [
        DEFAULT_PORT_MQTT_TOML.replace("host = 'router.invalid'", "host = ' '"),
        DEFAULT_PORT_MQTT_TOML.replace("username = 'teplo'", ''),
        DEFAULT_PORT_MQTT_TOML.replace("password = 'mqtt-password'", "password = ''"),
        f'{DEFAULT_PORT_MQTT_TOML}port = 0',
        f'{DEFAULT_PORT_MQTT_TOML}port = 65536',
        f"{DEFAULT_PORT_MQTT_TOML}port = '18 83'",
        f'{DEFAULT_PORT_MQTT_TOML}port = 1883.0',
        f'{DEFAULT_PORT_MQTT_TOML}port = true',
    ],
    ids=[
        'blank-host',
        'no-username',
        'empty-password',
        'port-zero',
        'port-too-high',
        'port-text',
        'port-float',
        'port-bool',
    ],
    indirect=True,
)
def test_incomplete_mqtt_table_configures_nothing(f_mqtt_secrets: SecretsFile) -> None:
    assert f_mqtt_secrets.mqtt is None


@pytest.mark.parametrize('f_mqtt_secrets', [f"{DEFAULT_PORT_MQTT_TOML}port = '1884'"], indirect=True)
def test_mqtt_port_may_be_written_as_text(f_mqtt_secrets: SecretsFile) -> None:
    assert f_mqtt_secrets.mqtt == MqttSecret(
        host='router.invalid',
        port=1884,
        username='teplo',
        password='mqtt-password',
    )


def test_missing_mqtt_table_configures_nothing(f_empty_secrets: SecretsFile) -> None:
    assert f_empty_secrets.mqtt is None


def test_addresses_keys_tokens_and_passwords_stay_out_of_repr(f_secrets: SecretsFile) -> None:
    assert (repr(f_secrets.device('thermostat-kitchen')), repr(f_secrets.telegram), repr(f_secrets.mqtt)) == (
        "DeviceSecret(protocol='3.3')",
        "TelegramSecret(chat_id='-1001234', commands=False)",
        'MqttSecret(port=1884)',
    )
