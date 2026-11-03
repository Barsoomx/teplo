from django.http import QueryDict

from apps.panel.forms.copy_comfort_week import CopyComfortWeekForm


def test_rooms_to_copy_to_are_read_by_slug() -> None:
    form = CopyComfortWeekForm(QueryDict('rooms=bathroom-1f&rooms=open-space'))

    assert (form.is_valid(), form.cleaned_data) == (True, {'rooms': ['bathroom-1f', 'open-space']})


def test_copy_without_a_room_is_refused() -> None:
    form = CopyComfortWeekForm(QueryDict(''))

    assert (form.is_valid(), form.errors) == (False, {'rooms': ['Pick at least one room']})


def test_room_that_is_not_a_slug_is_refused() -> None:
    form = CopyComfortWeekForm(QueryDict('rooms=bathroom-1f&rooms=../admin'))

    assert (form.is_valid(), list(form.errors)) == (False, ['rooms'])
