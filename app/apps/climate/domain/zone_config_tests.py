from dataclasses import replace
from decimal import Decimal

import pytest

from apps.climate.domain.zone_config import ZoneConfig, zone_config_problems
from apps.core.domain.settings import SettingProblem

KITCHEN = ZoneConfig(
    name='Кухня',
    heating_enabled=True,
    comfort_floor_setpoint=Decimal('28.0'),
    night_floor_setpoint=Decimal('22.0'),
    boost_floor_setpoint=Decimal('30.0'),
    idle_floor_setpoint=Decimal('18.0'),
    floor_limit_setpoint=Decimal('30.0'),
    comfort_air_target=Decimal('22.0'),
    night_air_target=Decimal('19.5'),
    preheat_lead_minutes=180,
)


@pytest.mark.parametrize(
    'config',
    [
        KITCHEN,
        replace(KITCHEN, comfort_floor_setpoint=Decimal('18.0'), night_floor_setpoint=Decimal('18.0')),
        replace(
            KITCHEN,
            comfort_floor_setpoint=Decimal('40.0'),
            boost_floor_setpoint=Decimal('40.0'),
            floor_limit_setpoint=Decimal('40.0'),
            night_floor_setpoint=Decimal('5.0'),
            idle_floor_setpoint=Decimal('5.0'),
            preheat_lead_minutes=240,
        ),
        replace(KITCHEN, preheat_lead_minutes=0, heating_enabled=False),
    ],
    ids=['reference_house', 'night_at_comfort', 'ends_of_the_thermostat_range', 'no_lead'],
)
def test_settings_the_thermostat_takes_in_order_pass(config: ZoneConfig) -> None:
    assert zone_config_problems(config) == ()


@pytest.mark.parametrize(
    ('config', 'problems'),
    [
        (
            replace(KITCHEN, comfort_floor_setpoint=Decimal('40.5'), boost_floor_setpoint=Decimal('40.5')),
            (
                SettingProblem(field='comfort_floor_setpoint', code='out_of_range'),
                SettingProblem(field='boost_floor_setpoint', code='out_of_range'),
            ),
        ),
        (
            replace(KITCHEN, night_floor_setpoint=Decimal('4.9')),
            (SettingProblem(field='night_floor_setpoint', code='out_of_range'),),
        ),
        (
            replace(KITCHEN, comfort_air_target=Decimal('41.0'), night_air_target=Decimal('41.0')),
            (
                SettingProblem(field='comfort_air_target', code='out_of_range'),
                SettingProblem(field='night_air_target', code='out_of_range'),
            ),
        ),
        (
            replace(KITCHEN, preheat_lead_minutes=241),
            (SettingProblem(field='preheat_lead_minutes', code='out_of_range'),),
        ),
        (
            replace(KITCHEN, preheat_lead_minutes=-1),
            (SettingProblem(field='preheat_lead_minutes', code='out_of_range'),),
        ),
        (
            replace(KITCHEN, night_floor_setpoint=Decimal('28.5')),
            (SettingProblem(field='night_floor_setpoint', code='above_comfort'),),
        ),
        (
            replace(KITCHEN, boost_floor_setpoint=Decimal('27.9')),
            (SettingProblem(field='boost_floor_setpoint', code='below_comfort'),),
        ),
        (
            replace(KITCHEN, idle_floor_setpoint=Decimal('30.0')),
            (SettingProblem(field='idle_floor_setpoint', code='not_below_limit'),),
        ),
        (
            replace(KITCHEN, night_air_target=Decimal('22.5')),
            (SettingProblem(field='night_air_target', code='above_comfort'),),
        ),
        (replace(KITCHEN, name='  '), (SettingProblem(field='name', code='blank'),)),
        (replace(KITCHEN, name='x' * 101), (SettingProblem(field='name', code='too_long'),)),
    ],
    ids=[
        'above_the_thermostat_range',
        'below_the_thermostat_range',
        'air_targets_out_of_range',
        'lead_over_four_hours',
        'negative_lead',
        'night_above_comfort',
        'boost_below_comfort',
        'idle_at_the_limit',
        'night_air_above_comfort_air',
        'blank_name',
        'long_name',
    ],
)
def test_settings_outside_the_thermostat_range_or_out_of_order_are_refused(
    config: ZoneConfig,
    problems: tuple[SettingProblem, ...],
) -> None:
    assert zone_config_problems(config) == problems


def test_normalized_settings_keep_one_decimal_and_a_trimmed_name() -> None:
    config = replace(KITCHEN, name=' Кухня ', comfort_floor_setpoint=Decimal('27'), night_air_target=Decimal('19.50'))

    normalized = config.normalized()

    assert (normalized.name, str(normalized.comfort_floor_setpoint), str(normalized.night_air_target)) == (
        'Кухня',
        '27.0',
        '19.5',
    )
