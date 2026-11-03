from dataclasses import replace
from decimal import Decimal

import pytest

from apps.core.domain.settings import SettingProblem
from apps.power.domain.load_config import LoadConfig, LoadSettingsConfig, load_problems, load_settings_problems
from apps.power.domain.loads import LoadPhase

DEFAULTS = LoadSettingsConfig(
    start_ceiling_amps=Decimal('20.0'),
    shed_amps=Decimal('24.0'),
    shed_hold_seconds=3,
    restore_amps=Decimal('18.0'),
    restore_hold_seconds=120,
    start_interval_seconds=30,
    meter_lost_after_seconds=60,
    default_load_amps=Decimal('6.0'),
)
BOILER = LoadConfig(priority=1, phase=LoadPhase.UNKNOWN, nominal_amps=Decimal('6.0'))
KITCHEN = LoadConfig(priority=2, phase=LoadPhase.L2, nominal_amps=None)


@pytest.mark.parametrize(
    'config',
    [
        DEFAULTS,
        replace(DEFAULTS, restore_amps=Decimal('20.0')),
        replace(
            DEFAULTS,
            start_ceiling_amps=Decimal('62.9'),
            shed_amps=Decimal('63.0'),
            restore_amps=Decimal('0.1'),
            default_load_amps=Decimal('63.0'),
            shed_hold_seconds=1,
            restore_hold_seconds=3600,
        ),
    ],
    ids=['defaults', 'restore_at_the_start_ceiling', 'ends_of_the_ranges'],
)
def test_load_settings_in_range_and_order_pass(config: LoadSettingsConfig) -> None:
    assert load_settings_problems(config) == ()


@pytest.mark.parametrize(
    ('config', 'problems'),
    [
        (
            replace(DEFAULTS, restore_amps=Decimal('20.5')),
            (SettingProblem(field='restore_amps', code='above_start_ceiling'),),
        ),
        (
            replace(DEFAULTS, start_ceiling_amps=Decimal('24.0')),
            (SettingProblem(field='start_ceiling_amps', code='not_below_shed'),),
        ),
        (
            replace(DEFAULTS, shed_amps=Decimal('63.1'), meter_lost_after_seconds=0),
            (
                SettingProblem(field='shed_amps', code='out_of_range'),
                SettingProblem(field='meter_lost_after_seconds', code='out_of_range'),
            ),
        ),
        (
            replace(DEFAULTS, default_load_amps=Decimal('0.0'), start_interval_seconds=3601),
            (
                SettingProblem(field='start_interval_seconds', code='out_of_range'),
                SettingProblem(field='default_load_amps', code='out_of_range'),
            ),
        ),
    ],
    ids=['restore_above_the_start_ceiling', 'start_ceiling_at_the_shed_threshold', 'out_of_range', 'in_field_order'],
)
def test_load_settings_out_of_range_or_order_are_refused(
    config: LoadSettingsConfig,
    problems: tuple[SettingProblem, ...],
) -> None:
    assert load_settings_problems(config) == problems


def test_loads_with_priorities_of_their_own_pass() -> None:
    assert load_problems({'boiler-1f': BOILER, 'kitchen': KITCHEN}) == ()


def test_loads_sharing_a_priority_are_refused_both() -> None:
    assert load_problems({'boiler-1f': BOILER, 'kitchen': replace(KITCHEN, priority=1)}) == (
        SettingProblem(field='priority', code='priority_taken', subject='boiler-1f'),
        SettingProblem(field='priority', code='priority_taken', subject='kitchen'),
    )


def test_load_priority_or_nominal_current_out_of_range_is_refused() -> None:
    assert load_problems(
        {'boiler-1f': replace(BOILER, priority=0), 'kitchen': replace(KITCHEN, nominal_amps=Decimal('63.5'))},
    ) == (
        SettingProblem(field='priority', code='out_of_range', subject='boiler-1f'),
        SettingProblem(field='nominal_amps', code='out_of_range', subject='kitchen'),
    )


def test_normalized_amps_keep_one_decimal() -> None:
    settings = replace(DEFAULTS, shed_amps=Decimal('25'), default_load_amps=Decimal('6.50')).normalized()
    load = replace(BOILER, nominal_amps=Decimal('7')).normalized()

    assert (str(settings.shed_amps), str(settings.default_load_amps), str(load.nominal_amps)) == ('25.0', '6.5', '7.0')
