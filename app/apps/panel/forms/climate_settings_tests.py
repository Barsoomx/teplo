from collections.abc import Iterator
from decimal import Decimal
from urllib.parse import urlencode

import pytest
from django.http import QueryDict
from django.utils import translation

from apps.climate.domain.climate_config import ClimateConfig, ClimateConfigRecord
from apps.panel.forms.climate_settings import ClimateSettingsForm

POSTED = {
    'air_start_offset': '0.25',
    'initial_early_stop_offset': '0.3',
    'air_min_band': '0.15',
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
        air_min_band=Decimal('0.20'),
        learned_mornings_needed=3,
        preheat_lead_cap_minutes=240,
        second_floor_guard_threshold=Decimal('19.0'),
        second_floor_guard_hysteresis=Decimal('0.30'),
    ),
    second_floor_guard_live=True,
)


@pytest.fixture
def russian() -> Iterator[None]:
    with translation.override('ru'):
        yield


def test_climate_settings_form_reads_every_setting() -> None:
    form = ClimateSettingsForm(QueryDict(urlencode(POSTED)))

    assert (form.is_valid(), form.cleaned_data) == (
        True,
        {
            'air_start_offset': Decimal('0.25'),
            'initial_early_stop_offset': Decimal('0.3'),
            'air_min_band': Decimal('0.15'),
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
        ('air_min_band', '0.09'),
        ('air_min_band', '1.01'),
        ('learned_mornings_needed', '0'),
        ('preheat_lead_cap_minutes', '481'),
        ('second_floor_guard_threshold', '4'),
        ('second_floor_guard_hysteresis', '-0.1'),
    ],
)
def test_climate_setting_out_of_its_range_is_refused_at_its_field(field: str, value: str) -> None:
    form = ClimateSettingsForm(QueryDict(urlencode({**POSTED, field: value})))

    assert (form.is_valid(), list(form.errors)) == (False, [field])


@pytest.mark.parametrize(
    ('start_offset', 'min_band'),
    [('0.10', '0.15'), ('0.25', '0.40')],
    ids=['start_offset_narrowed', 'band_widened'],
)
def test_start_offset_below_the_min_band_is_refused_at_the_start_offset(start_offset: str, min_band: str) -> None:
    form = ClimateSettingsForm(
        QueryDict(urlencode({**POSTED, 'air_start_offset': start_offset, 'air_min_band': min_band}))
    )

    assert (form.is_valid(), form.errors) == (
        False,
        {'air_start_offset': ['Must not be below the minimum band, or heating stops above the target']},
    )


@pytest.mark.usefixtures('russian')
def test_start_offset_below_the_min_band_is_explained_in_the_page_language() -> None:
    form = ClimateSettingsForm(QueryDict(urlencode({**POSTED, 'air_start_offset': '0.10'})))

    assert (form.is_valid(), form.errors) == (
        False,
        {'air_start_offset': ['Не может быть меньше минимального зазора, иначе обогрев остановится выше цели']},
    )


def test_start_offset_at_the_min_band_passes() -> None:
    form = ClimateSettingsForm(QueryDict(urlencode({**POSTED, 'air_start_offset': '0.15'})))

    assert form.is_valid() is True


def test_start_offset_out_of_its_range_is_not_checked_against_the_min_band() -> None:
    form = ClimateSettingsForm(QueryDict(urlencode({**POSTED, 'air_start_offset': '-0.05'})))

    assert (form.is_valid(), form.errors) == (
        False,
        {'air_start_offset': ['Ensure this value is greater than or equal to 0.00.']},
    )


def test_unbound_form_shows_the_stored_settings_and_no_guard_gate() -> None:
    form = ClimateSettingsForm.for_settings(STORED)

    assert (
        form['air_start_offset'].value(),
        form['air_min_band'].value(),
        form['learned_mornings_needed'].value(),
        form['air_start_offset'].auto_id,
        'second_floor_guard_live' in form.fields,
    ) == (Decimal('0.30'), Decimal('0.20'), 3, 'climate-air_start_offset', False)
