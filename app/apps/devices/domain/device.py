from dataclasses import dataclass

from apps.devices.domain.kinds import DeviceKind


@dataclass(frozen=True, kw_only=True)
class DeviceSettings:
    slug: str
    name: str
    kind: DeviceKind
