from decimal import Decimal
from urllib.parse import urlencode

import pytest
from django.http import QueryDict

from apps.panel.forms.load_settings import LoadSettingsForm
from apps.power.domain.load_config import LoadSettingsConfig, LoadSettingsRecord

POSTED = {
    'start_ceiling_amps': '21',
    'shed_amps': '25.5',
    'shed_hold_seconds': '5',
    'restore_amps': '18',
    'restore_hold_seconds': '180',
    'start_interval_seconds': '45',
    'meter_lost_after_seconds': '90',
    'default_load_amps': '7.5',
}
STORED = LoadSettingsRecord(
    id=1,
    config=LoadSettingsConfig(
        start_ceiling_amps=Decimal('20.0'),
        shed_amps=Decimal('24.0'),
        shed_hold_seconds=3,
        restore_amps=Decimal('18.0'),
        restore_hold_seconds=120,
        start_interval_seconds=30,
        meter_lost_after_seconds=60,
        default_load_amps=Decimal('6.0'),
    ),
    load_management_live=True,
)


def test_load_settings_form_reads_every_setting() -> None:
    form = LoadSettingsForm(QueryDict(urlencode(POSTED)))

    assert (form.is_valid(), form.cleaned_data) == (
        True,
        {
            'start_ceiling_amps': Decimal('21'),
            'shed_amps': Decimal('25.5'),
            'shed_hold_seconds': 5,
            'restore_amps': Decimal('18'),
            'restore_hold_seconds': 180,
            'start_interval_seconds': 45,
            'meter_lost_after_seconds': 90,
            'default_load_amps': Decimal('7.5'),
        },
    )


@pytest.mark.parametrize(
    ('field', 'value'),
    [
        ('start_ceiling_amps', '63.5'),
        ('shed_amps', '0'),
        ('restore_amps', '18.25'),
        ('shed_hold_seconds', '0'),
        ('meter_lost_after_seconds', '3601'),
        ('default_load_amps', ''),
    ],
)
def test_load_setting_out_of_its_range_is_refused_at_its_field(field: str, value: str) -> None:
    form = LoadSettingsForm(QueryDict(urlencode({**POSTED, field: value})))

    assert (form.is_valid(), list(form.errors)) == (False, [field])


def test_unbound_form_shows_the_stored_settings_and_no_load_management_gate() -> None:
    form = LoadSettingsForm.for_settings(STORED)

    assert (
        form['shed_amps'].value(),
        form['shed_hold_seconds'].value(),
        form['shed_amps'].auto_id,
        'load_management_live' in form.fields,
    ) == (Decimal('24.0'), 3, 'load-settings-shed_amps', False)
