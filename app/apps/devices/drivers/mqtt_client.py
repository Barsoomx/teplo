import uuid
from typing import Protocol

import structlog
from paho.mqtt.client import CallbackAPIVersion, Client, ConnectFlags, DisconnectFlags, MQTTMessage, MQTTv311
from paho.mqtt.properties import Properties
from paho.mqtt.reasoncodes import ReasonCode

from apps.core.services.secrets import MqttSecret

logger = structlog.get_logger(__name__)

KEEPALIVE_SECONDS = 30
RECONNECT_MIN_DELAY_SECONDS = 1
RECONNECT_MAX_DELAY_SECONDS = 30
QOS = 0


class MqttReceiver(Protocol):
    def receive(self, topic: str, payload: bytes, *, retained: bool) -> None: ...

    def connection_lost(self, detail: str) -> None: ...


class PahoClient(Protocol):
    def connect_async(self, host: str, port: int, keepalive: int) -> None: ...

    def loop_start(self) -> object: ...

    def loop_stop(self) -> object: ...

    def disconnect(self) -> object: ...

    def subscribe(self, topic: list[tuple[str, int]]) -> object: ...


class MqttClient:
    def __init__(self, client: PahoClient, secret: MqttSecret, topics: tuple[str, ...], receiver: MqttReceiver) -> None:
        self._client = client
        self._secret = secret
        self._topics = topics
        self._receiver = receiver
        self._connected = False

    @classmethod
    def init(cls, secret: MqttSecret, topics: tuple[str, ...], receiver: MqttReceiver) -> 'MqttClient':
        client = Client(
            callback_api_version=CallbackAPIVersion.VERSION2,
            client_id=f'teplo-{uuid.uuid4().hex[:8]}',
            protocol=MQTTv311,
        )
        client.username_pw_set(secret.username, secret.password)
        client.reconnect_delay_set(min_delay=RECONNECT_MIN_DELAY_SECONDS, max_delay=RECONNECT_MAX_DELAY_SECONDS)
        mqtt_client = cls(client=client, secret=secret, topics=topics, receiver=receiver)
        client.on_connect = mqtt_client.on_connect
        client.on_connect_fail = mqtt_client.on_connect_fail
        client.on_disconnect = mqtt_client.on_disconnect
        client.on_message = mqtt_client.on_message
        return mqtt_client

    def start(self) -> None:
        self._client.connect_async(self._secret.host, self._secret.port, KEEPALIVE_SECONDS)
        self._client.loop_start()

    def stop(self) -> None:
        self._client.disconnect()
        self._client.loop_stop()

    def on_connect(
        self,
        _client: object,
        _userdata: object,
        _flags: ConnectFlags,
        reason_code: ReasonCode,
        _properties: Properties | None,
    ) -> None:
        if reason_code.is_failure:
            logger.warning(
                'mqtt_connection_refused',
                host=self._secret.host,
                port=self._secret.port,
                reason=str(reason_code),
            )
            self._receiver.connection_lost(f'mqtt broker refused the connection: {reason_code}')
            return

        self._connected = True
        self._client.subscribe([(topic, QOS) for topic in self._topics])
        logger.info('mqtt_connected', host=self._secret.host, port=self._secret.port)

    def on_connect_fail(self, _client: object, _userdata: object) -> None:
        logger.warning('mqtt_connect_failed', host=self._secret.host, port=self._secret.port)
        self._receiver.connection_lost('mqtt broker unreachable')

    def on_disconnect(
        self,
        _client: object,
        _userdata: object,
        _flags: DisconnectFlags,
        reason_code: ReasonCode,
        _properties: Properties | None,
    ) -> None:
        if not self._connected:
            return

        self._connected = False
        if not reason_code.is_failure:
            return

        logger.warning('mqtt_connection_lost', reason=str(reason_code))
        self._receiver.connection_lost(f'mqtt connection lost: {reason_code}')

    def on_message(self, _client: object, _userdata: object, message: MQTTMessage) -> None:
        # An exception raised here would end paho's network thread, and with it every reading until a restart.
        try:
            self._receiver.receive(message.topic, message.payload, retained=message.retain)
        except Exception:
            logger.exception('mqtt_message_failed')
