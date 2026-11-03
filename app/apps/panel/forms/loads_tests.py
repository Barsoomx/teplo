from decimal import Decimal
from urllib.parse import urlencode

import pytest
from django.forms import BaseFormSet
from django.http import QueryDict

from apps.core.domain.settings import SettingProblem
from apps.panel.forms.loads import LoadForm, add_load_problems, load_formset, load_rows
from apps.power.domain.load_config import LoadConfig, LoadConfigRecord
from apps.power.domain.loads import LoadKind, LoadPhase

LOADS = (
    LoadConfigRecord(
        id=1,
        slug='boiler-1f',
        name='Бойлер 1 этаж',
        kind=LoadKind.PLUG,
        config=LoadConfig(priority=1, phase=LoadPhase.UNKNOWN, nominal_amps=Decimal('6.0')),
        live=True,
    ),
    LoadConfigRecord(
        id=2,
        slug='kitchen',
        name='Кухня',
        kind=LoadKind.ZONE,
        config=LoadConfig(priority=2, phase=LoadPhase.L2, nominal_amps=None),
        live=False,
    ),
)
POSTED = {
    'loads-TOTAL_FORMS': '2',
    'loads-INITIAL_FORMS': '2',
    'loads-MIN_NUM_FORMS': '0',
    'loads-MAX_NUM_FORMS': '1000',
    'loads-0-slug': 'boiler-1f',
    'loads-0-priority': '2',
    'loads-0-phase': 'unknown',
    'loads-0-nominal_amps': '6.0',
    'loads-1-slug': 'kitchen',
    'loads-1-priority': '1',
    'loads-1-phase': 'l2',
    'loads-1-nominal_amps': '',
}


@pytest.fixture
def f_checked_formset() -> BaseFormSet:
    formset = load_formset(LOADS, QueryDict(urlencode(POSTED)))
    formset.is_valid()
    return formset


def test_posted_rows_are_read_as_the_settings_of_each_load() -> None:
    formset = load_formset(LOADS, QueryDict(urlencode(POSTED)))

    assert (formset.is_valid(), [form.cleaned_data for form in formset.forms]) == (
        True,
        [
            {'slug': 'boiler-1f', 'priority': 2, 'phase': LoadPhase.UNKNOWN, 'nominal_amps': Decimal('6.0')},
            {'slug': 'kitchen', 'priority': 1, 'phase': LoadPhase.L2, 'nominal_amps': None},
        ],
    )


@pytest.mark.parametrize(
    ('field', 'value'),
    [('phase', 'l4'), ('priority', '0'), ('priority', ''), ('nominal_amps', '64'), ('slug', 'не slug')],
)
def test_row_with_a_setting_out_of_range_is_refused_at_its_field(field: str, value: str) -> None:
    formset = load_formset(LOADS, QueryDict(urlencode({**POSTED, f'loads-1-{field}': value})))

    assert (formset.is_valid(), [list(errors) for errors in formset.errors]) == (False, [[], [field]])


def test_unbound_rows_pair_each_load_with_its_settings() -> None:
    formset = load_formset(LOADS)

    rows = load_rows(formset, LOADS)

    assert [(row.load, row.form['priority'].value(), row.form['phase'].value()) for row in rows] == [
        (LOADS[0], 1, 'unknown'),
        (LOADS[1], 2, 'l2'),
    ]


def test_problems_of_the_use_case_land_on_the_rows_they_name(f_checked_formset: BaseFormSet) -> None:
    add_load_problems(
        f_checked_formset,
        (
            SettingProblem(field='priority', code='priority_taken', subject='kitchen'),
            SettingProblem(field='priority', code='priority_taken', subject='hallway'),
        ),
    )

    assert [form.errors for form in f_checked_formset.forms] == [
        {'__all__': ['Another load has this priority']},
        {'priority': ['Another load has this priority']},
    ]


def test_live_gate_is_no_field_of_a_row_and_a_posted_one_is_dropped() -> None:
    formset = load_formset(LOADS, QueryDict(urlencode({**POSTED, 'loads-0-live': 'on'})))

    assert (
        formset.is_valid(),
        'live' in LoadForm.base_fields,
        [sorted(form.cleaned_data) for form in formset.forms],
    ) == (
        True,
        False,
        [['nominal_amps', 'phase', 'priority', 'slug'], ['nominal_amps', 'phase', 'priority', 'slug']],
    )
