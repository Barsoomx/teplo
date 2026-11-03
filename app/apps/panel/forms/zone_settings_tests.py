from dataclasses import replace
from decimal import Decimal
from urllib.parse import urlencode

import pytest
from django.http import QueryDict

from apps.climate.domain.modes import ZoneKind
from apps.climate.domain.zone_config import ZoneConfig, ZoneConfigRecord
from apps.panel.forms.zone_settings import ZoneSettingsForm

KITCHEN = ZoneConfigRecord(
    id=1,
    slug='kitchen',
    kind=ZoneKind.AIR,
    config=ZoneConfig(
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
    ),
    live_control=True,
    air_control_live=True,
)
BATHROOM = replace(KITCHEN, slug='bathroom-1f', kind=ZoneKind.FLOOR)
POSTED = {
    'name': 'Кухня',
    'heating_enabled': 'on',
    'comfort_floor_setpoint': '27.5',
    'night_floor_setpoint': '22',
    'boost_floor_setpoint': '30.0',
    'idle_floor_setpoint': '18',
    'floor_limit_setpoint': '30',
    'comfort_air_target': '22',
    'night_air_target': '19.5',
    'preheat_lead_minutes': '150',
}
GATES = ('live_control', 'air_control_live')


def test_air_zone_form_reads_every_setting() -> None:
    form = ZoneSettingsForm.for_zone(KITCHEN, QueryDict(urlencode(POSTED)))

    assert (form.is_valid(), form.cleaned_data) == (
        True,
        {
            'name': 'Кухня',
            'heating_enabled': True,
            'comfort_floor_setpoint': Decimal('27.5'),
            'night_floor_setpoint': Decimal('22'),
            'boost_floor_setpoint': Decimal('30.0'),
            'idle_floor_setpoint': Decimal('18'),
            'floor_limit_setpoint': Decimal('30'),
            'comfort_air_target': Decimal('22'),
            'night_air_target': Decimal('19.5'),
            'preheat_lead_minutes': 150,
        },
    )


def test_floor_zone_form_leaves_out_the_air_targets() -> None:
    form = ZoneSettingsForm.for_zone(BATHROOM, QueryDict(urlencode({**POSTED, 'comfort_air_target': 'warm'})))

    assert (form.is_valid(), 'comfort_air_target' in form.fields, 'comfort_air_target' in form.cleaned_data) == (
        True,
        False,
        False,
    )


def test_heating_left_unticked_reads_as_off() -> None:
    posted = {field: value for field, value in POSTED.items() if field != 'heating_enabled'}

    form = ZoneSettingsForm.for_zone(KITCHEN, QueryDict(urlencode(posted)))

    assert (form.is_valid(), form.cleaned_data['heating_enabled']) == (True, False)


@pytest.mark.parametrize(
    ('field', 'value'),
    [
        ('comfort_floor_setpoint', '40.5'),
        ('night_floor_setpoint', '4.5'),
        ('boost_floor_setpoint', '30.25'),
        ('floor_limit_setpoint', ''),
        ('comfort_air_target', 'warm'),
        ('preheat_lead_minutes', '241'),
        ('preheat_lead_minutes', '-5'),
        ('name', ''),
        ('name', 'x' * 101),
    ],
)
def test_setting_the_thermostat_would_not_take_is_refused_at_its_field(field: str, value: str) -> None:
    form = ZoneSettingsForm.for_zone(KITCHEN, QueryDict(urlencode({**POSTED, field: value})))

    assert (form.is_valid(), list(form.errors)) == (False, [field])


def test_unbound_form_shows_the_stored_settings_under_ids_of_the_zone() -> None:
    form = ZoneSettingsForm.for_zone(KITCHEN)

    assert (
        form['comfort_floor_setpoint'].value(),
        form['heating_enabled'].value(),
        form['preheat_lead_minutes'].value(),
        form['name'].auto_id,
    ) == (Decimal('28.0'), True, 180, 'zone-kitchen-name')


def test_gates_are_no_fields_of_the_form_and_posted_gates_are_dropped() -> None:
    form = ZoneSettingsForm.for_zone(
        KITCHEN,
        QueryDict(urlencode({**POSTED, 'live_control': 'on', 'air_control_live': ''})),
    )

    assert (form.is_valid(), [gate for gate in GATES if gate in form.fields or gate in form.cleaned_data]) == (True, [])
