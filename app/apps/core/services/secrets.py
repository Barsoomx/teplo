import tomllib
from dataclasses import dataclass
from pathlib import Path

from apps.core.domain.errors import SecretMissingError


@dataclass(frozen=True, kw_only=True)
class DeviceSecret:
    address: str
    device_id: str
    local_key: str
    protocol: str


class SecretsFile:
    def __init__(self, devices: dict[str, DeviceSecret], panel_password: str | None) -> None:
        self._devices = devices
        self._panel_password = panel_password

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
        return cls(devices=devices, panel_password=data.get('panel', {}).get('password'))

    @property
    def panel_password(self) -> str | None:
        return self._panel_password

    def device(self, slug: str) -> DeviceSecret:
        if slug not in self._devices:
            raise SecretMissingError(slug)

        return self._devices[slug]
