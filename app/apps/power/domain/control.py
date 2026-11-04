from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from apps.power.domain.load_config import ESTIMATE_BASE_AMPS
from apps.power.domain.loads import LoadKind, LoadPhase, LoadRecord


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
class EstimateSettings:
    phase: LoadPhase | None
    base_amps: Decimal


NO_ESTIMATE = EstimateSettings(phase=None, base_amps=ESTIMATE_BASE_AMPS)


@dataclass(frozen=True, kw_only=True)
class LoadControl:
    live: bool
    thresholds: PhaseThresholds
    default_amps: Decimal
    estimate: EstimateSettings = NO_ESTIMATE


def commands_live(control: LoadControl, load: LoadRecord, *, dry_run: bool, controller_live: bool) -> bool:
    if dry_run or not control.live or not load.live:
        return False

    return load.kind is LoadKind.PLUG or controller_live


def starts_live(control: LoadControl, load: LoadRecord, *, writes_live: bool) -> bool:
    return writes_live and control.live and load.live
