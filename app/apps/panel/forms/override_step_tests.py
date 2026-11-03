import pytest
from django.http import QueryDict

from apps.climate.domain.room_intent import OverrideChange
from apps.panel.forms.override_step import OverrideStepForm


@pytest.mark.parametrize(('step', 'change'), [('up', OverrideChange.UP), ('down', OverrideChange.DOWN)])
def test_override_step_form_reads_half_a_degree_up_or_down(step: str, change: OverrideChange) -> None:
    form = OverrideStepForm(QueryDict(f'step={step}'))

    assert (form.is_valid(), form.cleaned_data['step']) == (True, change)


@pytest.mark.parametrize('posted', ['step=warmer', 'step=set', 'step=', ''])
def test_override_step_form_refuses_the_bot_changes_and_anything_else(posted: str) -> None:
    form = OverrideStepForm(QueryDict(posted))

    assert (form.is_valid(), list(form.errors)) == (False, ['step'])
