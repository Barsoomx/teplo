from django import forms

from apps.climate.domain.room_intent import MAX_GUEST_NIGHTS


class GuestsForm(forms.Form):
    nights = forms.IntegerField(min_value=0, max_value=MAX_GUEST_NIGHTS)
