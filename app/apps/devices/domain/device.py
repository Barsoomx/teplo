from dataclasses import dataclass

from apps.devices.domain.kinds import DeviceDriver, DeviceKind


@dataclass(frozen=True, kw_only=True)
class DeviceSettings:
    slug: str
    name: str
    kind: DeviceKind
    room_slug: str | None = None
    node: str = ''


@dataclass(frozen=True, kw_only=True)
class EsphomeDevice:
    slug: str
    node: str
    driver: DeviceDriver
