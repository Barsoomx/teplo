import pytest

from apps.core.domain.errors import DomainError
from apps.devices.domain.errors import (
    DeviceNotFoundError,
    DeviceProtocolError,
    DeviceUnavailableError,
    DriverNotImplementedError,
)


@pytest.mark.parametrize(
    ('error', 'code', 'detail'),
    [
        (DeviceUnavailableError('timeout'), 'device_unavailable', 'timeout'),
        (DeviceProtocolError('bad payload'), 'device_protocol_error', 'bad payload'),
        (DriverNotImplementedError('unsupported'), 'driver_not_implemented', 'driver not implemented: unsupported'),
        (DeviceNotFoundError('thermostat-attic'), 'device_not_found', 'device not found: thermostat-attic'),
    ],
)
def test_device_errors_carry_codes(error: DomainError, code: str, detail: str) -> None:
    assert (error.error_code, error.detail) == (code, detail)
