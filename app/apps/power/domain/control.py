from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from apps.power.domain.loads import LoadKind, LoadRecord


@dataclass(frozen=True, kw_only=True)
class PhaseThresholds:
    start_ceiling: Decimal
    shed: Decimal
    shed_hold: timedelta
    restore: Decimal
    restore_hold: timedelta
    shed_interval: timedelta
    restore_interval: timedelta
    start_interval: timedelta
    meter_lost_after: timedelta


@dataclass(frozen=True, kw_only=True)
class LoadControl:
    live: bool
    thresholds: PhaseThresholds
    default_amps: Decimal


def commands_live(control: LoadControl, load: LoadRecord, *, dry_run: bool, zone_live_control: bool) -> bool:
    if dry_run or not control.live or not load.live:
        return False

    return load.kind is LoadKind.PLUG or zone_live_control
