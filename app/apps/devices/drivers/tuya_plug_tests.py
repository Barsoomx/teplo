import threading
import time
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from apps.devices.domain.errors import DeviceProtocolError, DeviceUnavailableError
from apps.devices.domain.readings import PlugState
from apps.devices.drivers.tuya_client import TuyaPayload
from apps.devices.drivers.tuya_plug import TuyaPlugDriver, TuyaSwitchablePlugDriver

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
TUYA_ERROR: TuyaPayload = {'Error': 'Network Error: Unable to Connect', 'Err': '901', 'Payload': None}


class TuyaStatusClientStub:
    def __init__(self, status_payload: TuyaPayload | None, error: OSError | None = None) -> None:
        self._status_payload = status_payload
        self._error = error

    def status(self) -> TuyaPayload | None:
        if self._error is not None:
            raise self._error

        return self._status_payload


class TuyaClientStub:
    def __init__(
        self,
        status_payload: TuyaPayload | None,
        reply: TuyaPayload | None = None,
        error: OSError | None = None,
    ) -> None:
        self._status_payload = status_payload
        self._reply = reply
        self._error = error
        self._writes: list[tuple[int, bool | int | str]] = []

    @property
    def writes(self) -> list[tuple[int, bool | int | str]]:
        return self._writes

    def status(self) -> TuyaPayload | None:
        if self._error is not None:
            raise self._error

        return self._status_payload

    def set_value(self, index: int, value: bool | int | str) -> TuyaPayload | None:
        if self._error is not None:
            raise self._error

        self._writes.append((index, value))
        return self._reply


class SerialClientStub:
    def __init__(self) -> None:
        self._active = 0
        self._max_active = 0
        self._guard = threading.Lock()

    @property
    def max_active(self) -> int:
        return self._max_active

    def status(self) -> TuyaPayload | None:
        self._occupy()
        return {'dps': BOILER_DPS}

    def set_value(self, index: int, value: bool | int | str) -> TuyaPayload | None:  # noqa: ARG002
        self._occupy()
        return None

    def _occupy(self) -> None:
        with self._guard:
            self._active += 1
            self._max_active = max(self._max_active, self._active)
        time.sleep(0.05)
        with self._guard:
            self._active -= 1


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
def f_client() -> TuyaClientStub:
    return TuyaClientStub(status_payload={'dps': BOILER_DPS}, reply={'dps': {'1': False}, 't': 1793523600})


@pytest.fixture
def f_switchable_driver(f_client: TuyaClientStub) -> TuyaSwitchablePlugDriver:
    return TuyaSwitchablePlugDriver(client=f_client, clock=_now)


@pytest.fixture
def f_refused_switch_driver() -> TuyaSwitchablePlugDriver:
    return TuyaSwitchablePlugDriver(client=TuyaClientStub(status_payload=None, reply=TUYA_ERROR), clock=_now)


@pytest.fixture
def f_offline_switchable_driver() -> TuyaSwitchablePlugDriver:
    client = TuyaClientStub(status_payload=None, error=ConnectionRefusedError('connection refused'))
    return TuyaSwitchablePlugDriver(client=client, clock=_now)


@pytest.fixture
def f_serial_switchable_driver(f_serial_client: SerialClientStub) -> TuyaSwitchablePlugDriver:
    return TuyaSwitchablePlugDriver(client=f_serial_client, clock=_now)


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


def test_read_only_driver_has_no_switching_operation(f_driver: TuyaPlugDriver) -> None:
    assert not hasattr(f_driver, 'set_on')


def test_switchable_driver_reads_the_data_points_as_the_read_only_one(
    f_switchable_driver: TuyaSwitchablePlugDriver,
) -> None:
    assert f_switchable_driver.read_state() == PlugState(
        on=True,
        power_watts=Decimal('1305.4'),
        current_amps=Decimal('6.02'),
        voltage_volts=Decimal('216.8'),
        read_at=NOW,
    )


def test_set_on_writes_the_relay_data_point(
    f_switchable_driver: TuyaSwitchablePlugDriver,
    f_client: TuyaClientStub,
) -> None:
    f_switchable_driver.set_on(False)
    f_switchable_driver.set_on(True)

    assert f_client.writes == [(1, False), (1, True)]


def test_set_on_reports_tuya_error(f_refused_switch_driver: TuyaSwitchablePlugDriver) -> None:
    with pytest.raises(DeviceUnavailableError, match='Unable to Connect'):
        f_refused_switch_driver.set_on(False)


def test_set_on_reports_socket_errors(f_offline_switchable_driver: TuyaSwitchablePlugDriver) -> None:
    with pytest.raises(DeviceUnavailableError, match='connection refused'):
        f_offline_switchable_driver.set_on(False)


def test_switches_are_serialized_with_reads_across_threads(
    f_serial_switchable_driver: TuyaSwitchablePlugDriver,
    f_serial_client: SerialClientStub,
) -> None:
    threads = [
        threading.Thread(target=f_serial_switchable_driver.read_state),
        threading.Thread(target=f_serial_switchable_driver.set_on, args=(False,)),
        threading.Thread(target=f_serial_switchable_driver.set_on, args=(True,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert f_serial_client.max_active == 1
