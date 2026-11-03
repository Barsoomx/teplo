from dataclasses import asdict
from typing import Any

from django import forms
from django.http import QueryDict

from apps.climate.domain.climate_config import (
    MIN_BAND_MAX,
    MIN_BAND_MIN,
    MORNINGS_NEEDED_MAX,
    MORNINGS_NEEDED_MIN,
    OFFSET_MAX,
    OFFSET_MIN,
    PREHEAT_LEAD_CAP_MAX_MINUTES,
    ClimateConfigRecord,
    start_offset_keeps_band,
)
from apps.climate.domain.learning import EARLY_STOP_OFFSET_MAX, EARLY_STOP_OFFSET_MIN
from apps.panel.forms.problems import BELOW_MIN_BAND


def offset_field() -> forms.DecimalField:
    return forms.DecimalField(min_value=OFFSET_MIN, max_value=OFFSET_MAX, max_digits=3, decimal_places=2)


class ClimateSettingsForm(forms.Form):
    air_start_offset = offset_field()
    initial_early_stop_offset = forms.DecimalField(
        min_value=EARLY_STOP_OFFSET_MIN,
        max_value=EARLY_STOP_OFFSET_MAX,
        max_digits=3,
        decimal_places=2,
    )
    air_min_band = forms.DecimalField(min_value=MIN_BAND_MIN, max_value=MIN_BAND_MAX, max_digits=3, decimal_places=2)
    learned_mornings_needed = forms.IntegerField(min_value=MORNINGS_NEEDED_MIN, max_value=MORNINGS_NEEDED_MAX)
    preheat_lead_cap_minutes = forms.IntegerField(min_value=0, max_value=PREHEAT_LEAD_CAP_MAX_MINUTES)

    @classmethod
    def for_settings(cls, record: ClimateConfigRecord, data: QueryDict | None = None) -> 'ClimateSettingsForm':
        return cls(data, initial=asdict(record.config), auto_id='climate-%s')

    def clean(self) -> dict[str, Any]:
        super().clean()
        start_offset = self.cleaned_data.get('air_start_offset')
        min_band = self.cleaned_data.get('air_min_band')
        if start_offset is not None and min_band is not None and not start_offset_keeps_band(start_offset, min_band):
            self.add_error('air_start_offset', BELOW_MIN_BAND)
        return self.cleaned_data
