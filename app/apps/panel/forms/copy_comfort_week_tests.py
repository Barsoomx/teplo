from django.http import QueryDict

from apps.panel.forms.copy_comfort_week import CopyComfortWeekForm


def test_zones_to_copy_to_are_read_by_slug() -> None:
    form = CopyComfortWeekForm(QueryDict('zones=hallway&zones=living-room'))

    assert (form.is_valid(), form.cleaned_data) == (True, {'zones': ['hallway', 'living-room']})


def test_copy_without_a_zone_is_refused() -> None:
    form = CopyComfortWeekForm(QueryDict(''))

    assert (form.is_valid(), form.errors) == (False, {'zones': ['Pick at least one zone']})


def test_zone_that_is_not_a_slug_is_refused() -> None:
    form = CopyComfortWeekForm(QueryDict('zones=hallway&zones=../admin'))

    assert (form.is_valid(), list(form.errors)) == (False, ['zones'])
