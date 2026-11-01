import threading
import time
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from apps.core.services.secrets import DeviceSecret
from apps.devices.domain.errors import DeviceProtocolError, DeviceUnavailableError
from apps.devices.domain.readings import PlugState
from apps.devices.drivers.tuya_plug import TuyaPayload, TuyaPlugDriver, plug_client

NOW = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
BOILER_DPS = {
    '1': True,
    '9': 0,
    '17': 217,
    '18': 6020,
    '19': 13054,
    '20': 2168,
    '21': 1,
    '22': 548,
    '23': 27892,
    '24': 15094,
    '25': 2750,
    '26': 0,
    '38': 'memory',
    '39': 'relay',
    '40': False,
    '43': '',
    '51': False,
}


class TuyaStatusClientStub:
    def __init__(self, status_payload: TuyaPayload | None, error: OSError | None = None) -> None:
        self._status_payload = status_payload
        self._error = error

    def status(self) -> TuyaPayload | None:
        if self._error is not None:
            raise self._error

        return self._status_payload


class SerialClientStub:
    def __init__(self) -> None:
        self._active = 0
        self._max_active = 0
        self._guard = threading.Lock()

    @property
    def max_active(self) -> int:
        return self._max_active

    def status(self) -> TuyaPayload | None:
        with self._guard:
            self._active += 1
            self._max_active = max(self._max_active, self._active)
        time.sleep(0.05)
        with self._guard:
            self._active -= 1
        return {'dps': BOILER_DPS}


def _now() -> datetime:
    return NOW


@pytest.fixture
def f_driver() -> TuyaPlugDriver:
    return TuyaPlugDriver(client=TuyaStatusClientStub(status_payload={'dps': BOILER_DPS}), clock=_now)


@pytest.fixture
def f_malformed_driver(request: pytest.FixtureRequest) -> TuyaPlugDriver:
    return TuyaPlugDriver(client=TuyaStatusClientStub(status_payload=request.param), clock=_now)


@pytest.fixture
def f_error_payload_driver() -> TuyaPlugDriver:
    client = TuyaStatusClientStub(status_payload={'Error': 'Network Error: Unable to Connect', 'Err': '901'})
    return TuyaPlugDriver(client=client, clock=_now)


@pytest.fixture
def f_silent_driver() -> TuyaPlugDriver:
    return TuyaPlugDriver(client=TuyaStatusClientStub(status_payload=None), clock=_now)


@pytest.fixture
def f_offline_driver() -> TuyaPlugDriver:
    client = TuyaStatusClientStub(status_payload=None, error=ConnectionRefusedError('connection refused'))
    return TuyaPlugDriver(client=client, clock=_now)


@pytest.fixture
def f_serial_client() -> SerialClientStub:
    return SerialClientStub()


@pytest.fixture
def f_serial_driver(f_serial_client: SerialClientStub) -> TuyaPlugDriver:
    return TuyaPlugDriver(client=f_serial_client, clock=_now)


@pytest.fixture
def f_secret() -> DeviceSecret:
    return DeviceSecret(
        address='plug.invalid',
        device_id='plug-device-id',
        local_key='plug-local-key',
        protocol='3.5',
    )


def test_read_state_maps_data_points(f_driver: TuyaPlugDriver) -> None:
    assert f_driver.read_state() == PlugState(
        on=True,
        power_watts=Decimal('1305.4'),
        current_amps=Decimal('6.02'),
        voltage_volts=Decimal('216.8'),
        read_at=NOW,
    )


@pytest.mark.parametrize(
    'f_malformed_driver',
    [
        {'dps': {key: value for key, value in BOILER_DPS.items() if key != '19'}},
        {'dps': {**BOILER_DPS, '1': 'on'}},
        {'dps': {**BOILER_DPS, '18': True}},
        {'dps': {**BOILER_DPS, '20': None}},
        {'t': 1793523600},
    ],
    ids=['power-missing', 'switch-not-a-bool', 'current-a-bool', 'voltage-null', 'no-dps'],
    indirect=True,
)
def test_read_state_rejects_unexpected_payload(f_malformed_driver: TuyaPlugDriver) -> None:
    with pytest.raises(DeviceProtocolError):
        f_malformed_driver.read_state()


def test_read_state_reports_tuya_error(f_error_payload_driver: TuyaPlugDriver) -> None:
    with pytest.raises(DeviceUnavailableError, match='Unable to Connect'):
        f_error_payload_driver.read_state()


def test_read_state_reports_empty_response(f_silent_driver: TuyaPlugDriver) -> None:
    with pytest.raises(DeviceUnavailableError, match='empty response'):
        f_silent_driver.read_state()


def test_read_state_reports_socket_errors(f_offline_driver: TuyaPlugDriver) -> None:
    with pytest.raises(DeviceUnavailableError, match='connection refused'):
        f_offline_driver.read_state()


def test_reads_are_serialized_across_threads(
    f_serial_driver: TuyaPlugDriver,
    f_serial_client: SerialClientStub,
) -> None:
    threads = [threading.Thread(target=f_serial_driver.read_state) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert f_serial_client.max_active == 1


def test_plug_client_never_switches_to_device22(f_secret: DeviceSecret) -> None:
    client = plug_client(f_secret)

    assert (client.disabledetect, client.version) == (True, 3.5)
