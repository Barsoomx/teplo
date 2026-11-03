from dataclasses import replace
from decimal import Decimal

import pytest

from apps.climate.domain.climate_config import ClimateConfig, climate_config_problems, start_offset_keeps_band
from apps.core.domain.settings import SettingProblem

DEFAULTS = ClimateConfig(
    air_start_offset=Decimal('0.30'),
    initial_early_stop_offset=Decimal('0.30'),
    air_min_band=Decimal('0.20'),
    learned_mornings_needed=3,
    preheat_lead_cap_minutes=240,
)


@pytest.mark.parametrize(
    'config',
    [
        DEFAULTS,
        replace(
            DEFAULTS,
            air_start_offset=Decimal('2.00'),
            initial_early_stop_offset=Decimal('1.50'),
            air_min_band=Decimal('0.10'),
            learned_mornings_needed=1,
            preheat_lead_cap_minutes=480,
        ),
        replace(DEFAULTS, air_start_offset=Decimal('1.00'), air_min_band=Decimal('1.00')),
    ],
    ids=['defaults', 'ends_of_the_ranges', 'widest_band'],
)
def test_climate_settings_within_their_ranges_pass(config: ClimateConfig) -> None:
    assert climate_config_problems(config) == ()


@pytest.mark.parametrize(
    ('config', 'fields'),
    [
        (replace(DEFAULTS, air_start_offset=Decimal('2.01')), ('air_start_offset',)),
        (replace(DEFAULTS, initial_early_stop_offset=Decimal('1.51')), ('initial_early_stop_offset',)),
        (replace(DEFAULTS, air_min_band=Decimal('0.09')), ('air_min_band',)),
        (replace(DEFAULTS, air_start_offset=Decimal('2.00'), air_min_band=Decimal('1.01')), ('air_min_band',)),
        (replace(DEFAULTS, learned_mornings_needed=0), ('learned_mornings_needed',)),
        (replace(DEFAULTS, preheat_lead_cap_minutes=481), ('preheat_lead_cap_minutes',)),
        (
            replace(
                DEFAULTS,
                preheat_lead_cap_minutes=500,
                learned_mornings_needed=0,
                air_start_offset=Decimal('2.50'),
            ),
            ('air_start_offset', 'learned_mornings_needed', 'preheat_lead_cap_minutes'),
        ),
    ],
    ids=[
        'start_offset',
        'early_stop_offset',
        'band_too_narrow',
        'band_too_wide',
        'mornings',
        'lead_cap',
        'several',
    ],
)
def test_climate_settings_out_of_range_are_refused_in_field_order(
    config: ClimateConfig,
    fields: tuple[str, ...],
) -> None:
    assert climate_config_problems(config) == tuple(
        SettingProblem(field=field, code='out_of_range') for field in fields
    )


@pytest.mark.parametrize(
    ('start_offset', 'min_band', 'keeps'),
    [
        (Decimal('0.20'), Decimal('0.20'), True),
        (Decimal('0.19'), Decimal('0.20'), False),
        (Decimal('0.00'), Decimal('0.10'), False),
    ],
    ids=['at_the_band', 'narrower_than_the_band', 'no_offset'],
)
def test_start_offset_narrower_than_the_min_band_is_refused_at_the_start_offset(
    start_offset: Decimal,
    min_band: Decimal,
    keeps: bool,
) -> None:
    config = replace(DEFAULTS, air_start_offset=start_offset, air_min_band=min_band)

    assert (start_offset_keeps_band(start_offset, min_band), climate_config_problems(config)) == (
        keeps,
        () if keeps else (SettingProblem(field='air_start_offset', code='below_min_band'),),
    )


def test_start_offset_below_the_band_is_refused_after_the_values_out_of_range() -> None:
    config = replace(
        DEFAULTS,
        air_start_offset=Decimal('0.25'),
        air_min_band=Decimal('0.50'),
        initial_early_stop_offset=Decimal('1.60'),
        learned_mornings_needed=0,
    )

    assert climate_config_problems(config) == (
        SettingProblem(field='initial_early_stop_offset', code='out_of_range'),
        SettingProblem(field='learned_mornings_needed', code='out_of_range'),
        SettingProblem(field='air_start_offset', code='below_min_band'),
    )


def test_normalized_offsets_keep_two_decimals() -> None:
    config = replace(
        DEFAULTS,
        air_start_offset=Decimal('0.4'),
        initial_early_stop_offset=Decimal('0.255'),
        air_min_band=Decimal('0.3'),
    )

    normalized = config.normalized()

    assert (
        str(normalized.air_start_offset),
        str(normalized.initial_early_stop_offset),
        str(normalized.air_min_band),
    ) == ('0.40', '0.26', '0.30')
