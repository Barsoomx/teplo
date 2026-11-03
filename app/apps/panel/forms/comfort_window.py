from datetime import time

from django import forms
from django.core.exceptions import ValidationError

from apps.climate.domain.comfort_windows import WINDOW_STEP_MINUTES, on_grid
from apps.panel.forms.problems import OFF_GRID

TIME_ATTRS = {'type': 'time', 'step': str(WINDOW_STEP_MINUTES * 60)}


def validate_on_grid(value: time) -> None:
    if not on_grid(value):
        raise ValidationError(OFF_GRID, code='off_grid')


class ComfortWindowForm(forms.Form):
    start = forms.TimeField(widget=forms.TimeInput(format='%H:%M', attrs=TIME_ATTRS), validators=[validate_on_grid])
    end = forms.TimeField(widget=forms.TimeInput(format='%H:%M', attrs=TIME_ATTRS), validators=[validate_on_grid])
