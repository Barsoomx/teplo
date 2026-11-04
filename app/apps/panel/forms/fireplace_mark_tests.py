import pytest
from django.http import QueryDict

from apps.panel.forms.fireplace_mark import FireplaceMarkForm


@pytest.mark.parametrize(('posted', 'lit'), [('1', True), ('0', False)], ids=['lit', 'out'])
def test_fireplace_mark_form_reads_lit_or_out(posted: str, lit: bool) -> None:
    form = FireplaceMarkForm(QueryDict(f'lit={posted}'))

    assert (form.is_valid(), form.cleaned_data['lit']) == (True, lit)


@pytest.mark.parametrize('posted', ['lit=yes', 'lit=true', 'lit=', ''])
def test_fireplace_mark_form_refuses_anything_else(posted: str) -> None:
    form = FireplaceMarkForm(QueryDict(posted))

    assert (form.is_valid(), list(form.errors)) == (False, ['lit'])
