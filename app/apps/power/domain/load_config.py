from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from apps.core.domain.settings import SettingProblem
from apps.power.domain.loads import LoadKind, LoadPhase

LOAD = 'load'
LOAD_SETTINGS = 'load_settings'
AMPS_MIN = Decimal('0.1')
AMPS_MAX = Decimal('63.0')
BASE_AMPS_MIN = Decimal('0.0')
ESTIMATE_BASE_AMPS = Decimal('2.0')
AMPS_STEP = Decimal('0.1')
SECONDS_MIN = 1
SECONDS_MAX = 3600
PRIORITY_MIN = 1
PRIORITY_MAX = 2147483647
LOAD_SETTINGS_FIELDS = (
    'start_ceiling_amps',
    'shed_amps',
    'shed_hold_seconds',
    'restore_amps',
    'restore_hold_seconds',
    'start_interval_seconds',
    'meter_lost_after_seconds',
    'default_load_amps',
)


@dataclass(frozen=True, kw_only=True)
class LoadSettingsConfig:
    start_ceiling_amps: Decimal
    shed_amps: Decimal
    shed_hold_seconds: int
    restore_amps: Decimal
    restore_hold_seconds: int
    start_interval_seconds: int
    meter_lost_after_seconds: int
    default_load_amps: Decimal

    def normalized(self) -> 'LoadSettingsConfig':
        return LoadSettingsConfig(
            start_ceiling_amps=amps_tenths(self.start_ceiling_amps),
            shed_amps=amps_tenths(self.shed_amps),
            shed_hold_seconds=self.shed_hold_seconds,
            restore_amps=amps_tenths(self.restore_amps),
            restore_hold_seconds=self.restore_hold_seconds,
            start_interval_seconds=self.start_interval_seconds,
            meter_lost_after_seconds=self.meter_lost_after_seconds,
            default_load_amps=amps_tenths(self.default_load_amps),
        )


@dataclass(frozen=True, kw_only=True)
class LoadSettingsRecord:
    id: int | None
    config: LoadSettingsConfig
    load_management_live: bool


@dataclass(frozen=True, kw_only=True)
class LoadConfig:
    priority: int
    phase: LoadPhase
    nominal_amps: Decimal | None

    def normalized(self) -> 'LoadConfig':
        return LoadConfig(
            priority=self.priority,
            phase=self.phase,
            nominal_amps=None if self.nominal_amps is None else amps_tenths(self.nominal_amps),
        )


@dataclass(frozen=True, kw_only=True)
class LoadConfigRecord:
    id: int
    slug: str
    name: str
    kind: LoadKind
    config: LoadConfig
    live: bool


def amps_tenths(value: Decimal) -> Decimal:
    return value.quantize(AMPS_STEP, rounding=ROUND_HALF_UP)


def load_settings_problems(config: LoadSettingsConfig) -> tuple[SettingProblem, ...]:
    amps = {
        'start_ceiling_amps': config.start_ceiling_amps,
        'shed_amps': config.shed_amps,
        'restore_amps': config.restore_amps,
        'default_load_amps': config.default_load_amps,
    }
    seconds = {
        'shed_hold_seconds': config.shed_hold_seconds,
        'restore_hold_seconds': config.restore_hold_seconds,
        'start_interval_seconds': config.start_interval_seconds,
        'meter_lost_after_seconds': config.meter_lost_after_seconds,
    }
    out_of_range = {field for field, value in amps.items() if not AMPS_MIN <= value <= AMPS_MAX}
    out_of_range.update(field for field, value in seconds.items() if not SECONDS_MIN <= value <= SECONDS_MAX)
    problems = [
        SettingProblem(field=field, code='out_of_range') for field in LOAD_SETTINGS_FIELDS if field in out_of_range
    ]
    if config.restore_amps > config.start_ceiling_amps:
        problems.append(SettingProblem(field='restore_amps', code='above_start_ceiling'))
    if config.start_ceiling_amps >= config.shed_amps:
        problems.append(SettingProblem(field='start_ceiling_amps', code='not_below_shed'))
    return tuple(problems)


def load_problems(configs: Mapping[str, LoadConfig]) -> tuple[SettingProblem, ...]:
    taken = Counter(config.priority for config in configs.values())
    problems: list[SettingProblem] = []
    for slug, config in configs.items():
        if not PRIORITY_MIN <= config.priority <= PRIORITY_MAX:
            problems.append(SettingProblem(field='priority', code='out_of_range', subject=slug))
        elif taken[config.priority] > 1:
            problems.append(SettingProblem(field='priority', code='priority_taken', subject=slug))
        if config.nominal_amps is not None and not AMPS_MIN <= config.nominal_amps <= AMPS_MAX:
            problems.append(SettingProblem(field='nominal_amps', code='out_of_range', subject=slug))
    return tuple(problems)
