from datetime import time

import pytest
from django.http import QueryDict

from apps.panel.forms.comfort_window import ComfortWindowForm


@pytest.mark.parametrize(
    ('data', 'cleaned'),
    [
        ('start=06:30&end=23:30', {'start': time(6, 30), 'end': time(23, 30)}),
        ('start=00:00&end=23:45', {'start': time(0, 0), 'end': time(23, 45)}),
        ('start=07:15:00&end=08:00', {'start': time(7, 15), 'end': time(8, 0)}),
    ],
)
def test_window_on_the_quarter_hour_is_read(data: str, cleaned: dict[str, time]) -> None:
    form = ComfortWindowForm(QueryDict(data))

    assert (form.is_valid(), form.cleaned_data) == (True, cleaned)


def test_window_off_the_quarter_hour_is_refused_at_its_field() -> None:
    form = ComfortWindowForm(QueryDict('start=06:40&end=23:59'))

    assert (form.is_valid(), form.errors) == (
        False,
        {
            'start': ['Use a time on the quarter hour: :00, :15, :30 or :45'],
            'end': ['Use a time on the quarter hour: :00, :15, :30 or :45'],
        },
    )


def test_missing_or_garbled_time_is_refused() -> None:
    form = ComfortWindowForm(QueryDict('start=25:00'))

    assert (form.is_valid(), sorted(form.errors)) == (False, ['end', 'start'])
