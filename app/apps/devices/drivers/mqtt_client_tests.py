from dataclasses import dataclass

import pytest
from paho.mqtt.client import MQTT_ERR_NO_CONN, MQTT_ERR_SUCCESS, ConnectFlags, DisconnectFlags, MQTTMessage
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.reasoncodes import ReasonCode
from structlog.testing import capture_logs

from apps.core.services.secrets import MqttSecret
from apps.devices.domain.errors import BrokerUnavailableError
from apps.devices.drivers.mqtt_client import MqttClient
from apps.devices.stubs import MqttReceiverStub

SECRET = MqttSecret(host='192.0.2.1', port=1883, username='teplo', password='mqtt-password')
TOPICS = ('teplo/+/sensor/+/state', 'teplo/+/status')
SUBSCRIBE = ('subscribe', [('teplo/+/sensor/+/state', 0), ('teplo/+/status', 0)])
CONNECTED = ReasonCode(PacketTypes.CONNACK, 'Success')
NOT_AUTHORIZED = ReasonCode(PacketTypes.CONNACK, 'Not authorized')
DISCONNECTED = ReasonCode(PacketTypes.DISCONNECT, 'Success')
CONNECTION_LOST = ReasonCode(PacketTypes.DISCONNECT, 'Unspecified error')
CONNECT_FLAGS = ConnectFlags(session_present=False)
DISCONNECT_FLAGS = DisconnectFlags(is_disconnect_packet_from_server=False)
MODE_COMMAND = 'teplo/teplo-ac-bedroom/climate/air_conditioner/mode/command'


@dataclass(frozen=True, kw_only=True)
class PublishInfoStub:
    rc: int


class PahoClientStub:
    def __init__(self, publish_rc: int = MQTT_ERR_SUCCESS) -> None:
        self._publish_rc = publish_rc
        self._calls: list[tuple[str, object]] = []

    @property
    def calls(self) -> list[tuple[str, object]]:
        return self._calls

    def connect_async(self, host: str, port: int, keepalive: int) -> None:
        self._calls.append(('connect_async', (host, port, keepalive)))

    def loop_start(self) -> object:
        self._calls.append(('loop_start', None))
        return None

    def loop_stop(self) -> object:
        self._calls.append(('loop_stop', None))
        return None

    def disconnect(self) -> object:
        self._calls.append(('disconnect', None))
        return None

    def subscribe(self, topic: list[tuple[str, int]]) -> object:
        self._calls.append(('subscribe', topic))
        return None

    def publish(self, topic: str, payload: bytes, qos: int, retain: bool) -> PublishInfoStub:
        self._calls.append(('publish', (topic, payload, qos, retain)))
        return PublishInfoStub(rc=self._publish_rc)


@pytest.fixture
def f_paho() -> PahoClientStub:
    return PahoClientStub()


@pytest.fixture
def f_receiver() -> MqttReceiverStub:
    return MqttReceiverStub()


@pytest.fixture
def f_client(f_paho: PahoClientStub, f_receiver: MqttReceiverStub) -> MqttClient:
    return MqttClient(client=f_paho, secret=SECRET, topics=TOPICS, receiver=f_receiver)


@pytest.fixture
def f_connected_client(f_client: MqttClient, f_paho: PahoClientStub) -> MqttClient:
    f_client.on_connect(f_paho, None, CONNECT_FLAGS, CONNECTED, None)
    return f_client


@pytest.fixture
def f_dropped_client(f_connected_client: MqttClient, f_paho: PahoClientStub) -> MqttClient:
    f_connected_client.on_disconnect(f_paho, None, DISCONNECT_FLAGS, CONNECTION_LOST, None)
    return f_connected_client


@pytest.fixture
def f_refused_client(f_client: MqttClient, f_paho: PahoClientStub) -> MqttClient:
    f_client.on_connect(f_paho, None, CONNECT_FLAGS, NOT_AUTHORIZED, None)
    return f_client


@pytest.fixture
def f_failing_client(f_paho: PahoClientStub) -> MqttClient:
    return MqttClient(client=f_paho, secret=SECRET, topics=TOPICS, receiver=MqttReceiverStub(error=RuntimeError('bug')))


@pytest.fixture
def f_refusing_client(f_receiver: MqttReceiverStub) -> MqttClient:
    paho = PahoClientStub(publish_rc=MQTT_ERR_NO_CONN)
    client = MqttClient(client=paho, secret=SECRET, topics=TOPICS, receiver=f_receiver)
    client.on_connect(paho, None, CONNECT_FLAGS, CONNECTED, None)
    return client


@pytest.fixture
def f_message() -> MQTTMessage:
    message = MQTTMessage(topic=b'teplo/teplo-outdoor/status')
    message.payload = b'offline'
    message.retain = True
    return message


def test_start_connects_in_the_background(f_client: MqttClient, f_paho: PahoClientStub) -> None:
    f_client.start()

    assert f_paho.calls == [('connect_async', ('192.0.2.1', 1883, 30)), ('loop_start', None)]


def test_connection_subscribes_to_the_topics_and_tells_the_receiver(
    f_client: MqttClient,
    f_paho: PahoClientStub,
    f_receiver: MqttReceiverStub,
) -> None:
    with capture_logs() as logs:
        f_client.on_connect(f_paho, None, CONNECT_FLAGS, CONNECTED, None)

    assert (f_paho.calls, f_receiver.made, f_receiver.lost, logs) == (
        [SUBSCRIBE],
        1,
        [],
        [{'event': 'mqtt_connected', 'host': '192.0.2.1', 'port': 1883, 'log_level': 'info'}],
    )


def test_refused_connection_fails_the_devices_and_subscribes_nothing(
    f_client: MqttClient,
    f_paho: PahoClientStub,
    f_receiver: MqttReceiverStub,
) -> None:
    with capture_logs() as logs:
        f_client.on_connect(f_paho, None, CONNECT_FLAGS, NOT_AUTHORIZED, None)

    assert (f_paho.calls, f_receiver.made, f_receiver.lost, logs) == (
        [],
        0,
        ['mqtt broker refused the connection: Not authorized'],
        [
            {
                'event': 'mqtt_connection_refused',
                'host': '192.0.2.1',
                'port': 1883,
                'reason': 'Not authorized',
                'log_level': 'warning',
            },
        ],
    )


def test_unreachable_broker_fails_the_devices(
    f_client: MqttClient,
    f_paho: PahoClientStub,
    f_receiver: MqttReceiverStub,
) -> None:
    with capture_logs() as logs:
        f_client.on_connect_fail(f_paho, None)

    assert (f_receiver.lost, logs) == (
        ['mqtt broker unreachable'],
        [{'event': 'mqtt_connect_failed', 'host': '192.0.2.1', 'port': 1883, 'log_level': 'warning'}],
    )


def test_lost_connection_fails_the_devices(
    f_connected_client: MqttClient,
    f_paho: PahoClientStub,
    f_receiver: MqttReceiverStub,
) -> None:
    f_connected_client.on_disconnect(f_paho, None, DISCONNECT_FLAGS, CONNECTION_LOST, None)

    assert f_receiver.lost == ['mqtt connection lost: Unspecified error']


def test_reconnection_after_a_lost_connection_tells_the_receiver_again(
    f_dropped_client: MqttClient,
    f_paho: PahoClientStub,
    f_receiver: MqttReceiverStub,
) -> None:
    f_dropped_client.on_connect(f_paho, None, CONNECT_FLAGS, CONNECTED, None)

    assert (f_receiver.made, f_receiver.lost, f_paho.calls) == (
        2,
        ['mqtt connection lost: Unspecified error'],
        [SUBSCRIBE, SUBSCRIBE],
    )


def test_disconnect_after_a_refusal_keeps_the_refusal(
    f_refused_client: MqttClient,
    f_paho: PahoClientStub,
    f_receiver: MqttReceiverStub,
) -> None:
    f_refused_client.on_disconnect(f_paho, None, DISCONNECT_FLAGS, CONNECTION_LOST, None)

    assert f_receiver.lost == ['mqtt broker refused the connection: Not authorized']


def test_stop_disconnects_without_failing_the_devices(
    f_connected_client: MqttClient,
    f_paho: PahoClientStub,
    f_receiver: MqttReceiverStub,
) -> None:
    f_connected_client.stop()
    f_connected_client.on_disconnect(f_paho, None, DISCONNECT_FLAGS, DISCONNECTED, None)

    assert (f_paho.calls[1:], f_receiver.lost) == ([('disconnect', None), ('loop_stop', None)], [])


def test_message_reaches_the_receiver_with_its_retain_flag(
    f_client: MqttClient,
    f_paho: PahoClientStub,
    f_receiver: MqttReceiverStub,
    f_message: MQTTMessage,
) -> None:
    f_client.on_message(f_paho, None, f_message)

    assert f_receiver.received == [('teplo/teplo-outdoor/status', b'offline', True)]


def test_failing_receiver_does_not_end_the_network_thread(
    f_failing_client: MqttClient,
    f_paho: PahoClientStub,
    f_message: MQTTMessage,
) -> None:
    with capture_logs() as logs:
        f_failing_client.on_message(f_paho, None, f_message)

    assert logs == [{'event': 'mqtt_message_failed', 'exc_info': True, 'log_level': 'error'}]


def test_publish_while_connected_sends_qos_0_not_retained(
    f_connected_client: MqttClient,
    f_paho: PahoClientStub,
) -> None:
    f_connected_client.publish(MODE_COMMAND, b'heat')

    assert f_paho.calls[1:] == [('publish', (MODE_COMMAND, b'heat', 0, False))]


@pytest.mark.parametrize('client', ['f_client', 'f_dropped_client'], ids=['never_connected', 'connection_lost'])
def test_publish_while_not_connected_is_refused_and_queues_nothing(
    client: str,
    request: pytest.FixtureRequest,
    f_paho: PahoClientStub,
) -> None:
    disconnected: MqttClient = request.getfixturevalue(client)

    with pytest.raises(BrokerUnavailableError) as raised:
        disconnected.publish(MODE_COMMAND, b'heat')

    assert (raised.value.error_code, raised.value.detail, [call for call in f_paho.calls if call[0] == 'publish']) == (
        'broker_unavailable',
        'mqtt broker not connected',
        [],
    )


def test_publish_paho_does_not_take_is_a_lost_broker(f_refusing_client: MqttClient) -> None:
    with pytest.raises(BrokerUnavailableError) as raised:
        f_refusing_client.publish(MODE_COMMAND, b'off')

    assert (raised.value.error_code, raised.value.detail) == (
        'broker_unavailable',
        'mqtt publish failed: The client is not currently connected.',
    )
