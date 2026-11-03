from dataclasses import replace
from decimal import Decimal

import pytest

from apps.climate.domain.climate_config import ClimateConfig, climate_config_problems
from apps.core.domain.settings import SettingProblem

DEFAULTS = ClimateConfig(
    air_start_offset=Decimal('0.30'),
    initial_early_stop_offset=Decimal('0.30'),
    learned_mornings_needed=3,
    preheat_lead_cap_minutes=240,
    second_floor_guard_threshold=Decimal('19.0'),
    second_floor_guard_hysteresis=Decimal('0.30'),
)


@pytest.mark.parametrize(
    'config',
    [
        DEFAULTS,
        replace(
            DEFAULTS,
            air_start_offset=Decimal('0.00'),
            initial_early_stop_offset=Decimal('1.50'),
            learned_mornings_needed=1,
            preheat_lead_cap_minutes=480,
            second_floor_guard_threshold=Decimal('5.0'),
            second_floor_guard_hysteresis=Decimal('2.00'),
        ),
    ],
    ids=['defaults', 'ends_of_the_ranges'],
)
def test_climate_settings_within_their_ranges_pass(config: ClimateConfig) -> None:
    assert climate_config_problems(config) == ()


@pytest.mark.parametrize(
    ('config', 'fields'),
    [
        (replace(DEFAULTS, air_start_offset=Decimal('2.01')), ('air_start_offset',)),
        (replace(DEFAULTS, initial_early_stop_offset=Decimal('1.51')), ('initial_early_stop_offset',)),
        (replace(DEFAULTS, learned_mornings_needed=0), ('learned_mornings_needed',)),
        (replace(DEFAULTS, preheat_lead_cap_minutes=481), ('preheat_lead_cap_minutes',)),
        (replace(DEFAULTS, second_floor_guard_threshold=Decimal('4.9')), ('second_floor_guard_threshold',)),
        (replace(DEFAULTS, second_floor_guard_hysteresis=Decimal('-0.01')), ('second_floor_guard_hysteresis',)),
        (
            replace(
                DEFAULTS,
                second_floor_guard_hysteresis=Decimal('3.00'),
                learned_mornings_needed=0,
                air_start_offset=Decimal('2.50'),
            ),
            ('air_start_offset', 'learned_mornings_needed', 'second_floor_guard_hysteresis'),
        ),
    ],
    ids=['start_offset', 'early_stop_offset', 'mornings', 'lead_cap', 'guard_threshold', 'guard_hysteresis', 'several'],
)
def test_climate_settings_out_of_range_are_refused_in_field_order(
    config: ClimateConfig,
    fields: tuple[str, ...],
) -> None:
    assert climate_config_problems(config) == tuple(
        SettingProblem(field=field, code='out_of_range') for field in fields
    )


def test_normalized_offsets_keep_two_decimals_and_the_threshold_one() -> None:
    config = replace(
        DEFAULTS,
        air_start_offset=Decimal('0.4'),
        second_floor_guard_threshold=Decimal('18'),
        second_floor_guard_hysteresis=Decimal('0.5'),
    )

    normalized = config.normalized()

    assert (
        str(normalized.air_start_offset),
        str(normalized.second_floor_guard_threshold),
        str(normalized.second_floor_guard_hysteresis),
    ) == ('0.40', '18.0', '0.50')
