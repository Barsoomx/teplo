from dataclasses import replace
from decimal import Decimal

import pytest

from apps.climate.domain.room_climate import RoomClimateConfig, room_climate_problems
from apps.core.domain.settings import SettingProblem

BEDROOM = RoomClimateConfig(
    comfort_target=Decimal('20.5'),
    setback_target=Decimal('19.5'),
    backup_below=Decimal('19.0'),
    backup_hysteresis=Decimal('0.30'),
    guest_room=False,
)


@pytest.mark.parametrize(
    'config',
    [
        BEDROOM,
        replace(BEDROOM, setback_target=Decimal('20.5'), backup_below=Decimal('20.0')),
        replace(
            BEDROOM,
            comfort_target=Decimal('40.0'),
            setback_target=Decimal('5.5'),
            backup_below=Decimal('5.0'),
            backup_hysteresis=Decimal('2.00'),
        ),
        replace(BEDROOM, guest_room=True),
    ],
    ids=['as_seeded', 'night_at_comfort', 'ends_of_the_ranges', 'guest_room'],
)
def test_room_settings_in_order_pass(config: RoomClimateConfig) -> None:
    assert room_climate_problems(config, has_backup=True) == ()


def test_problems_refuse_a_night_target_above_comfort() -> None:
    config = replace(BEDROOM, setback_target=Decimal('20.6'), backup_below=Decimal('18.0'))

    assert room_climate_problems(config, has_backup=False) == (
        SettingProblem(field='setback_target', code='above_comfort'),
    )


@pytest.mark.parametrize(
    ('below', 'has_backup', 'problems'),
    [
        (Decimal('19.0'), True, ()),
        (Decimal('19.1'), True, (SettingProblem(field='backup_below', code='not_below_setback'),)),
        (Decimal('19.5'), True, (SettingProblem(field='backup_below', code='not_below_setback'),)),
        (Decimal('19.5'), False, ()),
        (Decimal('25.0'), False, ()),
    ],
    ids=['half_a_degree_below', 'closer', 'at_the_night_target', 'without_a_backup_zone', 'above_without_a_zone'],
)
def test_problems_refuse_backup_heat_within_half_a_degree_of_the_night_target_only_with_a_backup_zone(
    below: Decimal,
    has_backup: bool,
    problems: tuple[SettingProblem, ...],
) -> None:
    assert room_climate_problems(replace(BEDROOM, backup_below=below), has_backup=has_backup) == problems


@pytest.mark.parametrize(
    ('config', 'fields'),
    [
        (replace(BEDROOM, comfort_target=Decimal('40.1')), ('comfort_target',)),
        (
            replace(BEDROOM, setback_target=Decimal('4.9'), backup_below=Decimal('4.0')),
            ('setback_target', 'backup_below'),
        ),
        (replace(BEDROOM, backup_below=Decimal('4.9')), ('backup_below',)),
        (replace(BEDROOM, backup_hysteresis=Decimal('-0.01')), ('backup_hysteresis',)),
        (replace(BEDROOM, backup_hysteresis=Decimal('2.01')), ('backup_hysteresis',)),
    ],
    ids=['comfort', 'night_and_backup', 'backup_threshold', 'hysteresis_below', 'hysteresis_above'],
)
def test_problems_refuse_values_out_of_range(config: RoomClimateConfig, fields: tuple[str, ...]) -> None:
    assert room_climate_problems(config, has_backup=True) == tuple(
        SettingProblem(field=field, code='out_of_range') for field in fields
    )


def test_normalized_rounds_targets_to_tenths_and_hysteresis_to_hundredths_and_keeps_the_guest_room() -> None:
    config = RoomClimateConfig(
        comfort_target=Decimal('21'),
        setback_target=Decimal('19.45'),
        backup_below=Decimal('18.04'),
        backup_hysteresis=Decimal('0.305'),
        guest_room=True,
    )

    normalized = config.normalized()

    assert (
        str(normalized.comfort_target),
        str(normalized.setback_target),
        str(normalized.backup_below),
        str(normalized.backup_hysteresis),
        normalized.guest_room,
    ) == ('21.0', '19.5', '18.0', '0.31', True)
