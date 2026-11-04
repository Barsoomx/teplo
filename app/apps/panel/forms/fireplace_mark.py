from django import forms

LIT = '1'
OUT = '0'


def _lit(value: str) -> bool:
    return value == LIT


class FireplaceMarkForm(forms.Form):
    lit = forms.TypedChoiceField(choices=[(LIT, LIT), (OUT, OUT)], coerce=_lit)
