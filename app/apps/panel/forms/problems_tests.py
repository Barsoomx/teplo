from collections.abc import Iterator

import pytest
from django.http import QueryDict
from django.utils import translation

from apps.core.domain.settings import SettingProblem
from apps.panel.forms.comfort_window import ComfortWindowForm
from apps.panel.forms.problems import add_problems, problem_message


@pytest.fixture
def russian() -> Iterator[None]:
    with translation.override('ru'):
        yield


@pytest.fixture
def f_checked_form() -> ComfortWindowForm:
    form = ComfortWindowForm(QueryDict('start=06:00&end=07:00'))
    form.is_valid()
    return form


def test_problems_land_on_their_fields_and_the_rest_on_the_form(f_checked_form: ComfortWindowForm) -> None:
    add_problems(
        f_checked_form,
        (
            SettingProblem(field='end', code='not_after_start'),
            SettingProblem(field='', code='overlap'),
            SettingProblem(field='weekday', code='out_of_range'),
        ),
    )

    assert f_checked_form.errors == {
        'end': ['The window must end after it starts, on the same day'],
        '__all__': ['Comfort windows of a day must not overlap', 'Out of the allowed range'],
    }


def test_problem_of_an_unknown_kind_reads_generically() -> None:
    assert problem_message(SettingProblem(field='name', code='mystery')) == 'Not saved: check the values'


@pytest.mark.usefixtures('russian')
@pytest.mark.parametrize(
    ('code', 'expected'),
    [
        ('above_comfort', 'Не может быть выше комфорта'),
        ('not_below_limit', 'Должно быть ниже предела'),
        ('priority_taken', 'Этот приоритет уже у другой нагрузки'),
        ('off_grid', 'Время с шагом 15 минут: :00, :15, :30 или :45'),
        ('overlap', 'Окна комфорта одного дня не должны пересекаться'),
    ],
)
def test_problem_messages_are_translated(code: str, expected: str) -> None:
    assert problem_message(SettingProblem(field='', code=code)) == expected
