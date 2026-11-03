from decimal import Decimal
from enum import StrEnum

import pytest

from apps.core.domain.settings import SettingChange, SettingValue, setting_changes, setting_value


class Phase(StrEnum):
    L2 = 'l2'


@pytest.mark.parametrize(
    ('value', 'expected'),
    [
        (Decimal('28.0'), '28.0'),
        (Decimal('0.30'), '0.30'),
        (True, True),
        (180, 180),
        (None, None),
        ('Кухня', 'Кухня'),
        (('06:30-23:30',), ('06:30-23:30',)),
    ],
)
def test_setting_value_keeps_what_the_journal_can_store(value: object, expected: SettingValue) -> None:
    assert setting_value(value) == expected


def test_setting_value_stores_an_enum_as_its_plain_value() -> None:
    value = setting_value(Phase.L2)

    assert (value, type(value)) == ('l2', str)


def test_setting_changes_name_the_changed_fields_only() -> None:
    changes = setting_changes(
        target='zone',
        subject='kitchen',
        name='Кухня',
        old={'comfort_floor_setpoint': Decimal('28.0'), 'heating_enabled': True, 'preheat_lead_minutes': 180},
        new={'comfort_floor_setpoint': Decimal('27.5'), 'heating_enabled': True, 'preheat_lead_minutes': 150},
    )

    assert changes == (
        SettingChange(
            target='zone',
            subject='kitchen',
            name='Кухня',
            field='comfort_floor_setpoint',
            old='28.0',
            new='27.5',
        ),
        SettingChange(target='zone', subject='kitchen', name='Кухня', field='preheat_lead_minutes', old=180, new=150),
    )


def test_setting_changes_take_an_equal_number_with_other_decimals_as_unchanged() -> None:
    changes = setting_changes(
        target='zone',
        subject='kitchen',
        name='Кухня',
        old={'comfort_floor_setpoint': Decimal('28.0')},
        new={'comfort_floor_setpoint': Decimal('28')},
    )

    assert changes == ()


def test_payload_names_the_object_the_field_and_both_values() -> None:
    change = SettingChange(
        target='schedule',
        subject='kitchen',
        name='Кухня',
        field='monday',
        old=('06:30-23:30',),
        new=(),
    )

    assert change.payload() == {
        'object': 'schedule',
        'name': 'Кухня',
        'field': 'monday',
        'old': ['06:30-23:30'],
        'new': [],
    }
