import pytest
from structlog.testing import capture_logs

from apps.core.services.secrets import MqttSecret
from apps.devices.domain.errors import BrokerUnavailableError
from apps.devices.drivers.mqtt_client import MqttReceiver
from apps.devices.services.mqtt_listener import MqttListener
from apps.devices.stubs import MqttReceiverStub

SECRET = MqttSecret(host='192.0.2.1', port=1883, username='teplo', password='mqtt-password')
MODE_COMMAND = 'teplo/teplo-ac-bedroom/climate/air_conditioner/mode/command'


class MqttConnectionStub:
    def __init__(self) -> None:
        self._calls: list[str] = []
        self._published: list[tuple[str, bytes]] = []

    @property
    def calls(self) -> list[str]:
        return self._calls

    @property
    def published(self) -> list[tuple[str, bytes]]:
        return self._published

    def start(self) -> None:
        self._calls.append('start')

    def stop(self) -> None:
        self._calls.append('stop')

    def publish(self, topic: str, payload: bytes) -> None:
        self._published.append((topic, payload))


class ConnectionFactoryStub:
    def __init__(self, connection: MqttConnectionStub) -> None:
        self._connection = connection
        self._built: list[tuple[MqttSecret, tuple[str, ...], MqttReceiver]] = []

    @property
    def built(self) -> list[tuple[MqttSecret, tuple[str, ...], MqttReceiver]]:
        return self._built

    def __call__(self, secret: MqttSecret, topics: tuple[str, ...], receiver: MqttReceiver) -> MqttConnectionStub:
        self._built.append((secret, topics, receiver))
        return self._connection


@pytest.fixture
def f_connection() -> MqttConnectionStub:
    return MqttConnectionStub()


@pytest.fixture
def f_factory(f_connection: MqttConnectionStub) -> ConnectionFactoryStub:
    return ConnectionFactoryStub(connection=f_connection)


@pytest.fixture
def f_receiver() -> MqttReceiverStub:
    return MqttReceiverStub()


@pytest.fixture
def f_listener(f_factory: ConnectionFactoryStub, f_receiver: MqttReceiverStub) -> MqttListener:
    return MqttListener(secret=SECRET, receiver_factory=lambda: f_receiver, connection_factory=f_factory)


@pytest.fixture
def f_unconfigured_listener(f_factory: ConnectionFactoryStub, f_receiver: MqttReceiverStub) -> MqttListener:
    return MqttListener(secret=None, receiver_factory=lambda: f_receiver, connection_factory=f_factory)


@pytest.fixture
def f_started_listener(f_listener: MqttListener) -> MqttListener:
    f_listener.start()
    return f_listener


def test_start_connects_to_the_esphome_topics(
    f_listener: MqttListener,
    f_factory: ConnectionFactoryStub,
    f_connection: MqttConnectionStub,
    f_receiver: MqttReceiverStub,
) -> None:
    with capture_logs() as logs:
        f_listener.start()

    assert (f_factory.built, f_connection.calls, logs) == (
        [(SECRET, ('teplo/+/sensor/+/state', 'teplo/+/status', 'teplo/+/climate/+/+/state'), f_receiver)],
        ['start'],
        [{'event': 'mqtt_listener_started', 'host': '192.0.2.1', 'port': 1883, 'log_level': 'info'}],
    )


def test_without_an_mqtt_table_teplo_runs_without_mqtt_and_says_so(
    f_unconfigured_listener: MqttListener,
    f_factory: ConnectionFactoryStub,
) -> None:
    with capture_logs() as logs:
        f_unconfigured_listener.start()
        f_unconfigured_listener.stop()

    assert (f_factory.built, logs) == ([], [{'event': 'mqtt_not_configured', 'log_level': 'warning'}])


def test_stop_closes_the_connection_once(f_started_listener: MqttListener, f_connection: MqttConnectionStub) -> None:
    f_started_listener.stop()
    f_started_listener.stop()

    assert f_connection.calls == ['start', 'stop']


def test_publish_goes_out_through_the_connection(
    f_started_listener: MqttListener,
    f_connection: MqttConnectionStub,
) -> None:
    f_started_listener.publish(MODE_COMMAND, b'heat')

    assert f_connection.published == [(MODE_COMMAND, b'heat')]


@pytest.mark.parametrize('listener', ['f_unconfigured_listener', 'f_listener'], ids=['without_mqtt', 'not_started'])
def test_publish_without_a_connection_is_a_lost_broker(listener: str, request: pytest.FixtureRequest) -> None:
    unconnected: MqttListener = request.getfixturevalue(listener)

    with pytest.raises(BrokerUnavailableError) as raised:
        unconnected.publish(MODE_COMMAND, b'heat')

    assert (raised.value.error_code, raised.value.detail) == ('broker_unavailable', 'mqtt is not configured')
