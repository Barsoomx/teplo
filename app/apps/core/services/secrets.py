import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from apps.core.domain.errors import SecretMissingError

DEFAULT_MQTT_PORT = 1883
LAST_PORT = 65535


@dataclass(frozen=True, kw_only=True)
class DeviceSecret:
    address: str = field(repr=False)
    device_id: str = field(repr=False)
    local_key: str = field(repr=False)
    protocol: str


@dataclass(frozen=True, kw_only=True)
class TelegramSecret:
    token: str = field(repr=False)
    chat_id: str
    proxy: str | None = field(repr=False)
    commands: bool = False


@dataclass(frozen=True, kw_only=True)
class MqttSecret:
    host: str = field(repr=False)
    port: int
    username: str = field(repr=False)
    password: str = field(repr=False)


class SecretsFile:
    def __init__(
        self,
        devices: dict[str, DeviceSecret],
        panel_password: str | None,
        telegram: TelegramSecret | None = None,
        mqtt: MqttSecret | None = None,
    ) -> None:
        self._devices = devices
        self._panel_password = panel_password
        self._telegram = telegram
        self._mqtt = mqtt

    @classmethod
    def init(cls, path: Path) -> 'SecretsFile':
        data = tomllib.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
        devices = {
            slug: DeviceSecret(
                address=str(entry['address']),
                device_id=str(entry['device_id']),
                local_key=str(entry['local_key']),
                protocol=str(entry.get('protocol', '3.3')),
            )
            for slug, entry in data.get('devices', {}).items()
        }
        return cls(
            devices=devices,
            panel_password=data.get('panel', {}).get('password'),
            telegram=_telegram(data.get('telegram', {})),
            mqtt=_mqtt(data.get('mqtt', {})),
        )

    @property
    def panel_password(self) -> str | None:
        return self._panel_password

    @property
    def telegram(self) -> TelegramSecret | None:
        return self._telegram

    @property
    def mqtt(self) -> MqttSecret | None:
        return self._mqtt

    def device(self, slug: str) -> DeviceSecret:
        if slug not in self._devices:
            raise SecretMissingError(slug)

        return self._devices[slug]


def _telegram(entry: dict[str, object]) -> TelegramSecret | None:
    token = str(entry.get('token') or '').strip()
    chat_id = str(entry.get('chat_id') or '').strip()
    if not token or not chat_id:
        return None

    return TelegramSecret(
        token=token,
        chat_id=chat_id,
        proxy=str(entry.get('proxy') or '').strip() or None,
        commands=entry.get('commands') is True,
    )


def _mqtt(entry: dict[str, object]) -> MqttSecret | None:
    host = str(entry.get('host') or '').strip()
    username = str(entry.get('username') or '').strip()
    password = str(entry.get('password') or '')
    port = _port(entry.get('port', DEFAULT_MQTT_PORT))
    if not host or not username or not password or port is None:
        return None

    return MqttSecret(host=host, port=port, username=username, password=password)


def _port(value: object) -> int | None:
    try:
        port = int(str(value))
    except ValueError:
        return None

    return port if 0 < port <= LAST_PORT else None
