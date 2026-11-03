from django import forms
from django.utils.translation import gettext_lazy

from apps.climate.domain.comfort_windows import WEEKDAYS


class CopyComfortDayForm(forms.Form):
    weekdays = forms.TypedMultipleChoiceField(
        choices=[(str(weekday), str(weekday)) for weekday in WEEKDAYS],
        coerce=int,
        error_messages={'required': gettext_lazy('Pick at least one day')},
    )
