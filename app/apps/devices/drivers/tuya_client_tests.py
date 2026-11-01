import pytest
import tinytuya

from apps.core.services.secrets import DeviceSecret
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
