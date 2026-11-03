from django import forms

from apps.climate.domain.room_intent import OverrideChange

STEPS = (OverrideChange.UP, OverrideChange.DOWN)


class OverrideStepForm(forms.Form):
    step = forms.TypedChoiceField(choices=[(step.value, step.value) for step in STEPS], coerce=OverrideChange)
