from dataclasses import replace

import pytest
import tinytuya

from apps.core.services.secrets import DeviceSecret
from apps.devices.domain.errors import DeviceConfigError
from apps.devices.drivers.tuya_client import tuya_client

LOCAL_KEY = '0123456789abcdef'


@pytest.fixture
def f_secret() -> DeviceSecret:
    return DeviceSecret(address='192.0.2.10', device_id='device-id', local_key=LOCAL_KEY, protocol='3.3')


@pytest.fixture
def f_plug_secret() -> DeviceSecret:
    return DeviceSecret(
        address='plug.invalid',
        device_id='plug-device-id',
        local_key='plug-local-key',
        protocol='3.5',
    )


@pytest.fixture
def f_data_unvalid_answer() -> bytes:
    return tinytuya.AESCipher(LOCAL_KEY.encode()).encrypt(b'json obj data unvalid', use_base64=False)


def test_client_keeps_full_status_query_after_data_unvalid_answer(
    f_secret: DeviceSecret,
    f_data_unvalid_answer: bytes,
) -> None:
    client = tuya_client(f_secret)
    assert isinstance(client, tinytuya.Device)

    result = client._decode_payload(f_data_unvalid_answer)

    assert (result['Err'], client.dev_type, client.dps_to_request) == ('900', 'default', {})


def test_plug_client_never_switches_to_device22(f_plug_secret: DeviceSecret) -> None:
    client = tuya_client(f_plug_secret)
    assert isinstance(client, tinytuya.Device)

    assert (client.disabledetect, client.version) == (True, 3.5)


@pytest.mark.parametrize('protocol', ['3,5', 'v3.5', '', '3.6'])
def test_unknown_protocol_is_a_config_error(f_plug_secret: DeviceSecret, protocol: str) -> None:
    with pytest.raises(DeviceConfigError) as raised:
        tuya_client(replace(f_plug_secret, protocol=protocol))

    assert (raised.value.error_code, raised.value.detail) == (
        'device_config_invalid',
        f'protocol {protocol!r} is not one of 3.1, 3.2, 3.3, 3.4, 3.5',
    )


@pytest.mark.parametrize('address', ['', '  '])
def test_missing_address_is_a_config_error_without_a_network_scan(f_secret: DeviceSecret, address: str) -> None:
    with pytest.raises(DeviceConfigError, match='no address'):
        tuya_client(replace(f_secret, address=address))


def test_settings_tinytuya_refuses_are_a_config_error(f_secret: DeviceSecret) -> None:
    with pytest.raises(DeviceConfigError, match='tinytuya refused the device settings'):
        tuya_client(replace(f_secret, local_key='ключ-не-latin-1'))
