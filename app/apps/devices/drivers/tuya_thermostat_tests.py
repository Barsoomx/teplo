import threading
import time
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from apps.devices.domain.errors import DeviceProtocolError, DeviceUnavailableError
from apps.devices.domain.readings import ThermostatMode, ThermostatState
from apps.devices.drivers.tuya_client import TuyaPayload
from apps.devices.drivers.tuya_thermostat import TuyaThermostatDriver

NOW = datetime(2026, 10, 29, 5, 0, tzinfo=UTC)
KITCHEN_DPS = {'1': True, '2': 'hold', '16': 305, '24': 224, '104': 0}


class TuyaClientStub:
    def __init__(self, status_payload: TuyaPayload | None, error: OSError | None = None) -> None:
        self._status_payload = status_payload
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
        return None


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
        return {'dps': KITCHEN_DPS}

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
def f_client() -> TuyaClientStub:
    return TuyaClientStub(status_payload={'dps': KITCHEN_DPS})


@pytest.fixture
def f_driver(f_client: TuyaClientStub) -> TuyaThermostatDriver:
    return TuyaThermostatDriver(client=f_client, clock=_now)


@pytest.fixture
def f_unknown_mode_driver() -> TuyaThermostatDriver:
    return TuyaThermostatDriver(client=TuyaClientStub(status_payload={'dps': {**KITCHEN_DPS, '2': 'eco'}}), clock=_now)


@pytest.fixture
def f_error_payload_driver() -> TuyaThermostatDriver:
    client = TuyaClientStub(status_payload={'Error': 'Network Error: Unable to Connect', 'Err': '901'})
    return TuyaThermostatDriver(client=client, clock=_now)


@pytest.fixture
def f_offline_driver() -> TuyaThermostatDriver:
    client = TuyaClientStub(status_payload=None, error=ConnectionRefusedError('connection refused'))
    return TuyaThermostatDriver(client=client, clock=_now)


@pytest.fixture
def f_serial_client() -> SerialClientStub:
    return SerialClientStub()


@pytest.fixture
def f_serial_driver(f_serial_client: SerialClientStub) -> TuyaThermostatDriver:
    return TuyaThermostatDriver(client=f_serial_client, clock=_now)


def test_read_state_maps_data_points(f_driver: TuyaThermostatDriver) -> None:
    assert f_driver.read_state() == ThermostatState(
        power=True,
        mode=ThermostatMode.HOLD,
        setpoint=Decimal('30.5'),
        floor_temperature=Decimal('22.4'),
        read_at=NOW,
    )


def test_commands_write_data_points(f_driver: TuyaThermostatDriver, f_client: TuyaClientStub) -> None:
    f_driver.set_setpoint(Decimal('28.0'))
    f_driver.set_power(True)
    f_driver.set_mode(ThermostatMode.HOLD)

    assert f_client.writes == [(16, 280), (1, True), (2, 'hold')]


def test_read_state_rejects_unknown_mode(f_unknown_mode_driver: TuyaThermostatDriver) -> None:
    with pytest.raises(DeviceProtocolError):
        f_unknown_mode_driver.read_state()


def test_read_state_reports_tuya_error(f_error_payload_driver: TuyaThermostatDriver) -> None:
    with pytest.raises(DeviceUnavailableError, match='Unable to Connect'):
        f_error_payload_driver.read_state()


def test_commands_report_socket_errors(f_offline_driver: TuyaThermostatDriver) -> None:
    with pytest.raises(DeviceUnavailableError, match='connection refused'):
        f_offline_driver.set_power(False)


def test_client_calls_are_serialized_across_threads(
    f_serial_driver: TuyaThermostatDriver,
    f_serial_client: SerialClientStub,
) -> None:
    threads = [
        threading.Thread(target=f_serial_driver.read_state),
        threading.Thread(target=f_serial_driver.set_power, args=(True,)),
        threading.Thread(target=f_serial_driver.set_setpoint, args=(Decimal('28.0'),)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert f_serial_client.max_active == 1
