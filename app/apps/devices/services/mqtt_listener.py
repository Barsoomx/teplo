from collections.abc import Callable
from pathlib import Path
from typing import Protocol

import structlog
from django.conf import settings

from apps.core.services.secrets import MqttSecret, SecretsFile
from apps.devices.domain.errors import BrokerUnavailableError
from apps.devices.drivers.esphome_messages import EsphomeMessageHandler
from apps.devices.drivers.esphome_protocol import SUBSCRIPTIONS
from apps.devices.drivers.mqtt_client import MqttClient, MqttReceiver

logger = structlog.get_logger(__name__)


class MqttConnection(Protocol):
    def start(self) -> None: ...

    def stop(self) -> None: ...

    def publish(self, topic: str, payload: bytes) -> None: ...


ConnectionFactory = Callable[[MqttSecret, tuple[str, ...], MqttReceiver], MqttConnection]


class MqttListener:
    def __init__(
        self,
        secret: MqttSecret | None,
        receiver_factory: Callable[[], MqttReceiver],
        connection_factory: ConnectionFactory,
    ) -> None:
        self._secret = secret
        self._receiver_factory = receiver_factory
        self._connection_factory = connection_factory
        self._connection: MqttConnection | None = None

    @classmethod
    def init(cls) -> 'MqttListener':
        return cls(
            secret=SecretsFile.init(Path(settings.TEPLO_SECRETS_FILE)).mqtt,
            receiver_factory=EsphomeMessageHandler.init,
            connection_factory=MqttClient.init,
        )

    def start(self) -> None:
        if self._secret is None:
            logger.warning('mqtt_not_configured')
            return

        self._connection = self._connection_factory(self._secret, SUBSCRIPTIONS, self._receiver_factory())
        self._connection.start()
        logger.info('mqtt_listener_started', host=self._secret.host, port=self._secret.port)

    def stop(self) -> None:
        if self._connection is None:
            return

        self._connection.stop()
        self._connection = None

    def publish(self, topic: str, payload: bytes) -> None:
        connection = self._connection
        if connection is None:
            raise BrokerUnavailableError('mqtt is not configured')

        connection.publish(topic, payload)


mqtt_listener = MqttListener.init()
