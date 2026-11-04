from collections.abc import Callable
from datetime import datetime

from apps.climate.domain.write_guard import MIN_WRITE_INTERVAL
from apps.devices.domain.capabilities import SwitchablePlugProvider
from apps.devices.services.driver_registry import driver_registry
from apps.power.domain.loads import LoadRecord
from apps.power.domain.ports import SourceSwitch, ZoneSwitch
from apps.power.domain.shedding import HandBack
from apps.power.services.source_switch import ClimateUnitSourceSwitch
from apps.power.services.zone_switch import ThermostatZoneSwitch


class LoadSwitcher:
    def __init__(self, plugs: SwitchablePlugProvider, zones: ZoneSwitch, sources: SourceSwitch) -> None:
        self._plugs = plugs
        self._zones = zones
        self._sources = sources

    @classmethod
    def init(cls) -> 'LoadSwitcher':
        return cls(plugs=driver_registry, zones=ThermostatZoneSwitch.init(), sources=ClimateUnitSourceSwitch.init())

    def shed(self, load: LoadRecord, at: datetime) -> bool:
        if load.source_slug is not None:
            return self._sources.switch_off(load.source_slug, load.device_slug, at)

        if load.zone_slug is None:
            self._plugs.switchable_plug(load.device_slug).set_on(False)
            return True

        return self._zones.switch_off(load.zone_slug, load.device_slug, at)

    def restore(self, load: LoadRecord, *, device: str, was_on: bool | None, permit: Callable[[], bool]) -> bool:
        if load.zone_slug is not None or load.source_slug is not None or was_on is not True:
            return True

        plug = self._plugs.switchable_plug(device)
        if not permit():
            return False

        plug.set_on(True)
        return True

    def release(
        self,
        load: LoadRecord,
        *,
        device: str,
        was_on: bool | None,
        cycle_writes: bool,
        at: datetime,
        read_at: float,
    ) -> HandBack:
        if load.source_slug is not None:
            return self._release_source(load, load.source_slug, device=device, was_on=was_on, at=at, read_at=read_at)

        if load.zone_slug is None:
            if was_on is not True:
                return HandBack.UNTOUCHED

            self._plugs.switchable_plug(device).set_on(True)
            return HandBack.SWITCHED_ON

        if cycle_writes:
            return HandBack.UNTOUCHED

        if not self._zones.switch_on(load.zone_slug, device, at, stamped_at=load.last_command_at, read_at=read_at):
            return HandBack.WAITING

        return HandBack.SWITCHED_ON

    def _release_source(
        self,
        load: LoadRecord,
        source: str,
        *,
        device: str,
        was_on: bool | None,
        at: datetime,
        read_at: float,
    ) -> HandBack:
        if was_on is not True:
            return HandBack.UNTOUCHED

        switched = self._sources.switch_on(
            source,
            device,
            at,
            stamped_at=load.last_command_at,
            read_at=read_at,
            interval=load.write_interval or MIN_WRITE_INTERVAL,
        )
        return HandBack.SWITCHED_ON if switched else HandBack.WAITING
