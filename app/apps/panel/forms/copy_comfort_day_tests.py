from django.http import QueryDict

from apps.panel.forms.copy_comfort_day import CopyComfortDayForm


def test_days_to_copy_to_are_read_as_weekdays() -> None:
    form = CopyComfortDayForm(QueryDict('weekdays=1&weekdays=5'))

    assert (form.is_valid(), form.cleaned_data) == (True, {'weekdays': [1, 5]})


def test_copy_without_a_day_is_refused() -> None:
    form = CopyComfortDayForm(QueryDict(''))

    assert (form.is_valid(), form.errors) == (False, {'weekdays': ['Pick at least one day']})


def test_day_outside_the_week_is_refused() -> None:
    form = CopyComfortDayForm(QueryDict('weekdays=1&weekdays=7'))

    assert (form.is_valid(), list(form.errors)) == (False, ['weekdays'])
