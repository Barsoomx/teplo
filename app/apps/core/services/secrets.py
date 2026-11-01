import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from apps.core.domain.errors import SecretMissingError


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


class SecretsFile:
    def __init__(
        self,
        devices: dict[str, DeviceSecret],
        panel_password: str | None,
        telegram: TelegramSecret | None = None,
    ) -> None:
        self._devices = devices
        self._panel_password = panel_password
        self._telegram = telegram

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
        )

    @property
    def panel_password(self) -> str | None:
        return self._panel_password

    @property
    def telegram(self) -> TelegramSecret | None:
        return self._telegram

    def device(self, slug: str) -> DeviceSecret:
        if slug not in self._devices:
            raise SecretMissingError(slug)

        return self._devices[slug]


def _telegram(entry: dict[str, object]) -> TelegramSecret | None:
    token = str(entry.get('token') or '').strip()
    chat_id = str(entry.get('chat_id') or '').strip()
    if not token or not chat_id:
        return None

    return TelegramSecret(token=token, chat_id=chat_id, proxy=str(entry.get('proxy') or '').strip() or None)
