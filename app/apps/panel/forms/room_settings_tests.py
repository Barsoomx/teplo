from dataclasses import replace
from decimal import Decimal
from urllib.parse import urlencode

import pytest
from django.http import QueryDict

from apps.climate.domain.room_climate import RoomClimateConfig, RoomClimateConfigRecord
from apps.panel.forms.room_settings import RoomSettingsForm

BEDROOM = RoomClimateConfigRecord(
    id=2,
    slug='bedroom',
    name='Спальня',
    floor_name='2 этаж',
    config=RoomClimateConfig(
        comfort_target=Decimal('20.5'),
        setback_target=Decimal('19.5'),
        backup_below=Decimal('19.0'),
        backup_hysteresis=Decimal('0.30'),
        guest_room=False,
    ),
    air_sensor_slug='air-second-floor',
    backup_zone_slug='bathroom-2f',
    backup_zone_name='Санузел 2 этаж',
    backup_live=False,
)
OPEN_SPACE = replace(
    BEDROOM,
    id=1,
    slug='open-space',
    name='Гостиная, кухня и прихожая',
    floor_name='1 этаж',
    air_sensor_slug=None,
    backup_zone_slug=None,
    backup_zone_name=None,
)
POSTED = {
    'comfort_target': '21',
    'setback_target': '19.5',
    'backup_below': '18.5',
    'backup_hysteresis': '0.5',
}


def test_room_form_of_a_room_with_backup_heat_reads_its_targets_and_its_backup_heat() -> None:
    form = RoomSettingsForm.for_room(BEDROOM, QueryDict(urlencode(POSTED)))

    assert (form.is_valid(), form.cleaned_data) == (
        True,
        {
            'comfort_target': Decimal('21'),
            'setback_target': Decimal('19.5'),
            'backup_below': Decimal('18.5'),
            'backup_hysteresis': Decimal('0.5'),
            'guest_room': False,
        },
    )


def test_room_form_of_a_room_without_backup_heat_leaves_its_backup_fields_out() -> None:
    form = RoomSettingsForm.for_room(OPEN_SPACE, QueryDict(urlencode({**POSTED, 'backup_below': 'warm'})))

    assert (form.is_valid(), list(form.fields), form.cleaned_data) == (
        True,
        ['comfort_target', 'setback_target', 'guest_room'],
        {'comfort_target': Decimal('21'), 'setback_target': Decimal('19.5'), 'guest_room': False},
    )


@pytest.mark.parametrize(
    ('posted', 'guest_room'),
    [({'guest_room': 'on'}, True), ({}, False)],
    ids=['ticked', 'unticked'],
)
def test_room_form_reads_the_guest_room_checkbox(posted: dict[str, str], guest_room: bool) -> None:
    form = RoomSettingsForm.for_room(OPEN_SPACE, QueryDict(urlencode({**POSTED, **posted})))

    assert (form.is_valid(), form.cleaned_data['guest_room']) == (True, guest_room)


@pytest.mark.parametrize(
    ('field', 'value'),
    [
        ('comfort_target', '40.5'),
        ('setback_target', '4.9'),
        ('setback_target', '19.55'),
        ('backup_below', ''),
        ('backup_hysteresis', '2.01'),
        ('backup_hysteresis', '0.125'),
    ],
)
def test_room_setting_out_of_its_range_is_refused_at_its_field(field: str, value: str) -> None:
    form = RoomSettingsForm.for_room(BEDROOM, QueryDict(urlencode({**POSTED, field: value})))

    assert (form.is_valid(), list(form.errors)) == (False, [field])


def test_unbound_room_form_shows_the_stored_settings_under_ids_of_the_room() -> None:
    form = RoomSettingsForm.for_room(BEDROOM)

    assert (
        form['comfort_target'].value(),
        form['backup_hysteresis'].value(),
        form['setback_target'].auto_id,
        'backup_live' in form.fields,
        form['guest_room'].value(),
        form['guest_room'].auto_id,
    ) == (Decimal('20.5'), Decimal('0.30'), 'room-bedroom-setback_target', False, False, 'room-bedroom-guest_room')
