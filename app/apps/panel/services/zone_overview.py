from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from django.conf import settings
from django.utils import timezone

from apps.climate.domain.floor_policy import decide_floor_target
from apps.climate.domain.modes import ScheduleMode, ZoneKind
from apps.climate.domain.schedule import resolve_mode
from apps.climate.domain.zone import ZoneSettings
from apps.climate.repositories.zone import ZoneRepository
from apps.devices.services.state_store import ThermostatStateStore, thermostat_state_store
from apps.system.domain.clock import ClockStatus, ClockTrustSource
from apps.system.services.clock_trust import clock_trust_source


@dataclass(frozen=True, kw_only=True)
class ZoneOverview:
    slug: str
    name: str
    kind: ZoneKind
    mode: ScheduleMode
    available: bool
    error: str | None
    floor_temperature: Decimal | None
    power: bool | None
    setpoint: Decimal | None
    target_setpoint: Decimal | None
    boosted: bool
    live_control: bool


@dataclass(frozen=True, kw_only=True)
class PanelOverview:
    zones: tuple[ZoneOverview, ...]
    clock: ClockStatus
    dry_run: bool


class ZoneOverviewService:
    def __init__(
        self,
        zones: ZoneRepository,
        store: ThermostatStateStore,
        clock: ClockTrustSource,
        dry_run: bool,
    ) -> None:
        self._zones = zones
        self._store = store
        self._clock = clock
        self._dry_run = dry_run

    @classmethod
    def init(cls) -> 'ZoneOverviewService':
        return cls(
            zones=ZoneRepository(),
            store=thermostat_state_store,
            clock=clock_trust_source(),
            dry_run=settings.TEPLO_DRY_RUN,
        )

    def overview(self, now: datetime) -> PanelOverview:
        local_now = timezone.localtime(now)
        clock = self._clock.status()
        return PanelOverview(
            zones=tuple(self._zone(zone, local_now, clock.trusted) for zone in self._zones.all_settings()),
            clock=clock,
            dry_run=self._dry_run,
        )

    def _zone(self, zone: ZoneSettings, now: datetime, clock_trusted: bool) -> ZoneOverview:
        snapshot = self._store.snapshot(zone.thermostat_slug, now)
        mode = resolve_mode(zone.windows, now, zone.preheat_lead)
        target = decide_floor_target(zone, mode, now, clock_trusted=clock_trusted)
        state = snapshot.state
        return ZoneOverview(
            slug=zone.slug,
            name=zone.name,
            kind=zone.kind,
            mode=mode,
            available=snapshot.available,
            error=snapshot.error,
            floor_temperature=state.floor_temperature if state else None,
            power=state.power if state else None,
            setpoint=state.setpoint if state else None,
            target_setpoint=target.setpoint,
            boosted=zone.is_boosted(now),
            live_control=zone.live_control,
        )
