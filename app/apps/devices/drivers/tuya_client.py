from typing import Protocol

import tinytuya

from apps.core.services.secrets import DeviceSecret
from apps.devices.domain.errors import DeviceConfigError

TuyaPayload = dict[str, object]
TUYA_PROTOCOLS = ('3.1', '3.2', '3.3', '3.4', '3.5')


class TuyaStatusClient(Protocol):
    def status(self) -> TuyaPayload | None: ...


class TuyaClient(TuyaStatusClient, Protocol):
    def set_value(self, index: int, value: bool | int | str) -> TuyaPayload | None: ...


def tuya_client(secret: DeviceSecret) -> TuyaClient:
    _check(secret)
    try:
        client = tinytuya.Device(
            secret.device_id,
            secret.address,
            secret.local_key,
            version=float(secret.protocol),
            connection_timeout=5,
            connection_retry_limit=2,
            connection_retry_delay=1,
        )
    except (ValueError, RuntimeError) as error:
        raise DeviceConfigError(f'tinytuya refused the device settings: {error}') from error

    client.disabledetect = True
    return client


def _check(secret: DeviceSecret) -> None:
    if not secret.address.strip():
        raise DeviceConfigError('the secrets file gives the device no address')

    if secret.protocol.strip() not in TUYA_PROTOCOLS:
        raise DeviceConfigError(f'protocol {secret.protocol!r} is not one of {", ".join(TUYA_PROTOCOLS)}')
