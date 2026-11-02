from collections.abc import Callable
from datetime import datetime

from apps.devices.domain.capabilities import SwitchablePlugProvider
from apps.devices.services.driver_registry import driver_registry
from apps.power.domain.loads import LoadRecord
from apps.power.domain.ports import ZoneSwitch
from apps.power.services.zone_switch import ThermostatZoneSwitch


class LoadSwitcher:
    def __init__(self, plugs: SwitchablePlugProvider, zones: ZoneSwitch) -> None:
        self._plugs = plugs
        self._zones = zones

    @classmethod
    def init(cls) -> 'LoadSwitcher':
        return cls(plugs=driver_registry, zones=ThermostatZoneSwitch.init())

    def shed(self, load: LoadRecord, at: datetime) -> bool:
        if load.zone_slug is None:
            self._plugs.switchable_plug(load.device_slug).set_on(False)
            return True

        return self._zones.switch_off(load.zone_slug, load.device_slug, at)

    def restore(self, load: LoadRecord, *, was_on: bool | None, permit: Callable[[], bool]) -> bool:
        if load.zone_slug is not None or was_on is not True:
            return True

        plug = self._plugs.switchable_plug(load.device_slug)
        if not permit():
            return False

        plug.set_on(True)
        return True

    def release(self, load: LoadRecord, *, was_on: bool | None, zone_writes_live: bool, at: datetime) -> None:
        if load.zone_slug is None:
            if was_on is True:
                self._plugs.switchable_plug(load.device_slug).set_on(True)
            return

        if not zone_writes_live:
            self._zones.switch_on(load.zone_slug, load.device_slug, at)
