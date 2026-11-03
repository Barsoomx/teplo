from collections.abc import Iterable

from django import forms
from django.utils.translation import gettext_lazy

from apps.core.domain.settings import SettingProblem

OFF_GRID = gettext_lazy('Use a time on the quarter hour: :00, :15, :30 or :45')
BELOW_MIN_BAND = gettext_lazy('Must not be below the minimum band, or heating stops above the target')
PROBLEM_MESSAGES = {
    'blank': gettext_lazy('Enter a name'),
    'too_long': gettext_lazy('The name is too long'),
    'out_of_range': gettext_lazy('Out of the allowed range'),
    'above_comfort': gettext_lazy('Must not be above comfort'),
    'below_comfort': gettext_lazy('Must not be below comfort'),
    'not_below_limit': gettext_lazy('Must be below the floor limit'),
    'below_min_band': BELOW_MIN_BAND,
    'above_start_ceiling': gettext_lazy('Must not be above the start ceiling'),
    'not_below_shed': gettext_lazy('Must be below the shed threshold'),
    'priority_taken': gettext_lazy('Another load has this priority'),
    'off_grid': OFF_GRID,
    'not_after_start': gettext_lazy('The window must end after it starts, on the same day'),
    'overlap': gettext_lazy('Comfort windows of a day must not overlap'),
}
UNKNOWN_PROBLEM = gettext_lazy('Not saved: check the values')


def problem_message(problem: SettingProblem) -> str:
    return str(PROBLEM_MESSAGES.get(problem.code, UNKNOWN_PROBLEM))


def add_problems(form: forms.BaseForm, problems: Iterable[SettingProblem]) -> None:
    for problem in problems:
        form.add_error(problem.field if problem.field in form.fields else None, problem_message(problem))
