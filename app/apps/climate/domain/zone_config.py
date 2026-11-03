from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from apps.climate.domain.modes import ZoneKind
from apps.core.domain.settings import SettingProblem

ZONE = 'zone'
NAME_MAX_LENGTH = 100
SETPOINT_MIN = Decimal('5.0')
SETPOINT_MAX = Decimal('40.0')
SETPOINT_STEP = Decimal('0.1')
PREHEAT_LEAD_MAX_MINUTES = 240


@dataclass(frozen=True, kw_only=True)
class ZoneConfig:
    name: str
    heating_enabled: bool
    comfort_floor_setpoint: Decimal
    night_floor_setpoint: Decimal
    boost_floor_setpoint: Decimal
    idle_floor_setpoint: Decimal
    floor_limit_setpoint: Decimal
    comfort_air_target: Decimal
    night_air_target: Decimal
    preheat_lead_minutes: int

    def temperatures(self) -> dict[str, Decimal]:
        return {
            'comfort_floor_setpoint': self.comfort_floor_setpoint,
            'night_floor_setpoint': self.night_floor_setpoint,
            'boost_floor_setpoint': self.boost_floor_setpoint,
            'idle_floor_setpoint': self.idle_floor_setpoint,
            'floor_limit_setpoint': self.floor_limit_setpoint,
            'comfort_air_target': self.comfort_air_target,
            'night_air_target': self.night_air_target,
        }

    def normalized(self) -> 'ZoneConfig':
        return ZoneConfig(
            name=self.name.strip(),
            heating_enabled=self.heating_enabled,
            comfort_floor_setpoint=tenths(self.comfort_floor_setpoint),
            night_floor_setpoint=tenths(self.night_floor_setpoint),
            boost_floor_setpoint=tenths(self.boost_floor_setpoint),
            idle_floor_setpoint=tenths(self.idle_floor_setpoint),
            floor_limit_setpoint=tenths(self.floor_limit_setpoint),
            comfort_air_target=tenths(self.comfort_air_target),
            night_air_target=tenths(self.night_air_target),
            preheat_lead_minutes=self.preheat_lead_minutes,
        )


@dataclass(frozen=True, kw_only=True)
class ZoneConfigRecord:
    id: int
    slug: str
    kind: ZoneKind
    config: ZoneConfig
    live_control: bool
    air_control_live: bool


def tenths(value: Decimal) -> Decimal:
    return value.quantize(SETPOINT_STEP, rounding=ROUND_HALF_UP)


def zone_config_problems(config: ZoneConfig) -> tuple[SettingProblem, ...]:
    problems = [*_name_problems(config.name)]
    problems.extend(
        SettingProblem(field=field, code='out_of_range')
        for field, value in config.temperatures().items()
        if not SETPOINT_MIN <= value <= SETPOINT_MAX
    )
    if not 0 <= config.preheat_lead_minutes <= PREHEAT_LEAD_MAX_MINUTES:
        problems.append(SettingProblem(field='preheat_lead_minutes', code='out_of_range'))
    if config.night_floor_setpoint > config.comfort_floor_setpoint:
        problems.append(SettingProblem(field='night_floor_setpoint', code='above_comfort'))
    if config.boost_floor_setpoint < config.comfort_floor_setpoint:
        problems.append(SettingProblem(field='boost_floor_setpoint', code='below_comfort'))
    if config.idle_floor_setpoint >= config.floor_limit_setpoint:
        problems.append(SettingProblem(field='idle_floor_setpoint', code='not_below_limit'))
    if config.night_air_target > config.comfort_air_target:
        problems.append(SettingProblem(field='night_air_target', code='above_comfort'))
    return tuple(problems)


def _name_problems(name: str) -> tuple[SettingProblem, ...]:
    if not name.strip():
        return (SettingProblem(field='name', code='blank'),)

    if len(name.strip()) > NAME_MAX_LENGTH:
        return (SettingProblem(field='name', code='too_long'),)

    return ()
