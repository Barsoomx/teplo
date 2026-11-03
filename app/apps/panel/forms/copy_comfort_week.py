from django import forms
from django.core.validators import slug_re
from django.utils.translation import gettext_lazy


class SlugsField(forms.MultipleChoiceField):
    def valid_value(self, value: str) -> bool:
        return slug_re.match(value) is not None


class CopyComfortWeekForm(forms.Form):
    rooms = SlugsField(error_messages={'required': gettext_lazy('Pick at least one room')})
