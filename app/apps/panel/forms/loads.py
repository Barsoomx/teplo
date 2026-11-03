from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace

from django import forms
from django.forms import BaseFormSet
from django.http import QueryDict
from django.utils.translation import gettext_lazy

from apps.core.domain.settings import SettingProblem
from apps.panel.forms.load_settings import amps_field
from apps.panel.forms.problems import add_not_saved, add_problems
from apps.power.domain.load_config import PRIORITY_MAX, PRIORITY_MIN, LoadConfigRecord
from apps.power.domain.loads import MEASURED_PHASES, LoadPhase

PHASE_CHOICES = [
    *((phase.value, phase.value.upper()) for phase in MEASURED_PHASES),
    (LoadPhase.UNKNOWN.value, gettext_lazy('Phase unknown')),
]


class LoadForm(forms.Form):
    slug = forms.SlugField(widget=forms.HiddenInput)
    priority = forms.IntegerField(min_value=PRIORITY_MIN, max_value=PRIORITY_MAX)
    phase = forms.TypedChoiceField(choices=PHASE_CHOICES, coerce=LoadPhase)
    nominal_amps = amps_field(required=False)


LoadFormSet = forms.formset_factory(LoadForm, extra=0)


@dataclass(frozen=True, kw_only=True)
class LoadRow:
    form: LoadForm
    load: LoadConfigRecord | None


def load_formset(loads: Sequence[LoadConfigRecord], data: QueryDict | None = None) -> BaseFormSet:
    return LoadFormSet(
        data,
        initial=[
            {
                'slug': load.slug,
                'priority': load.config.priority,
                'phase': load.config.phase.value,
                'nominal_amps': load.config.nominal_amps,
            }
            for load in loads
        ],
        prefix='loads',
    )


def load_rows(formset: BaseFormSet, loads: Sequence[LoadConfigRecord]) -> list[LoadRow]:
    cards = {load.slug: load for load in loads}
    return [LoadRow(form=form, load=cards.get(str(form['slug'].value()))) for form in formset.forms]


def add_load_problems(formset: BaseFormSet, problems: Iterable[SettingProblem]) -> None:
    rows = {str(form['slug'].value()): form for form in formset.forms}
    for problem in problems:
        row = rows.get(problem.subject)
        if row is not None:
            add_problems(row, (problem,))
        elif formset.forms:
            add_problems(formset.forms[0], (replace(problem, field=''),))


def add_loads_not_saved(formset: BaseFormSet) -> None:
    if formset.forms:
        add_not_saved(formset.forms[0])
