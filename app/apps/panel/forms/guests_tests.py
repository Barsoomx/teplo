import pytest
from django.http import QueryDict

from apps.panel.forms.guests import GuestsForm


@pytest.mark.parametrize('nights', [0, 1, 2, 3])
def test_guests_form_takes_up_to_three_nights_or_none(nights: int) -> None:
    form = GuestsForm(QueryDict(f'nights={nights}'))

    assert (form.is_valid(), form.cleaned_data['nights']) == (True, nights)


@pytest.mark.parametrize('posted', ['nights=4', 'nights=-1', 'nights=two', 'nights=', ''])
def test_guests_form_refuses_anything_else(posted: str) -> None:
    form = GuestsForm(QueryDict(posted))

    assert (form.is_valid(), list(form.errors)) == (False, ['nights'])
