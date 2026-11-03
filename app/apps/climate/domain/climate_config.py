from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from apps.climate.domain.learning import EARLY_STOP_OFFSET_MAX, EARLY_STOP_OFFSET_MIN
from apps.climate.domain.zone_config import SETPOINT_MAX, SETPOINT_MIN, tenths
from apps.core.domain.settings import SettingProblem

CLIMATE_SETTINGS = 'climate_settings'
OFFSET_MIN = Decimal('0.00')
OFFSET_MAX = Decimal('2.00')
OFFSET_STEP = Decimal('0.01')
MIN_BAND_MIN = Decimal('0.10')
MIN_BAND_MAX = Decimal('1.00')
PREHEAT_LEAD_CAP_MAX_MINUTES = 480
MORNINGS_NEEDED_MIN = 1
MORNINGS_NEEDED_MAX = 32767
FIELDS = (
    'air_start_offset',
    'initial_early_stop_offset',
    'air_min_band',
    'learned_mornings_needed',
    'preheat_lead_cap_minutes',
    'second_floor_guard_threshold',
    'second_floor_guard_hysteresis',
)


@dataclass(frozen=True, kw_only=True)
class ClimateConfig:
    air_start_offset: Decimal
    initial_early_stop_offset: Decimal
    air_min_band: Decimal
    learned_mornings_needed: int
    preheat_lead_cap_minutes: int
    second_floor_guard_threshold: Decimal
    second_floor_guard_hysteresis: Decimal

    def normalized(self) -> 'ClimateConfig':
        return ClimateConfig(
            air_start_offset=hundredths(self.air_start_offset),
            initial_early_stop_offset=hundredths(self.initial_early_stop_offset),
            air_min_band=hundredths(self.air_min_band),
            learned_mornings_needed=self.learned_mornings_needed,
            preheat_lead_cap_minutes=self.preheat_lead_cap_minutes,
            second_floor_guard_threshold=tenths(self.second_floor_guard_threshold),
            second_floor_guard_hysteresis=hundredths(self.second_floor_guard_hysteresis),
        )


@dataclass(frozen=True, kw_only=True)
class ClimateConfigRecord:
    id: int | None
    config: ClimateConfig
    second_floor_guard_live: bool


def hundredths(value: Decimal) -> Decimal:
    return value.quantize(OFFSET_STEP, rounding=ROUND_HALF_UP)


def start_offset_keeps_band(start_offset: Decimal, min_band: Decimal) -> bool:
    return start_offset >= min_band


def climate_config_problems(config: ClimateConfig) -> tuple[SettingProblem, ...]:
    decimals = (
        ('air_start_offset', config.air_start_offset, OFFSET_MIN, OFFSET_MAX),
        ('initial_early_stop_offset', config.initial_early_stop_offset, EARLY_STOP_OFFSET_MIN, EARLY_STOP_OFFSET_MAX),
        ('air_min_band', config.air_min_band, MIN_BAND_MIN, MIN_BAND_MAX),
        ('second_floor_guard_threshold', config.second_floor_guard_threshold, SETPOINT_MIN, SETPOINT_MAX),
        ('second_floor_guard_hysteresis', config.second_floor_guard_hysteresis, OFFSET_MIN, OFFSET_MAX),
    )
    integers = (
        ('learned_mornings_needed', config.learned_mornings_needed, MORNINGS_NEEDED_MIN, MORNINGS_NEEDED_MAX),
        ('preheat_lead_cap_minutes', config.preheat_lead_cap_minutes, 0, PREHEAT_LEAD_CAP_MAX_MINUTES),
    )
    out_of_range = [field for field, value, lowest, highest in decimals if not lowest <= value <= highest]
    out_of_range.extend(field for field, number, lowest, highest in integers if not lowest <= number <= highest)
    problems = [SettingProblem(field=field, code='out_of_range') for field in sorted(out_of_range, key=FIELDS.index)]
    if not start_offset_keeps_band(config.air_start_offset, config.air_min_band):
        problems.append(SettingProblem(field='air_start_offset', code='below_min_band'))
    return tuple(problems)
