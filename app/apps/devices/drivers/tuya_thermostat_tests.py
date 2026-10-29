from datetime import UTC, datetime
from decimal import Decimal

import pytest

from apps.devices.domain.errors import DeviceProtocolError, DeviceUnavailableError
from apps.devices.domain.readings import ThermostatMode, ThermostatState
from apps.devices.drivers.tuya_thermostat import TuyaPayload, TuyaThermostatDriver

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
