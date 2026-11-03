from decimal import Decimal
from urllib.parse import urlencode

import pytest
from django.http import QueryDict

from apps.climate.domain.climate_config import ClimateConfig, ClimateConfigRecord
from apps.panel.forms.climate_settings import ClimateSettingsForm

POSTED = {
    'air_start_offset': '0.25',
    'initial_early_stop_offset': '0.3',
    'learned_mornings_needed': '5',
    'preheat_lead_cap_minutes': '210',
    'second_floor_guard_threshold': '18.5',
    'second_floor_guard_hysteresis': '0.5',
}
STORED = ClimateConfigRecord(
    id=None,
    config=ClimateConfig(
        air_start_offset=Decimal('0.30'),
        initial_early_stop_offset=Decimal('0.30'),
        learned_mornings_needed=3,
        preheat_lead_cap_minutes=240,
        second_floor_guard_threshold=Decimal('19.0'),
        second_floor_guard_hysteresis=Decimal('0.30'),
    ),
    second_floor_guard_live=True,
)


def test_climate_settings_form_reads_every_setting() -> None:
    form = ClimateSettingsForm(QueryDict(urlencode(POSTED)))

    assert (form.is_valid(), form.cleaned_data) == (
        True,
        {
            'air_start_offset': Decimal('0.25'),
            'initial_early_stop_offset': Decimal('0.3'),
            'learned_mornings_needed': 5,
            'preheat_lead_cap_minutes': 210,
            'second_floor_guard_threshold': Decimal('18.5'),
            'second_floor_guard_hysteresis': Decimal('0.5'),
        },
    )


@pytest.mark.parametrize(
    ('field', 'value'),
    [
        ('air_start_offset', '2.5'),
        ('air_start_offset', '0.125'),
        ('initial_early_stop_offset', '1.6'),
        ('learned_mornings_needed', '0'),
        ('preheat_lead_cap_minutes', '481'),
        ('second_floor_guard_threshold', '4'),
        ('second_floor_guard_hysteresis', '-0.1'),
    ],
)
def test_climate_setting_out_of_its_range_is_refused_at_its_field(field: str, value: str) -> None:
    form = ClimateSettingsForm(QueryDict(urlencode({**POSTED, field: value})))

    assert (form.is_valid(), list(form.errors)) == (False, [field])


def test_unbound_form_shows_the_stored_settings_and_no_guard_gate() -> None:
    form = ClimateSettingsForm.for_settings(STORED)

    assert (
        form['air_start_offset'].value(),
        form['learned_mornings_needed'].value(),
        form['air_start_offset'].auto_id,
        'second_floor_guard_live' in form.fields,
    ) == (Decimal('0.30'), 3, 'climate-air_start_offset', False)
