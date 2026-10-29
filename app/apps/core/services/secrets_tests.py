from pathlib import Path

import pytest

from apps.core.domain.errors import SecretMissingError
from apps.core.services.secrets import DeviceSecret, SecretsFile

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
"""


@pytest.fixture
def f_secrets(tmp_path: Path) -> SecretsFile:
    path = tmp_path / 'secrets.toml'
    path.write_text(SECRETS_TOML, encoding='utf-8')
    return SecretsFile.init(path)


@pytest.fixture
def f_empty_secrets(tmp_path: Path) -> SecretsFile:
    return SecretsFile.init(tmp_path / 'absent.toml')


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


def test_missing_file_has_no_secrets(f_empty_secrets: SecretsFile) -> None:
    with pytest.raises(SecretMissingError):
        f_empty_secrets.device('thermostat-kitchen')
