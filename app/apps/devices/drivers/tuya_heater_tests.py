import threading
import time
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from apps.devices.domain.errors import DeviceProtocolError, DeviceUnavailableError
from apps.devices.domain.readings import ClimateMode, ClimateUnitState
from apps.devices.drivers.tuya_client import TuyaPayload
from apps.devices.drivers.tuya_heater import TuyaHeaterDriver

NOW = datetime(2026, 11, 4, 16, 20, tzinfo=UTC)
OFFICE_DPS = {'1': True, '2': False, '3': 22, '4': 21, '5': 0, '7': 'High'}


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


class RefusingClientStub(TuyaClientStub):
    def set_value(self, index: int, value: bool | int | str) -> TuyaPayload | None:
        super().set_value(index, value)
        return {'Error': 'Network Error: Unable to Connect', 'Err': '901'}


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
        return {'dps': OFFICE_DPS}

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
    return TuyaClientStub(status_payload={'dps': OFFICE_DPS})


@pytest.fixture
def f_driver(f_client: TuyaClientStub) -> TuyaHeaterDriver:
    return TuyaHeaterDriver(client=f_client, clock=_now)


@pytest.fixture
def f_payload_driver(request: pytest.FixtureRequest) -> TuyaHeaterDriver:
    return TuyaHeaterDriver(client=TuyaClientStub(status_payload=request.param), clock=_now)


@pytest.fixture
def f_offline_driver() -> TuyaHeaterDriver:
    client = TuyaClientStub(status_payload=None, error=ConnectionRefusedError('connection refused'))
    return TuyaHeaterDriver(client=client, clock=_now)


@pytest.fixture
def f_refusing_client() -> RefusingClientStub:
    return RefusingClientStub(status_payload={'dps': OFFICE_DPS})


@pytest.fixture
def f_refusing_driver(f_refusing_client: RefusingClientStub) -> TuyaHeaterDriver:
    return TuyaHeaterDriver(client=f_refusing_client, clock=_now)


@pytest.fixture
def f_serial_client() -> SerialClientStub:
    return SerialClientStub()


@pytest.fixture
def f_serial_driver(f_serial_client: SerialClientStub) -> TuyaHeaterDriver:
    return TuyaHeaterDriver(client=f_serial_client, clock=_now)


def test_read_state_maps_power_setpoint_temperature_and_level(f_driver: TuyaHeaterDriver) -> None:
    assert f_driver.read_state() == ClimateUnitState(
        mode=ClimateMode.HEAT,
        target=Decimal(22),
        room_temperature=Decimal(21),
        read_at=NOW,
        level='High',
    )


@pytest.mark.parametrize(
    ('f_payload_driver', 'mode', 'level'),
    [
        ({'dps': {**OFFICE_DPS, '1': False}}, ClimateMode.OFF, 'High'),
        ({'dps': {key: value for key, value in OFFICE_DPS.items() if key != '7'}}, ClimateMode.HEAT, None),
        ({'dps': {**OFFICE_DPS, '7': 1}}, ClimateMode.HEAT, None),
    ],
    ids=['off', 'level_missing', 'level_not_a_word'],
    indirect=['f_payload_driver'],
)
def test_read_state_reads_off_as_the_off_mode_and_never_fails_on_the_level(
    f_payload_driver: TuyaHeaterDriver,
    mode: ClimateMode,
    level: str | None,
) -> None:
    assert f_payload_driver.read_state() == ClimateUnitState(
        mode=mode,
        target=Decimal(22),
        room_temperature=Decimal(21),
        read_at=NOW,
        level=level,
    )


@pytest.mark.parametrize(
    'f_payload_driver',
    [
        {'dps': {**OFFICE_DPS, '1': 1}},
        {'dps': {**OFFICE_DPS, '3': '22'}},
        {'dps': {**OFFICE_DPS, '4': True}},
        {'dps': {key: value for key, value in OFFICE_DPS.items() if key != '4'}},
        {'devId': 'heater'},
    ],
    ids=['power_not_a_bool', 'setpoint_not_an_integer', 'temperature_a_bool', 'temperature_missing', 'no_dps'],
    indirect=True,
)
def test_read_state_rejects_an_unexpected_payload(f_payload_driver: TuyaHeaterDriver) -> None:
    with pytest.raises(DeviceProtocolError) as raised:
        f_payload_driver.read_state()

    assert raised.value.error_code == 'device_protocol_error'


@pytest.mark.parametrize(
    'f_payload_driver',
    [{'Error': 'Network Error: Unable to Connect', 'Err': '901'}, None],
    ids=['error_payload', 'empty'],
    indirect=True,
)
def test_read_state_reports_an_unreachable_heater(f_payload_driver: TuyaHeaterDriver) -> None:
    with pytest.raises(DeviceUnavailableError) as raised:
        f_payload_driver.read_state()

    assert raised.value.error_code == 'device_unavailable'


def test_read_state_reports_socket_errors(f_offline_driver: TuyaHeaterDriver) -> None:
    with pytest.raises(DeviceUnavailableError, match='connection refused'):
        f_offline_driver.read_state()


def test_commands_write_only_power_and_whole_degree_setpoints(
    f_driver: TuyaHeaterDriver,
    f_client: TuyaClientStub,
) -> None:
    f_driver.set_power(True)
    f_driver.set_setpoint(Decimal('22.0'))
    f_driver.set_power(False)

    assert f_client.writes == [(1, True), (3, 22), (1, False)]


def test_set_setpoint_refuses_a_fraction_of_a_degree_and_writes_nothing(
    f_driver: TuyaHeaterDriver,
    f_client: TuyaClientStub,
) -> None:
    with pytest.raises(DeviceProtocolError):
        f_driver.set_setpoint(Decimal('22.5'))

    assert f_client.writes == []


def test_commands_report_a_refused_write(f_refusing_driver: TuyaHeaterDriver) -> None:
    with pytest.raises(DeviceUnavailableError, match='Unable to Connect'):
        f_refusing_driver.set_setpoint(Decimal(19))


def test_commands_report_socket_errors(f_offline_driver: TuyaHeaterDriver) -> None:
    with pytest.raises(DeviceUnavailableError, match='connection refused'):
        f_offline_driver.set_power(False)


def test_client_calls_are_serialized_across_threads(
    f_serial_driver: TuyaHeaterDriver,
    f_serial_client: SerialClientStub,
) -> None:
    threads = [
        threading.Thread(target=f_serial_driver.read_state),
        threading.Thread(target=f_serial_driver.set_power, args=(True,)),
        threading.Thread(target=f_serial_driver.set_setpoint, args=(Decimal(22),)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert f_serial_client.max_active == 1
