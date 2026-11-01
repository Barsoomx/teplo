from typing import Protocol

import tinytuya

from apps.core.services.secrets import DeviceSecret

TuyaPayload = dict[str, object]


class TuyaStatusClient(Protocol):
    def status(self) -> TuyaPayload | None: ...


class TuyaClient(TuyaStatusClient, Protocol):
    def set_value(self, index: int, value: bool | int | str) -> TuyaPayload | None: ...


def tuya_client(secret: DeviceSecret) -> TuyaClient:
    client = tinytuya.Device(
        secret.device_id,
        secret.address,
        secret.local_key,
        version=float(secret.protocol),
        connection_timeout=5,
        connection_retry_limit=2,
        connection_retry_delay=1,
    )
    client.disabledetect = True
    return client
